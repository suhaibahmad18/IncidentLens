"""Start and execute investigations. Used by the API (background task) and the evaluator."""

import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable

from app.agent.llm import CachedChatClient, ChatClient, GroqChatClient
from app.agent.loop import AgentLimits, Outcome, investigate
from app.config import Settings
from app.db.connection import connect
from app.rag.embeddings import Embedder
from app.runs import store
from app.tools.context import open_tool_context

logger = logging.getLogger("incidentlens.runs")


class InvestigationsUnavailable(Exception):
    """No LLM is configured (missing GROQ_API_KEY)."""


class TooManyRuns(Exception):
    """The concurrent investigation limit is reached."""


def groq_client_factory(settings: Settings) -> Callable[[], ChatClient] | None:
    key = settings.groq_api_key
    if key is None or not key.get_secret_value().strip() or "change-me" in key.get_secret_value():
        return None  # missing or still the .env.example placeholder
    return lambda: GroqChatClient(
        key, settings.groq_model, settings.groq_base_url, settings.llm_timeout_seconds, settings.llm_max_retries
    )


def limits_from(settings: Settings) -> AgentLimits:
    return AgentLimits(
        max_iterations=settings.agent_max_iterations,
        max_tool_calls=settings.agent_max_tool_calls,
        max_tool_result_chars=settings.agent_max_tool_result_chars,
    )


class InvestigationService:
    def __init__(self, settings: Settings, embedder: Embedder,
                 llm_factory: Callable[[], ChatClient] | None):
        self._settings = settings
        self._embedder = embedder
        self._llm_factory = llm_factory
        self._slots = threading.BoundedSemaphore(settings.max_concurrent_runs)
        self._recent_starts: deque[float] = deque()
        self._lock = threading.Lock()

    def _within_hourly_limit(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._recent_starts and now - self._recent_starts[0] > 3600:
                self._recent_starts.popleft()
            if len(self._recent_starts) >= self._settings.max_runs_per_hour:
                return False
            self._recent_starts.append(now)
            return True

    @property
    def available(self) -> bool:
        return self._llm_factory is not None

    def model_name(self) -> str:
        return self._settings.groq_model

    def start(self, scenario_id: int) -> uuid.UUID:
        """Create a pending run and reserve a slot. Call execute() afterwards (e.g. in background)."""
        if self._llm_factory is None:
            raise InvestigationsUnavailable()
        if not self._slots.acquire(blocking=False):
            raise TooManyRuns()
        if not self._within_hourly_limit():
            self._slots.release()
            raise TooManyRuns()
        try:
            with connect(self._settings.database_url, autocommit=True) as conn:
                return store.create_run(conn, scenario_id, self._settings.groq_model)
        except Exception:
            self._slots.release()
            raise

    def execute(self, run_id: uuid.UUID, scenario_slug: str, title: str, alert_summary: str) -> Outcome | None:
        """Run the agent and persist the outcome. Never raises; failures are stored on the run."""
        outcome = None
        try:
            with connect(self._settings.database_url, autocommit=True) as app_conn:
                store.mark_running(app_conn, run_id)
                error = None
                try:
                    llm = self._llm_factory()
                    if self._settings.llm_cache_enabled:
                        llm = CachedChatClient(llm, app_conn)
                    with open_tool_context(self._settings.tools_ro_database_url, scenario_slug, self._embedder) as ctx:
                        outcome = investigate(ctx, title, alert_summary, llm, store.DbTraceSink(app_conn, run_id),
                                              limits_from(self._settings))
                    if outcome.status == "failed":
                        error = f"{outcome.error_code}: {outcome.error_message}"
                except Exception as exc:  # unexpected: store a safe summary, log the type only
                    logger.error("investigation %s crashed: %s", run_id, type(exc).__name__)
                    error = f"internal_error: {type(exc).__name__}"
                store.finish_run(app_conn, run_id, outcome.report if outcome else None, error)
        except Exception as exc:
            logger.error("could not persist run %s: %s", run_id, type(exc).__name__)
        finally:
            self._slots.release()
        return outcome

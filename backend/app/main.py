import logging
import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.api.auth import router as auth_router
from app.api.routes import router
from app.api.user_incidents import router as user_incidents_router
from app.config import Settings, WebSettings, get_settings
from app.db.connection import connect
from app.http_guards import BodySizeLimit, SameOriginWrites, SecurityHeaders
from app.rag.embeddings import get_embedder
from app.ratelimit import KeyedLimiter, SlidingWindowLimiter
from app.runs import store
from app.runs.service import InvestigationService, groq_client_factory, stale_run_seconds

logger = logging.getLogger("incidentlens")


class HealthResponse(BaseModel):
    status: str
    service: str


def create_app(settings: Settings | None = None, investigations: InvestigationService | None = None) -> FastAPI:
    """Settings load at startup (not import), so the app can be created without a database."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved = settings or get_settings()
        app.state.settings = resolved
        app.state.investigations = investigations or InvestigationService(
            resolved, get_embedder(), groq_client_factory(resolved)
        )
        app.state.incident_limiter = SlidingWindowLimiter(resolved.max_incidents_per_hour)
        # In-process limits: they reset on restart and are per instance (one instance is deployed).
        app.state.auth_limits = {
            "login_ip": KeyedLimiter(resolved.login_attempts_per_ip_per_15min, 15 * 60),
            "login_email": KeyedLimiter(resolved.login_failures_per_email_per_15min, 15 * 60),
            "register_ip": KeyedLimiter(resolved.registrations_per_ip_per_hour, 3600),
            "register_all": KeyedLimiter(resolved.registrations_per_hour, 3600),
        }
        app.state.run_limiter = KeyedLimiter(resolved.max_runs_per_user_per_hour, 3600)
        if os.environ.get("DATABASE_ADMIN_URL"):
            # The web process never needs superuser access; release/migrations run as a separate job.
            logger.warning("DATABASE_ADMIN_URL is set in the web process environment; remove it and "
                           "run `python -m app.db.release` as a separate one-off job")
        if resolved.public_demo_mode:
            logger.info("public demo mode: secure session cookies; user incidents are private to their owners")
        if not resolved.cookie_secure:
            logger.info("session cookies are not marked Secure (local HTTP development)")
        try:
            with connect(resolved.database_url, autocommit=True) as conn:
                # Only runs with no progress for longer than any live run can be quiet: a run still
                # executing on another instance (overlapping deploy) is left alone.
                interrupted = store.fail_interrupted_runs(conn, stale_run_seconds(resolved))
            if interrupted:
                logger.warning("marked %d interrupted run(s) as failed", interrupted)
        except psycopg.Error as exc:
            logger.warning("startup run cleanup skipped: %s", type(exc).__name__)
        yield

    app = FastAPI(title="IncidentLens API", lifespan=lifespan)
    web = WebSettings()

    origins = [origin.strip() for origin in web.cors_origins.split(",") if origin.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "DELETE"],
                           allow_headers=["Content-Type", "X-CSRF-Token"], allow_credentials=True)

    # Starlette runs the LAST added middleware outermost: security headers on every response
    # (including 413s and 403s), then the streaming size cap, then the cross-origin write check.
    app.add_middleware(SameOriginWrites, extra_origins=tuple(origins))
    app.add_middleware(BodySizeLimit, max_bytes=web.max_request_bytes)
    app.add_middleware(SecurityHeaders)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI's default response echoes the submitted value ("input"), which could be pasted
        # logs containing secrets. Report only where the problem is and what it is.
        problems = [
            {"loc": [str(part) for part in err.get("loc", ())], "msg": err.get("msg", "invalid"),
             "type": err.get("type", "value_error")}
            for err in exc.errors()[:20]
        ]
        return JSONResponse({"detail": problems}, status_code=422)

    @app.exception_handler(psycopg.OperationalError)
    async def database_unavailable(request: Request, exc: psycopg.OperationalError):
        return JSONResponse({"detail": "database unavailable"}, status_code=503)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logger.error("unhandled %s on %s", type(exc).__name__, request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="incidentlens-api")

    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(user_incidents_router)

    if web.frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=web.frontend_dist, html=True), name="frontend")
    return app


app = create_app()

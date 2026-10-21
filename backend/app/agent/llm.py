"""Chat model clients. The agent depends only on the ChatClient protocol."""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

import psycopg
from psycopg.types.json import Jsonb
from pydantic import SecretStr

ErrorCode = Literal["timeout", "rate_limited", "auth", "connection", "bad_request", "api_error"]

# Safe, fixed messages: provider error bodies are never surfaced (they can echo request data).
ERROR_MESSAGES: dict[str, str] = {
    "timeout": "The language model did not respond in time.",
    "rate_limited": "The language model rate limit was reached; try again in a minute.",
    "auth": "The language model rejected the API credentials.",
    "connection": "Could not reach the language model service.",
    "bad_request": "The language model rejected the request.",
    "api_error": "The language model service returned an error.",
}


class LLMError(Exception):
    def __init__(self, code: ErrorCode, status: int | None = None):
        super().__init__(ERROR_MESSAGES[code])
        self.code = code
        self.status = status


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text from the model; parsed and validated by the agent


@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    cached: bool = field(default=False, compare=False)


class ChatClient(Protocol):
    model: str

    def complete(self, messages: list[dict], tools: list[dict] | None, tool_choice: str) -> LLMResponse: ...


class GroqChatClient:
    """Groq through the OpenAI-compatible SDK. The key stays inside the SDK client."""

    def __init__(self, api_key: SecretStr, model: str, base_url: str, timeout: float, max_retries: int):
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(
            api_key=api_key.get_secret_value(), base_url=base_url, timeout=timeout, max_retries=max_retries
        )

    def complete(self, messages: list[dict], tools: list[dict] | None, tool_choice: str) -> LLMResponse:
        import openai

        params: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": 2000}
        if tools:
            params.update(tools=tools, tool_choice=tool_choice, parallel_tool_calls=True)
        if self.model.startswith("openai/gpt-oss"):
            params["extra_body"] = {"reasoning_effort": "low"}  # fewer tokens under tight rate limits

        error: LLMError | None = None
        try:
            completion = self._client.chat.completions.create(**params)
        except openai.APITimeoutError:
            error = LLMError("timeout")
        except openai.RateLimitError as exc:
            error = LLMError("rate_limited", exc.status_code)
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            error = LLMError("auth", exc.status_code)
        except openai.BadRequestError as exc:
            error = LLMError("bad_request", exc.status_code)
        except openai.APIConnectionError:
            error = LLMError("connection")
        except openai.APIStatusError as exc:
            error = LLMError("api_error", exc.status_code)
        if error is not None:
            raise error  # raised outside the except blocks: no chained provider exception

        choice = completion.choices[0]
        calls = tuple(
            ToolCall(call.id, call.function.name, call.function.arguments or "")
            for call in (choice.message.tool_calls or [])
            if getattr(call, "function", None) is not None
        )
        usage = completion.usage
        return LLMResponse(
            content=choice.message.content,
            tool_calls=calls,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            finish_reason=choice.finish_reason,
        )


class CachedChatClient:
    """Reuses identical past completions from llm_cache (saves quota, makes re-runs repeatable)."""

    def __init__(self, inner: ChatClient, conn: psycopg.Connection):
        self.model = inner.model
        self._inner = inner
        self._conn = conn  # incidentlens_app connection, autocommit

    def complete(self, messages: list[dict], tools: list[dict] | None, tool_choice: str) -> LLMResponse:
        request = {"model": self.model, "messages": messages, "tools": tools, "tool_choice": tool_choice}
        key = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()

        row = self._conn.execute("SELECT response FROM llm_cache WHERE cache_key = %s", (key,)).fetchone()
        if row is not None:
            stored = row[0]
            stored["tool_calls"] = tuple(ToolCall(**call) for call in stored["tool_calls"])
            return LLMResponse(**stored, cached=True)

        response = self._inner.complete(messages, tools, tool_choice)
        stored = asdict(response)
        stored.pop("cached")
        self._conn.execute(
            "INSERT INTO llm_cache (cache_key, model, request, response) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (cache_key) DO NOTHING",
            (key, self.model, Jsonb(request), Jsonb(stored)),
        )
        return response

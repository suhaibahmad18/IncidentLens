"""Scripted language model and trace sink for agent tests (no network, fully repeatable)."""

import copy
import json

from app.agent.llm import LLMError, LLMResponse, ToolCall


def tool_call(name: str, arguments: dict | str, call_id: str | None = None) -> ToolCall:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ToolCall(call_id or f"call_{name}_{abs(hash(raw)) % 10_000}", name, raw)


def calls(*tool_calls: ToolCall) -> LLMResponse:
    return LLMResponse(content=None, tool_calls=tuple(tool_calls), input_tokens=100, output_tokens=20,
                       finish_reason="tool_calls")


def final(report: dict | str) -> LLMResponse:
    content = report if isinstance(report, str) else json.dumps(report)
    return LLMResponse(content=content, input_tokens=200, output_tokens=150, finish_reason="stop")


class ScriptedLLM:
    """Plays back a fixed list of responses (or LLMErrors) and records every request."""

    model = "scripted-test-model"

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []

    def complete(self, messages, tools, tool_choice):
        self.requests.append({"messages": copy.deepcopy(messages), "tools": tools, "tool_choice": tool_choice})
        if not self.script:
            raise AssertionError("ScriptedLLM ran out of responses")
        item = self.script.pop(0)
        if callable(item):
            item = item(messages)
        if isinstance(item, LLMError):
            raise item
        return item


class ListSink:
    def __init__(self):
        self.steps: list[dict] = []

    def record(self, step_type, name, input, output=None, error=None, latency_ms=None,
               input_tokens=None, output_tokens=None):
        self.steps.append({"type": step_type, "name": name, "input": input, "output": output, "error": error})

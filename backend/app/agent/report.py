"""The investigation report: what the model must return, and how it is checked."""

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Citation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    evidence_id: str = Field(max_length=120)
    why: str = Field("", max_length=400)


class Report(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["conclusive", "inconclusive"]
    root_cause: str = Field(max_length=800)
    affected_service: str | None = Field(None, max_length=64)
    confidence: float = Field(ge=0, le=1)
    explanation: str = Field(max_length=3000)
    evidence: list[Citation] = Field(default_factory=list, max_length=15)
    remediation: list[str] = Field(default_factory=list, max_length=8)
    missing_information: list[str] = Field(default_factory=list, max_length=8)


class ReportFormatError(Exception):
    """The model's final answer was not a valid report (message is safe to send back to the model)."""


REPORT_SCHEMA_HINT = """{
  "status": "conclusive" | "inconclusive",
  "root_cause": "one or two sentences: the most likely root cause, or why it cannot be determined",
  "affected_service": "the service where the root cause originates (must be one of the listed services), or null",
  "confidence": 0.0-1.0,
  "explanation": "how the cited evidence supports the conclusion, in timeline order",
  "evidence": [{"evidence_id": "an evidence_id returned by a tool", "why": "what it shows"}],
  "remediation": ["advisory step for a human to approve"],
  "missing_information": ["what else would need checking"]
}"""


def parse_report(content: str | None) -> Report:
    """Extract and validate the JSON report from the model's final message."""
    if not content or not content.strip():
        raise ReportFormatError("the final answer was empty")
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ReportFormatError("the final answer did not contain a JSON object")

    problem = None
    try:
        return Report.model_validate(json.loads(text[start : end + 1]))
    except json.JSONDecodeError as exc:
        problem = f"invalid JSON ({exc.msg} at position {exc.pos})"
    except ValidationError as exc:
        problem = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'report'}: {err['msg']}" for err in exc.errors()[:5]
        )
    raise ReportFormatError(problem)

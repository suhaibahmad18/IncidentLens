"""Evidence ID formats shared by seeding, tools, citations and the API.

Logs and deployments carry stored refs ('log:0007', 'deploy:2'); metric series and runbook
chunks have derived IDs. All IDs are scenario-local and stable across re-seeding.
"""

import re

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "FATAL")

EVIDENCE_ID_PATTERN = re.compile(
    r"^(log:\d{4}|deploy:\d+|metric:[a-z0-9-]+:[a-z0-9_]+|runbook:[a-z0-9.-]+#\d+)$"
)


def metric_evidence_id(service: str, name: str) -> str:
    return f"metric:{service}:{name}"


def runbook_evidence_id(source: str, chunk_index: int) -> str:
    return f"runbook:{source.removeprefix('runbooks/')}#{chunk_index}"


def evidence_kind(evidence_id: str) -> str | None:
    """'log', 'deploy', 'metric' or 'runbook' for a well-formed ID, else None."""
    return evidence_id.split(":", 1)[0] if EVIDENCE_ID_PATTERN.match(evidence_id) else None

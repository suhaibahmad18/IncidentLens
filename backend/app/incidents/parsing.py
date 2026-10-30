"""Parse user-supplied evidence (pasted logs, metrics CSV, deployments CSV) into records.

Strict: inputs over the limits are rejected as a whole (EvidenceLimitError), never silently
truncated. Everything is redacted before parsing. Skipped rows are reported by line number
and reason only, never by content, so nothing a user pasted is echoed back.
"""

import csv
import io
import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.evidence import LEVELS
from app.incidents.redaction import REDACTED, is_secret_key, redact

MAX_LOG_CHARS = 1_000_000
MAX_LOG_LINES = 5_000
MAX_LINE_CHARS = 2_000
MAX_METRICS_CHARS = 300_000
MAX_METRIC_ROWS = 2_000
MAX_DEPLOYMENTS_CHARS = 50_000
MAX_DEPLOYMENT_ROWS = 200
MAX_SERVICES = 32
MAX_ATTRIBUTES = 20
MAX_ATTRIBUTE_CHARS = 200
MAX_REPORTED_PROBLEMS = 20

SERVICE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
METRIC_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")
DEPLOYMENT_STATUSES = ("succeeded", "failed", "rolled_back")

_TIMESTAMP = re.compile(
    r"^\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\]?\s*"
)
_LEVEL_WORD = re.compile(r"\b(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|ERR|CRITICAL|CRIT|SEVERE|FATAL|PANIC)\b")
_LEVEL_KV = re.compile(r"(?i)\blevel=\"?([a-z]+)")
_SERVICE_KV = re.compile(r"(?i)\b(?:service|svc|app)=\"?([A-Za-z0-9._-]{1,64})")
_LEVEL_ALIASES = {
    "TRACE": "DEBUG", "DEBUG": "DEBUG", "INFO": "INFO", "NOTICE": "INFO", "INFORMATION": "INFO",
    "WARN": "WARN", "WARNING": "WARN", "ERROR": "ERROR", "ERR": "ERROR", "CRITICAL": "FATAL",
    "CRIT": "FATAL", "SEVERE": "FATAL", "FATAL": "FATAL", "PANIC": "FATAL", "EMERG": "FATAL", "ALERT": "FATAL",
}
_JSON_TS_KEYS = ("timestamp", "ts", "time", "@timestamp", "datetime")
_JSON_LEVEL_KEYS = ("level", "severity", "lvl", "log.level")
_JSON_SERVICE_KEYS = ("service", "service.name", "svc", "app", "application")
_JSON_MESSAGE_KEYS = ("message", "msg", "log", "event", "text")


class EvidenceLimitError(ValueError):
    """Input exceeds a hard limit; the message names the limit, never the content."""


@dataclass
class ParsedLog:
    ts: datetime | None
    service: str
    level: str
    message: str
    attributes: dict


@dataclass
class ParsedMetric:
    ts: datetime
    service: str
    name: str
    value: float
    unit: str


@dataclass
class ParsedDeployment:
    ts: datetime
    service: str
    version: str
    status: str
    change_summary: str
    deployed_by: str


@dataclass
class ParsedEvidence:
    logs: list[ParsedLog] = field(default_factory=list)
    metrics: list[ParsedMetric] = field(default_factory=list)
    deployments: list[ParsedDeployment] = field(default_factory=list)
    redactions: int = 0
    skipped: list[str] = field(default_factory=list)  # "metrics line 4: invalid value"
    skipped_count: int = 0

    def services(self) -> set[str]:
        return {r.service for r in (*self.logs, *self.metrics, *self.deployments)}

    def skip(self, where: str, line: int, reason: str) -> None:
        self.skipped_count += 1
        if len(self.skipped) < MAX_REPORTED_PROBLEMS:
            self.skipped.append(f"{where} line {line}: {reason}")


def normalize_service(name: str | None) -> str | None:
    if not name:
        return None
    candidate = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")[:64]
    return candidate if SERVICE_PATTERN.match(candidate) else None


def parse_timestamp(value) -> datetime | None:
    """ISO-8601 (naive = UTC) or Unix seconds/milliseconds. Years outside 2000-2100 are rejected."""
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value > 1e11 else value
            ts = datetime.fromtimestamp(seconds, UTC)
        else:
            text = str(value).strip().replace(",", ".")
            if re.search(r"[+-]\d{4}$", text):
                text = f"{text[:-2]}:{text[-2:]}"
            ts = datetime.fromisoformat(text)
            ts = ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    except (ValueError, OverflowError, OSError):
        return None
    return ts.astimezone(UTC) if 2000 <= ts.year <= 2100 else None


def _level(raw: str | None) -> str | None:
    return _LEVEL_ALIASES.get(str(raw).strip().upper()) if raw else None


def _first(record: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record.pop(key)
    return None


def _check_size(text: str, limit: int, label: str) -> None:
    if len(text) > limit:
        raise EvidenceLimitError(f"{label} exceeds {limit:,} characters")


def parse_logs(text: str, default_service: str, result: ParsedEvidence) -> None:
    """Plain-text lines or JSON lines. Indented lines continue the previous entry (stack traces)."""
    if not text.strip():
        return
    _check_size(text, MAX_LOG_CHARS, "logs")
    text, count = redact(text)
    result.redactions += count

    entries: list[ParsedLog] = []
    for number, line in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        if not line.strip():
            continue
        if line[:1] in (" ", "\t") and entries:
            previous = entries[-1]
            previous.message = (previous.message + "\n" + line.rstrip())[:MAX_LINE_CHARS]
            continue
        if len(entries) >= MAX_LOG_LINES:
            raise EvidenceLimitError(f"logs exceed {MAX_LOG_LINES:,} entries")
        entries.append(_parse_log_line(line.strip(), number, default_service, result))
    result.logs.extend(e for e in entries if e is not None)


def _parse_log_line(line: str, number: int, default_service: str, result: ParsedEvidence) -> ParsedLog | None:
    attributes: dict = {"line": number}
    if line.startswith("{"):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = None
        if isinstance(record, dict):
            ts = parse_timestamp(_first(record, _JSON_TS_KEYS))
            level = _level(_first(record, _JSON_LEVEL_KEYS))
            service = normalize_service(str(_first(record, _JSON_SERVICE_KEYS) or "")) or default_service
            message = _first(record, _JSON_MESSAGE_KEYS)
            for key, value in list(record.items())[:MAX_ATTRIBUTES]:
                if isinstance(value, (str, int, float, bool)) or value is None:
                    attributes[str(key)[:64]] = REDACTED if is_secret_key(str(key)) else (
                        value[:MAX_ATTRIBUTE_CHARS] if isinstance(value, str) else value)
            message = str(message) if message is not None else json.dumps(record)[:MAX_LINE_CHARS]
            return _finish(ts, service, level, message, attributes)

    ts = None
    match = _TIMESTAMP.match(line)
    if match:
        ts = parse_timestamp(match.group(1))
        line = line[match.end():] if ts else line
    level_match = _LEVEL_KV.search(line)
    level = _level(level_match.group(1)) if level_match else None
    if level is None:
        word = _LEVEL_WORD.search(line[:120])
        level = _level(word.group(1)) if word else None
    service_match = _SERVICE_KV.search(line)
    service = (normalize_service(service_match.group(1)) if service_match else None) or default_service
    return _finish(ts, service, level, line, attributes)


def _finish(ts, service, level, message, attributes) -> ParsedLog:
    if ts is None:
        attributes["timestamp_inferred"] = True
    if level is None:
        attributes["level_inferred"] = True
    if len(message) > MAX_LINE_CHARS:
        attributes["truncated"] = True
    return ParsedLog(ts, service, level or "INFO", message[:MAX_LINE_CHARS] or "(empty)", attributes)


def _csv_rows(text: str, required: tuple[str, ...], label: str, limit_chars: int, limit_rows: int,
              result: ParsedEvidence):
    _check_size(text, limit_chars, label)
    text, count = redact(text)
    result.redactions += count
    reader = csv.DictReader(io.StringIO(text.strip()))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [column for column in required if column not in header]
    if missing:
        raise EvidenceLimitError(f"{label} CSV needs a header row with: {', '.join(required)}")
    reader.fieldnames = header
    rows = []
    for row in reader:
        if len(rows) >= limit_rows:
            raise EvidenceLimitError(f"{label} exceed {limit_rows:,} rows")
        rows.append((reader.line_num, {k: (v or "").strip() for k, v in row.items() if k}))
    return rows


def parse_metrics_csv(text: str, default_service: str, result: ParsedEvidence) -> None:
    """Header: timestamp,service,metric,value[,unit]. Duplicate points keep the last value."""
    if not text.strip():
        return
    points: dict[tuple, ParsedMetric] = {}
    for line, row in _csv_rows(text, ("timestamp", "metric", "value"), "metrics", MAX_METRICS_CHARS,
                               MAX_METRIC_ROWS, result):
        ts = parse_timestamp(row.get("timestamp"))
        name = re.sub(r"[^a-z0-9_]+", "_", row.get("metric", "").lower()).strip("_")
        try:
            value = float(row.get("value", ""))
        except ValueError:
            value = math.nan
        service = normalize_service(row.get("service")) or default_service
        if ts is None:
            result.skip("metrics", line, "invalid timestamp")
        elif not METRIC_PATTERN.match(name):
            result.skip("metrics", line, "invalid metric name")
        elif not math.isfinite(value):
            result.skip("metrics", line, "invalid value")
        else:
            points[(service, name, ts)] = ParsedMetric(ts, service, name, value, row.get("unit", "")[:32])
    result.metrics.extend(points.values())


def parse_deployments_csv(text: str, default_service: str, result: ParsedEvidence) -> None:
    """Header: timestamp,service,version[,status][,description][,deployed_by]."""
    if not text.strip():
        return
    for line, row in _csv_rows(text, ("timestamp", "version"), "deployments", MAX_DEPLOYMENTS_CHARS,
                               MAX_DEPLOYMENT_ROWS, result):
        ts = parse_timestamp(row.get("timestamp"))
        status = (row.get("status") or "succeeded").lower().replace(" ", "_")
        version = row.get("version", "")[:64]
        if ts is None:
            result.skip("deployments", line, "invalid timestamp")
        elif not version:
            result.skip("deployments", line, "missing version")
        elif status not in DEPLOYMENT_STATUSES:
            result.skip("deployments", line, f"status must be one of {', '.join(DEPLOYMENT_STATUSES)}")
        else:
            result.deployments.append(ParsedDeployment(
                ts, normalize_service(row.get("service")) or default_service, version, status,
                (row.get("description") or row.get("change_summary") or "")[:500],
                row.get("deployed_by", "")[:64],
            ))


def parse_evidence(logs: str, metrics_csv: str, deployments_csv: str, default_service: str) -> ParsedEvidence:
    result = ParsedEvidence()
    parse_logs(logs, default_service, result)
    parse_metrics_csv(metrics_csv, default_service, result)
    parse_deployments_csv(deployments_csv, default_service, result)
    if len(result.services()) > MAX_SERVICES:
        raise EvidenceLimitError(f"evidence mentions more than {MAX_SERVICES} services")
    return result


def infer_incident_start(evidence: ParsedEvidence) -> datetime | None:
    """Earliest WARN-or-worse log line, else the earliest timestamp of any evidence."""
    rank = {level: i for i, level in enumerate(LEVELS)}
    severe = [log.ts for log in evidence.logs if log.ts and rank[log.level] >= rank["WARN"]]
    if severe:
        return min(severe)
    stamps = [log.ts for log in evidence.logs if log.ts] + [m.ts for m in evidence.metrics] + [
        d.ts for d in evidence.deployments]
    return min(stamps) if stamps else None

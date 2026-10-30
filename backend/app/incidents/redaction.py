"""Redact obvious credentials from user-supplied text BEFORE it is parsed or stored.

Pattern-based, so it is a safety net, not a guarantee: it catches common secret shapes
(key=value secrets, bearer/basic auth, credentials in URLs, well-known token formats,
private keys). Only counts are reported; matched values are never returned or logged.
"""

import re

REDACTED = "[REDACTED]"

_SECRET_KEYS = (
    r"password|passwd|pwd|secret|client[_-]?secret|api[_-]?key|apikey|access[_-]?key|"
    r"secret[_-]?key|private[_-]?key|auth[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"session[_-]?token|token|credentials?"
)

# (pattern, replacement). Order matters: whole blocks first, then structured forms.
_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
     REDACTED),
    # Authorization headers: keep the scheme, drop the credential.
    (re.compile(r"(?i)(\b(?:authorization|proxy-authorization)\s*[:=]\s*[\"']?(?:bearer|basic|token)\s+)[^\s\"',;]+"),
     r"\1" + REDACTED),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1" + REDACTED),
    # user:password@host in URLs / connection strings.
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s:/@\"']+:)[^\s@/\"']+(@)"), r"\1" + REDACTED + r"\2"),
    # key=value, key: value, "key": "value" (JSON stays valid: the quotes are kept).
    (re.compile(rf"(?i)(\b(?:{_SECRET_KEYS})\b[\"']?\s*[:=]\s*[\"']?)(?!\[REDACTED\])[^\s\"',;&}}]+"),
     r"\1" + REDACTED),
    # Well-known token formats appearing anywhere.
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), REDACTED),
    (re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"), REDACTED),
    (re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b"), REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), REDACTED),  # JWT
)

SECRET_KEY_NAME = re.compile(rf"(?i)^(?:{_SECRET_KEYS})$|(?:password|secret|token|api[_-]?key)")


def redact(text: str) -> tuple[str, int]:
    """Return (redacted text, number of redactions)."""
    total = 0
    for pattern, replacement in _RULES:
        text, count = pattern.subn(replacement, text)
        total += count
    return text, total


def is_secret_key(name: str) -> bool:
    """For structured (JSON) fields: should this key's value be hidden regardless of shape?"""
    return bool(SECRET_KEY_NAME.search(name))

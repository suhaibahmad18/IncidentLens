"""Offline: credentials are removed before storage. All secrets here are fake test values."""

import json

import pytest

from app.incidents.redaction import REDACTED, is_secret_key, redact

FAKE = "Zx9fakeSecretValue123"


@pytest.mark.parametrize(
    "text",
    [
        f"login failed password={FAKE} user=bob",
        f"config: api_key: {FAKE}",
        f"export AWS_SECRET_ACCESS_KEY... secret_key={FAKE}",
        f'{{"level": "ERROR", "password": "{FAKE}"}}',
        f"Authorization: Bearer {FAKE}",
        f"authorization=Basic {FAKE}",
        f"connecting to postgresql://app:{FAKE}@db.internal:5432/orders",
        f"redis://default:{FAKE}@cache:6379",
        f"retrying with access_token={FAKE}&scope=read",
        f"client_secret = '{FAKE}'",
    ],
    ids=["password-kv", "api-key-colon", "secret-key", "json-password", "bearer", "basic", "postgres-url",
         "redis-url", "access-token-query", "client-secret-quoted"],
)
def test_secret_values_are_removed(text):
    redacted, count = redact(text)

    assert FAKE not in redacted
    assert REDACTED in redacted
    assert count >= 1


@pytest.mark.parametrize(
    "token",
    [
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "ghp_" + "a" * 36,
        "gsk_" + "b" * 40,
        "sk-proj-" + "c" * 30,
        "xoxb-" + "1234567890-abcdef",
        "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0In0" + ".abcdefghijklmnop",
    ],
    ids=["aws-access-key", "github", "groq", "openai", "slack", "jwt"],
)
def test_known_token_formats_are_removed_anywhere(token):
    redacted, count = redact(f"request failed for token {token} at 14:00")

    assert token not in redacted and count == 1


def test_private_key_blocks_are_removed():
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEfakekeymaterial\nmore\n-----END RSA PRIVATE KEY-----"

    redacted, count = redact(f"loaded key:\n{key}\nnext line")

    assert "MIIEfake" not in redacted and count == 1
    assert redacted.endswith("next line")


def test_json_lines_stay_valid_json():
    redacted, _ = redact(json.dumps({"msg": "auth failed", "password": FAKE, "user": "bob"}))

    assert json.loads(redacted) == {"msg": "auth failed", "password": REDACTED, "user": "bob"}


@pytest.mark.parametrize(
    "text",
    [
        "token bucket refilled: 40 tokens available",
        "timeout acquiring connection from pool after 5000ms",
        "GET /api/password-reset 200",
        "deploying v2.14.0 to payments-api",
    ],
)
def test_ordinary_log_text_is_untouched(text):
    assert redact(text) == (text, 0)


def test_redacting_twice_does_not_double_count():
    once, first = redact(f"password={FAKE}")
    twice, second = redact(once)

    assert first == 1 and second == 0 and once == twice


@pytest.mark.parametrize(("key", "secret"), [("password", True), ("db_password", True), ("api_key", True),
                                             ("authToken", True), ("user", False), ("latency_ms", False)])
def test_secret_key_names(key, secret):
    assert is_secret_key(key) is secret

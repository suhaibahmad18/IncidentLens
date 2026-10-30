"""Auth building blocks without a database."""

from app.auth.deps import cookie_name
from app.auth.passwords import hash_password, verify_password
from app.config import Settings
from app.ratelimit import KeyedLimiter


def test_passwords_hash_with_argon2id_and_verify():
    stored = hash_password("correct horse battery staple")
    assert stored.startswith("$argon2id$")
    assert stored != hash_password("correct horse battery staple")  # salted
    assert verify_password(stored, "correct horse battery staple")
    assert not verify_password(stored, "wrong")
    assert not verify_password(None, "anything")  # unknown account: still does the work, says no
    assert not verify_password("not-a-hash", "anything")


def test_keyed_limiter_windows_are_per_key_and_expire():
    now = [0.0]
    limiter = KeyedLimiter(2, 60, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    assert limiter.allow("b")
    now[0] = 61
    assert limiter.allow("a")


def test_keyed_limiter_failures_only_count_when_recorded():
    limiter = KeyedLimiter(2, 60)
    assert not limiter.blocked("x")
    limiter.record("x")
    limiter.record("x")
    assert limiter.blocked("x") and not limiter.blocked("y")


def test_keyed_limiter_memory_is_bounded():
    limiter = KeyedLimiter(1, 60, max_keys=3)
    for key in "abcdef":
        limiter.allow(key)
    assert len(limiter._events) == 3


def settings(**overrides) -> Settings:
    urls = {"database_url": "postgresql://incidentlens_app@localhost/x",
            "tools_ro_database_url": "postgresql://tools_ro@localhost/x"}
    return Settings(_env_file=None, **urls, **overrides)


def test_cookies_are_secure_in_public_mode_unless_overridden():
    assert settings(public_demo_mode=True).cookie_secure is True
    assert settings(public_demo_mode=False).cookie_secure is False
    assert settings(public_demo_mode=False, session_cookie_secure=True).cookie_secure is True
    assert settings(public_demo_mode=True, session_cookie_secure=False).cookie_secure is False
    assert cookie_name(True) == "__Host-incidentlens_session" and cookie_name(False) == "incidentlens_session"

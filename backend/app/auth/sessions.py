"""Server-side sessions. The browser holds a random token in an HttpOnly cookie; the database
stores only its SHA-256 hash, so a database leak does not yield usable sessions."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg


@dataclass(frozen=True)
class AuthUser:
    id: int
    email: str
    csrf_token: str

    def public(self) -> dict:
        return {"id": self.id, "email": self.email}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: psycopg.Connection, user_id: int, ttl_hours: int) -> tuple[str, str]:
    """Returns (session token for the cookie, CSRF token for the client)."""
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at) VALUES (%s, %s, %s, %s)",
        (_hash(token), user_id, csrf, datetime.now(UTC) + timedelta(hours=ttl_hours)),
    )
    return token, csrf


def lookup_session(conn: psycopg.Connection, token: str | None) -> AuthUser | None:
    """The user behind a session token, or None if missing, unknown or expired."""
    if not token or len(token) > 200:
        return None
    row = conn.execute(
        "SELECT u.id, u.email, s.csrf_token FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = %s AND s.expires_at > now()",
        (_hash(token),),
    ).fetchone()
    return AuthUser(*row) if row else None


def delete_session(conn: psycopg.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_hash(token),))


def delete_expired_sessions(conn: psycopg.Connection) -> int:
    return conn.execute("DELETE FROM sessions WHERE expires_at <= now()").rowcount

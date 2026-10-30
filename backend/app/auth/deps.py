"""Request authentication and CSRF enforcement.

Every request may carry a session cookie. If it does and the method changes state (POST, PUT,
PATCH, DELETE), the X-CSRF-Token header must equal the session's CSRF token; otherwise 403.
An unknown or expired cookie is treated as anonymous (endpoints that need a user return 401).
"""

import hmac

from fastapi import HTTPException, Request, status

from app.auth.sessions import AuthUser, lookup_session
from app.db.connection import connect

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "x-csrf-token"


def cookie_name(secure: bool) -> str:
    # The __Host- prefix makes browsers refuse the cookie unless it is Secure, host-only and
    # Path=/; it can only be used over HTTPS, so local HTTP development uses a plain name.
    return "__Host-incidentlens_session" if secure else "incidentlens_session"


def session_token(request: Request) -> str | None:
    return request.cookies.get(cookie_name(request.app.state.settings.cookie_secure))


def current_user(request: Request) -> AuthUser | None:
    """The authenticated user or None. Enforces CSRF for state-changing requests with a session."""
    if hasattr(request.state, "auth_user"):
        return request.state.auth_user
    token = session_token(request)
    user = None
    if token:
        with connect(request.app.state.settings.database_url) as conn:
            user = lookup_session(conn, token)
    if user and request.method in UNSAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), user.csrf_token.encode()):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "missing or invalid CSRF token")
    request.state.auth_user = user
    return user


def require_user(request: Request) -> AuthUser:
    user = current_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login required")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

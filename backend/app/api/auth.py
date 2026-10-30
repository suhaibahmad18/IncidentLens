"""Registration, login, logout and the current-session endpoint.

Cookie: HttpOnly, SameSite=Lax, Path=/, Secure (and __Host- prefixed) in public deployments.
The CSRF token is returned in the JSON body; the frontend sends it back as X-CSRF-Token.
"""

import re

from fastapi import APIRouter, HTTPException, Request, Response, status
from psycopg import errors
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.deps import client_ip, cookie_name, current_user, require_user, session_token
from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import AuthUser, create_session, delete_expired_sessions, delete_session
from app.db.connection import connect

router = APIRouter(prefix="/api/auth")

EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
INVALID_LOGIN = "invalid email or password"
MIN_PASSWORD, MAX_PASSWORD = 12, 128


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        email = value.strip().lower()
        if not EMAIL_PATTERN.match(email):
            raise ValueError("enter a valid email address")
        return email


class Registration(Credentials):
    @field_validator("password")
    @classmethod
    def _password_policy(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD:
            raise ValueError(f"use at least {MIN_PASSWORD} characters")
        if len(set(value)) < 4:
            raise ValueError("use a less repetitive password")
        return value


def _session_response(request: Request, response: Response, user_id: int, email: str) -> dict:
    settings = request.app.state.settings
    with connect(settings.database_url, autocommit=True) as conn:
        delete_expired_sessions(conn)
        token, csrf = create_session(conn, user_id, settings.session_ttl_hours)
    response.set_cookie(
        cookie_name(settings.cookie_secure), token,
        max_age=settings.session_ttl_hours * 3600, httponly=True, secure=settings.cookie_secure,
        samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"user": {"id": user_id, "email": email}, "csrf_token": csrf}


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(request: Request, response: Response, body: Registration):
    settings = request.app.state.settings
    limits = request.app.state.auth_limits
    if not settings.registration_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "registration is closed on this deployment")
    if not (limits["register_ip"].allow(client_ip(request)) and limits["register_all"].allow("all")):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many registrations; try again later")
    if body.password.lower() == body.email:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "the password must not be your email")

    password_hash = hash_password(body.password)
    try:
        with connect(settings.database_url, autocommit=True) as conn:
            user_id = conn.execute(
                "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                (body.email, password_hash),
            ).fetchone()[0]
    except errors.UniqueViolation:
        # Deliberately vague: do not confirm which emails have accounts.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "could not create an account with these details; if you already have one, log in")
    return _session_response(request, response, user_id, body.email)


@router.post("/login")
def login(request: Request, response: Response, body: Credentials):
    settings = request.app.state.settings
    limits = request.app.state.auth_limits
    if not limits["login_ip"].allow(client_ip(request)) or limits["login_email"].blocked(body.email):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many login attempts; try again in 15 minutes")

    with connect(settings.database_url) as conn:
        row = conn.execute("SELECT id, password_hash FROM users WHERE email = %s", (body.email,)).fetchone()
    # Always verify (against a dummy hash for unknown emails) so timing does not reveal accounts.
    if not verify_password(row[1] if row else None, body.password):
        limits["login_email"].record(body.email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_LOGIN)
    return _session_response(request, response, row[0], body.email)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response):
    current_user(request)  # enforces CSRF when a session cookie is present
    settings = request.app.state.settings
    with connect(settings.database_url, autocommit=True) as conn:
        delete_session(conn, session_token(request))
    response.delete_cookie(cookie_name(settings.cookie_secure), path="/", secure=settings.cookie_secure,
                           httponly=True, samesite="lax")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me")
def me(request: Request, response: Response):
    user: AuthUser = require_user(request)
    response.headers["Cache-Control"] = "no-store"
    return {"user": user.public(), "csrf_token": user.csrf_token}

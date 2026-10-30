"""Sign test clients in the way the browser does: register (or log in), keep the session
cookie, and send the CSRF token back on every request."""

import uuid

from fastapi.testclient import TestClient

PASSWORD = "correct horse battery staple"


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}@example.com"


def sign_in(client: TestClient, response) -> dict:
    assert response.status_code in (200, 201), response.text
    body = response.json()
    client.headers["X-CSRF-Token"] = body["csrf_token"]
    return body["user"]


def register(client: TestClient, email: str | None = None, password: str = PASSWORD) -> dict:
    """Create an account and sign the client in. Returns {"id", "email"}."""
    response = client.post("/api/auth/register", json={"email": email or unique_email(), "password": password})
    return sign_in(client, response)


def login(client: TestClient, email: str, password: str = PASSWORD) -> dict:
    return sign_in(client, client.post("/api/auth/login", json={"email": email, "password": password}))


class SignedInClient(TestClient):
    """A TestClient that registers a fresh account as soon as the app has started."""

    def __enter__(self):
        client = super().__enter__()
        self.user = register(client)
        return client

"""Accounts and sessions through the real API and the throwaway test database.
Passwords here are test values only."""

import hashlib

import pytest

from tests.auth_helpers import PASSWORD, login, register, unique_email
from tests.db.test_api import make_client
from tests.db.test_user_incidents import new_incident

pytestmark = pytest.mark.db

COOKIE = "incidentlens_session"


@pytest.fixture
def client(test_db):
    with make_client(test_db) as c:
        yield c


def set_cookie(response) -> str:
    return response.headers.get("set-cookie", "")


def test_register_signs_the_user_in_with_a_hardened_cookie(client):
    email = unique_email()
    response = client.post("/api/auth/register", json={"email": email, "password": PASSWORD})

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == email and isinstance(body["user"]["id"], int)
    assert len(body["csrf_token"]) >= 32
    assert PASSWORD not in response.text
    cookie = set_cookie(response).lower()
    assert cookie.startswith(f"{COOKIE}=") and "httponly" in cookie and "samesite=lax" in cookie
    assert "path=/" in cookie and "max-age=" in cookie
    assert "secure" not in cookie  # local HTTP development
    assert response.headers["cache-control"] == "no-store"


def test_public_deployments_use_secure_host_prefixed_cookies(test_db):
    with make_client(test_db, public_demo_mode=True) as public:
        response = public.post("/api/auth/register", json={"email": unique_email(), "password": PASSWORD})
    cookie = set_cookie(response)
    assert cookie.startswith("__Host-incidentlens_session=") and "secure" in cookie.lower()
    assert "domain=" not in cookie.lower()  # host-only, as the __Host- prefix requires


def test_secure_cookie_can_be_forced_in_local_mode(test_db):
    with make_client(test_db, session_cookie_secure=True) as c:
        response = c.post("/api/auth/register", json={"email": unique_email(), "password": PASSWORD})
    assert "secure" in set_cookie(response).lower()


def test_passwords_are_stored_only_as_argon2id_hashes(client, admin_conn):
    user = register(client)
    stored = admin_conn.execute("SELECT password_hash FROM users WHERE id = %s", (user["id"],)).fetchone()[0]

    assert stored.startswith("$argon2id$") and PASSWORD not in stored


def test_session_tokens_are_stored_only_as_hashes(client, admin_conn):
    user = register(client)
    token = client.cookies.get(COOKIE)
    stored = [row[0] for row in admin_conn.execute("SELECT token_hash FROM sessions WHERE user_id = %s",
                                                    (user["id"],))]

    assert stored == [hashlib.sha256(token.encode()).hexdigest()] and token not in stored


def test_emails_are_case_insensitive_and_duplicates_are_refused_vaguely(client, test_db):
    email = unique_email()
    register(client, email.upper())
    with make_client(test_db) as other:
        duplicate = other.post("/api/auth/register", json={"email": email, "password": PASSWORD + "x"})
        assert duplicate.status_code == 400
        assert "already" not in duplicate.json()["detail"].split(";")[0]  # no "this email is taken"
        assert login(other, email.title())["email"] == email


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"email": "not-an-email", "password": PASSWORD}, "valid email"),
        ({"email": "short@example.com", "password": "Sh0rt!"}, "at least 12"),
        ({"email": "repeat@example.com", "password": "aaaaaaaaaaaaaaab"}, "less repetitive"),
        ({"email": "extra@example.com", "password": PASSWORD, "is_admin": True}, "Extra inputs"),
        ({"email": "long@example.com", "password": "x1" * 65}, "at most 128"),
    ],
)
def test_registration_validation_never_echoes_the_password(client, payload, fragment):
    response = client.post("/api/auth/register", json=payload)

    assert response.status_code == 422
    assert fragment in response.text
    assert payload["password"] not in response.text


def test_password_equal_to_email_is_refused(client):
    email = unique_email()
    response = client.post("/api/auth/register", json={"email": email, "password": email.upper()})
    assert response.status_code == 422 and "must not be your email" in response.json()["detail"]


def test_login_failures_do_not_reveal_whether_the_account_exists(client):
    email = unique_email()
    register(client, email)
    with_wrong_password = client.post("/api/auth/login", json={"email": email, "password": "wrong password!"})
    unknown_account = client.post("/api/auth/login", json={"email": unique_email(), "password": PASSWORD})

    for response in (with_wrong_password, unknown_account):
        assert (response.status_code, response.json()) == (401, {"detail": "invalid email or password"})


def test_me_login_and_logout_round_trip(client):
    assert client.get("/api/auth/me").status_code == 401
    email = unique_email()
    user = register(client, email)

    me = client.get("/api/auth/me")
    assert me.json()["user"] == user and me.json()["csrf_token"] == client.headers["X-CSRF-Token"]
    assert me.headers["cache-control"] == "no-store"

    token = client.cookies.get(COOKIE)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401

    # The old token is dead server-side, not merely forgotten by the browser.
    client.cookies.set(COOKIE, token)
    assert client.get("/api/auth/me").status_code == 401

    assert login(client, email) == user
    assert client.get("/api/auth/me").status_code == 200


def test_logging_out_one_session_keeps_the_others(client, test_db):
    email = unique_email()
    register(client, email)
    with make_client(test_db) as second_device:
        login(second_device, email)
        assert second_device.post("/api/auth/logout").status_code == 204
        assert second_device.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me").status_code == 200


def test_expired_sessions_are_rejected_and_cleaned_up(client, admin_conn):
    user = register(client)
    admin_conn.execute("UPDATE sessions SET created_at = now() - interval '9 days',"
                       " expires_at = now() - interval '1 day' WHERE user_id = %s", (user["id"],))
    admin_conn.commit()

    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/incidents", json=new_incident()).status_code == 401

    register(client)  # any new session also purges expired ones
    assert admin_conn.execute("SELECT count(*) FROM sessions WHERE user_id = %s", (user["id"],)).fetchone()[0] == 0


@pytest.mark.parametrize("cookie", ["forged-token", "x" * 500, ""])
def test_unknown_or_tampered_cookies_are_anonymous(client, cookie):
    client.cookies.set(COOKIE, cookie)
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/incidents").status_code == 200  # demos still readable


def test_state_changes_need_the_csrf_token(client):
    register(client)
    token = client.headers.pop("X-CSRF-Token")

    for headers in ({}, {"X-CSRF-Token": "wrong"}):
        for method, path, body in [("POST", "/api/incidents", new_incident()), ("POST", "/api/auth/logout", None)]:
            response = client.request(method, path, json=body, headers=headers)
            assert (response.status_code, response.json()) == (403, {"detail": "missing or invalid CSRF token"})

    assert client.get("/api/auth/me").status_code == 200  # the failed logout did nothing
    assert client.post("/api/incidents", json=new_incident(), headers={"X-CSRF-Token": token}).status_code == 201


def test_cross_origin_writes_are_refused_before_anything_runs(client):
    email = unique_email()
    for path, body in [("/api/auth/register", {"email": email, "password": PASSWORD}),
                       ("/api/auth/login", {"email": email, "password": PASSWORD}),
                       ("/api/incidents/checkout-db-pool-exhaustion/investigations", None)]:
        response = client.post(path, json=body, headers={"Origin": "https://evil.example"})
        assert (response.status_code, response.json()) == (403, {"detail": "cross-origin request refused"})
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 401  # never created

    same_origin = client.post("/api/auth/register", json={"email": email, "password": PASSWORD},
                              headers={"Origin": "http://testserver"})
    assert same_origin.status_code == 201


def test_repeated_login_failures_lock_the_email_temporarily(test_db):
    with make_client(test_db, login_failures_per_email_per_15min=3) as c:
        email = unique_email()
        register(c, email)
        for _ in range(3):
            assert c.post("/api/auth/login", json={"email": email, "password": "wrong password!"}).status_code == 401
        locked = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        assert locked.status_code == 429  # even the right password, until the window passes
        other = register(c)["email"]
        assert login(c, other)["email"] == other  # other accounts are unaffected


def test_login_attempts_are_limited_per_ip(test_db):
    with make_client(test_db, login_attempts_per_ip_per_15min=2) as c:
        for _ in range(2):
            assert c.post("/api/auth/login", json={"email": unique_email(), "password": PASSWORD}).status_code == 401
        assert c.post("/api/auth/login", json={"email": unique_email(), "password": PASSWORD}).status_code == 429


def test_registrations_are_limited_per_ip(test_db):
    with make_client(test_db, registrations_per_ip_per_hour=2) as c:
        for _ in range(2):
            register(c)
        refused = c.post("/api/auth/register", json={"email": unique_email(), "password": PASSWORD})
        assert refused.status_code == 429


def test_registration_can_be_closed(test_db):
    with make_client(test_db, registration_enabled=False) as c:
        refused = c.post("/api/auth/register", json={"email": unique_email(), "password": PASSWORD})
        assert refused.status_code == 403 and "closed" in refused.json()["detail"]
        assert c.get("/api/config").json()["registration_enabled"] is False


def test_endpoints_that_need_an_account_say_so(client):
    for method, path, body in [("POST", "/api/incidents", new_incident()),
                               ("POST", "/api/incidents/u-0000000000000000/evidence", {"logs": ""}),
                               ("DELETE", "/api/incidents/u-0000000000000000", None),
                               ("GET", "/api/auth/me", None)]:
        response = client.request(method, path, json=body)
        assert (response.status_code, response.json()) == (401, {"detail": "login required"}), path

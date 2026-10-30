"""v1 hardening: privacy of user incidents in public deployments, streaming body limits, storage limits, retention,
LLM-cache behaviour and security headers. All against the throwaway test database."""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.db.connection import connect
from app.db.prune import prune
from app.incidents import store
from app.incidents.parsing import parse_evidence
from app.auth.passwords import hash_password
from tests.auth_helpers import login, unique_email
from tests.db.test_agent import GATHER, report_citing_seen
from tests.db.test_api import make_client
from tests.db.test_user_incidents import new_incident
from tests.llm_fakes import ScriptedLLM

pytestmark = pytest.mark.db

DEMO = "checkout-db-pool-exhaustion"


def create(client, **overrides) -> str:
    response = client.post("/api/incidents", json=new_incident(incident_start="2026-08-20T14:00:00Z", **overrides))
    assert response.status_code == 201, response.json()
    return response.json()["slug"]


@pytest.fixture
def user_incident_with_run(test_db):
    """A signed-in account's incident with one completed run, created in local mode."""
    with make_client(test_db, [GATHER, report_citing_seen], signed_in=True) as client:
        slug = create(client)
        run_id = client.post(f"/api/incidents/{slug}/investigations").json()["run_id"]
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"
        email = client.user["email"]
    return slug, run_id, email


# --- Public deployments: user incidents are private to their owner, demos stay public ---


def test_public_mode_hides_user_incidents_from_visitors(test_db, user_incident_with_run):
    slug, run_id, _ = user_incident_with_run
    missing_incident = {"detail": "incident not found"}
    missing_run = {"detail": "run not found"}

    with make_client(test_db, public_demo_mode=True) as public:
        listed = public.get("/api/incidents").json()
        assert listed and all(i["kind"] == "demo" for i in listed)
        assert slug not in json.dumps(listed)

        for path in (f"/api/incidents/{slug}", f"/api/incidents/{slug}/runs"):
            response = public.get(path)
            # Identical to an incident that never existed: no existence oracle.
            assert (response.status_code, response.json()) == (404, missing_incident), path
        response = public.post(f"/api/incidents/{slug}/investigations")
        assert (response.status_code, response.json()) == (404, missing_incident)

        for path in (f"/api/runs/{run_id}", f"/api/runs/{run_id}/trace", f"/api/runs/{run_id}/evidence"):
            response = public.get(path)
            assert (response.status_code, response.json()) == (404, missing_run), path

        # Writing anything requires an account.
        for method, path, body in [
            ("post", "/api/incidents", new_incident()),
            ("post", f"/api/incidents/{slug}/evidence", {"logs": "2026-08-20T14:00:00Z ERROR x"}),
            ("delete", f"/api/incidents/{slug}", None),
        ]:
            response = public.request(method.upper(), path, **({"json": body} if body else {}))
            assert (response.status_code, response.json()) == (401, {"detail": "login required"}), path

        config = public.get("/api/config").json()
        assert config["public_demo_mode"] is True and config["user_incidents_enabled"] is True


def test_public_mode_still_serves_demos(test_db):
    with make_client(test_db, [GATHER, report_citing_seen], public_demo_mode=True) as public:
        assert public.get(f"/api/incidents/{DEMO}").status_code == 200
        run_id = public.post(f"/api/incidents/{DEMO}/investigations").json()["run_id"]
        assert public.get(f"/api/runs/{run_id}").json()["status"] == "completed"
        assert public.get(f"/api/runs/{run_id}/trace").status_code == 200
        assert public.get(f"/api/runs/{run_id}/evidence").status_code == 200
        assert public.post(f"/api/incidents/{DEMO}/evidence", json={"logs": "x"}).status_code == 401


def test_public_mode_owner_keeps_full_access_over_secure_cookies(test_db):
    with make_client(test_db, [GATHER, report_citing_seen], public_demo_mode=True, signed_in=True) as owner:
        cookie = next(c for c in owner.cookies.jar)
        assert cookie.name == "__Host-incidentlens_session" and cookie.secure
        slug = create(owner)
        assert slug in json.dumps(owner.get("/api/incidents").json())
        assert owner.get(f"/api/incidents/{slug}").status_code == 200
        run_id = owner.post(f"/api/incidents/{slug}/investigations").json()["run_id"]
        assert owner.get(f"/api/runs/{run_id}").json()["status"] == "completed"
        assert owner.post(f"/api/incidents/{DEMO}/evidence", json={"logs": "x"}).status_code == 403


def test_owner_regains_access_after_logging_in_again(test_db, user_incident_with_run):
    slug, run_id, email = user_incident_with_run
    with make_client(test_db) as later:
        assert later.get(f"/api/incidents/{slug}").status_code == 404
        login(later, email)
        assert later.get(f"/api/incidents/{slug}").status_code == 200
        assert later.get(f"/api/runs/{run_id}").status_code == 200


# --- M2: body size is enforced while streaming (no Content-Length) ---


@pytest.fixture
def small_body_client(test_db, monkeypatch):
    monkeypatch.setenv("MAX_REQUEST_BYTES", "20000")
    with make_client(test_db, signed_in=True) as client:
        yield client


def chunks(payload: bytes, size: int = 4096):
    for start in range(0, len(payload), size):
        yield payload[start:start + size]


def test_chunked_upload_over_the_limit_is_refused(small_body_client):
    payload = json.dumps(new_incident(logs="x" * 50_000)).encode()

    response = small_body_client.post("/api/incidents", content=chunks(payload),
                                      headers={"content-type": "application/json"})

    assert response.status_code == 413
    assert response.json() == {"detail": "request body too large"}


def test_chunked_upload_within_the_limit_works(small_body_client):
    payload = json.dumps(new_incident(incident_start="2026-08-20T14:00:00Z")).encode()

    response = small_body_client.post("/api/incidents", content=chunks(payload, 512),
                                      headers={"content-type": "application/json"})

    assert response.status_code == 201


def test_declared_oversized_body_is_refused_before_reading(small_body_client):
    response = small_body_client.post("/api/incidents", content=b" " * 20_001,
                                      headers={"content-type": "application/json"})

    assert response.status_code == 413


# --- H2: storage limits ---


def test_per_incident_evidence_limit_is_configurable(test_db):
    with make_client(test_db, max_incident_log_entries=3, signed_in=True) as client:
        response = client.post("/api/incidents", json=new_incident())  # 5 log lines
        assert response.status_code == 422 and "at most 3 log entries" in response.json()["detail"]

        slug = create(client, logs="2026-08-20T14:00:00Z ERROR one\n2026-08-20T14:00:01Z ERROR two")
        more = client.post(f"/api/incidents/{slug}/evidence",
                           json={"logs": "2026-08-20T14:00:02Z ERROR three\n2026-08-20T14:00:03Z ERROR four",
                                 "default_service": "orders-api"})
        assert more.status_code == 422 and "at most 3 log entries" in more.json()["detail"]


def test_total_user_incident_cap(test_db, admin_conn):
    existing = admin_conn.execute("SELECT count(*) FROM scenarios WHERE kind = 'user'").fetchone()[0]
    with make_client(test_db, max_user_incidents=existing + 1, signed_in=True) as client:
        assert client.post("/api/incidents", json=new_incident()).status_code == 201
        refused = client.post("/api/incidents", json=new_incident())
    assert refused.status_code == 429 and "limit has been reached" in refused.json()["detail"]


def test_concurrent_creations_cannot_exceed_the_cap(test_db, admin_conn):
    existing = admin_conn.execute("SELECT count(*) FROM scenarios WHERE kind = 'user'").fetchone()[0]
    owner = admin_conn.execute("INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id",
                               (unique_email("race"), hash_password("race password 123"))).fetchone()[0]
    admin_conn.commit()
    evidence = parse_evidence("", "", "", "web")
    barrier = threading.Barrier(6)

    def attempt(_):
        with connect(test_db["app"]) as conn:
            barrier.wait()
            try:
                store.create_incident(conn, "Race", "race", datetime.now(UTC), evidence,
                                      max_user_incidents=existing + 2, owner_id=owner)
                return "created"
            except store.IncidentLimitReached:
                return "refused"

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(attempt, range(6)))

    assert results.count("created") == 2 and results.count("refused") == 4


# --- H2: retention / pruning ---


def test_prune_dry_run_then_execute(test_db, admin_conn, user_incident_with_run):
    old_slug, old_run, _ = user_incident_with_run
    with make_client(test_db, signed_in=True) as client:
        recent_slug = create(client)
    admin_conn.execute("UPDATE scenarios SET created_at = now() - interval '40 days' WHERE slug = %s", (old_slug,))
    admin_conn.execute("UPDATE scenarios SET created_at = now() - interval '400 days' WHERE slug = %s", (DEMO,))
    admin_conn.execute(
        "INSERT INTO llm_cache (cache_key, model, request, response, created_at) VALUES"
        " (repeat('a', 64), 'm', '{}', '{}', now() - interval '40 days'),"
        " (repeat('b', 64), 'm', '{}', '{}', now())"
        " ON CONFLICT (cache_key) DO UPDATE SET created_at = EXCLUDED.created_at"
    )
    admin_conn.commit()
    demo_logs_before = admin_conn.execute(
        "SELECT count(*) FROM logs l JOIN scenarios s ON s.id = l.scenario_id WHERE s.kind = 'demo'"
    ).fetchone()[0]

    dry = prune(test_db["admin"], older_than_days=30, execute=False)
    assert dry["database"] == "incidentlens_test" and dry["executed"] is False
    assert old_slug in dry["slugs"] and recent_slug not in dry["slugs"] and DEMO not in dry["slugs"]
    assert dry["runs"] >= 1 and dry["logs"] == 5 and dry["llm_cache_entries"] >= 1
    assert admin_conn.execute("SELECT count(*) FROM scenarios WHERE slug = %s", (old_slug,)).fetchone()[0] == 1

    done = prune(test_db["admin"], older_than_days=30, execute=True)
    assert done["executed"] is True and old_slug in done["slugs"]

    def count(sql, *params):
        return admin_conn.execute(sql, params).fetchone()[0]

    assert count("SELECT count(*) FROM scenarios WHERE slug = %s", old_slug) == 0
    assert count("SELECT count(*) FROM runs WHERE id = %s", old_run) == 0
    assert count("SELECT count(*) FROM trace_steps WHERE run_id = %s", old_run) == 0
    assert count("SELECT count(*) FROM scenarios WHERE slug = %s", recent_slug) == 1  # newer: kept
    assert count("SELECT count(*) FROM scenarios WHERE slug = %s", DEMO) == 1  # demos: never deleted
    assert count("SELECT count(*) FROM logs l JOIN scenarios s ON s.id = l.scenario_id"
                 " WHERE s.kind = 'demo'") == demo_logs_before
    assert count("SELECT count(*) FROM llm_cache WHERE cache_key = repeat('a', 64)") == 0
    assert count("SELECT count(*) FROM llm_cache WHERE cache_key = repeat('b', 64)") == 1


# --- M3: LLM cache ---


def test_llm_cache_is_off_by_default():
    settings = Settings(_env_file=None,
                        database_url="postgresql://incidentlens_app:x@127.0.0.1/db",
                        tools_ro_database_url="postgresql://tools_ro:x@127.0.0.1/db")

    assert settings.llm_cache_enabled is False


def test_default_runs_write_nothing_to_the_cache(test_db, admin_conn):
    before = admin_conn.execute("SELECT count(*) FROM llm_cache").fetchone()[0]
    admin_conn.rollback()
    with make_client(test_db, [GATHER, report_citing_seen]) as client:
        run_id = client.post(f"/api/incidents/{DEMO}/investigations").json()["run_id"]
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"

    assert admin_conn.execute("SELECT count(*) FROM llm_cache").fetchone()[0] == before


def test_cached_answers_never_go_stale_when_evidence_changes(test_db):
    calls = {"model": 0}

    class Counting(ScriptedLLM):
        def complete(self, messages, tools, tool_choice):
            calls["model"] += 1
            return super().complete(messages, tools, tool_choice)

    with make_client(test_db, llm_cache_enabled=True, signed_in=True) as client:
        client.app.state.investigations._llm_factory = lambda: Counting([GATHER, report_citing_seen])
        slug = create(client)

        def investigate():
            run_id = client.post(f"/api/incidents/{slug}/investigations").json()["run_id"]
            run = client.get(f"/api/runs/{run_id}").json()
            assert run["status"] == "completed", run["error"]
            return run

        investigate()
        first = calls["model"]
        investigate()  # identical evidence: fully served from the cache
        assert calls["model"] == first

        client.post(f"/api/incidents/{slug}/evidence", json={
            "logs": "2026-08-20T14:01:00Z ERROR service=orders-api redis pool exhausted",
            "default_service": "orders-api"})
        investigate()  # the tool output now differs, so the cached answer cannot be reused
        assert calls["model"] > first


def test_cache_is_never_shared_between_incidents(test_db):
    calls = {"model": 0}

    class Counting(ScriptedLLM):
        def complete(self, messages, tools, tool_choice):
            calls["model"] += 1
            return super().complete(messages, tools, tool_choice)

    with make_client(test_db, llm_cache_enabled=True, signed_in=True) as client:
        client.app.state.investigations._llm_factory = lambda: Counting([GATHER, report_citing_seen])
        first_slug, second_slug = create(client), create(client)  # byte-identical content
        for slug in (first_slug, second_slug):
            run_id = client.post(f"/api/incidents/{slug}/investigations").json()["run_id"]
            assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"

    assert calls["model"] == 4  # 2 model calls per run; the second incident got no cache hits


# --- L6 / M4: headers and startup warning ---


def test_security_headers(test_db):
    with make_client(test_db) as client:
        api = client.get("/api/health")
        docs = client.get("/docs")

    assert api.headers["x-content-type-options"] == "nosniff"
    assert api.headers["x-frame-options"] == "DENY"
    assert api.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in api.headers["content-security-policy"]
    assert "strict-transport-security" not in api.headers  # only over HTTPS
    assert "content-security-policy" not in docs.headers  # Swagger UI loads assets from a CDN


def test_errors_also_carry_security_headers(test_db):
    with make_client(test_db) as client:
        response = client.get("/api/incidents/no-such-thing")

    assert response.status_code == 404 and response.headers["x-content-type-options"] == "nosniff"


def test_admin_url_in_web_environment_triggers_a_warning(test_db, monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_ADMIN_URL", "postgresql://postgres:fake-secret-0000@127.0.0.1/x")
    with caplog.at_level(logging.WARNING, logger="incidentlens"):
        with make_client(test_db):
            pass

    assert "DATABASE_ADMIN_URL is set in the web process environment" in caplog.text
    assert "fake-secret-0000" not in caplog.text

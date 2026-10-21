"""HTTP API against the test database, with a scripted model injected."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.runs.service import InvestigationService
from app.seed_data.ground_truth import GROUND_TRUTH
from tests.db.test_agent import GATHER, report_citing_seen
from tests.fakes import FakeEmbedder
from tests.llm_fakes import ScriptedLLM, final

pytestmark = pytest.mark.db

SLUG = "checkout-db-pool-exhaustion"


def make_client(test_db, script=None, enabled=True, max_runs=2):
    settings = Settings(
        _env_file=None,
        database_url=test_db["app"],
        tools_ro_database_url=test_db["tools_ro"],
        llm_cache_enabled=False,
        max_concurrent_runs=max_runs,
    )
    llm_factory = (lambda: ScriptedLLM(list(script or []))) if enabled else None
    service = InvestigationService(settings, FakeEmbedder(), llm_factory)
    return TestClient(create_app(settings=settings, investigations=service))


@pytest.fixture
def client(test_db):
    with make_client(test_db, [GATHER, report_citing_seen]) as c:
        yield c


def assert_no_ground_truth(payload) -> None:
    text = json.dumps(payload).lower()
    for truth in GROUND_TRUTH.values():
        assert truth["root_cause"].lower() not in text
    for marker in ("expected_root_cause", "ground_truth", "holdout", "key_evidence", "root_cause_keywords"):
        assert marker not in text


def test_health_endpoints(client):
    assert client.get("/api/health").json() == {"status": "ok", "service": "incidentlens-api"}
    assert client.get("/api/health/ready").json()["database"] == "ok"
    assert client.get("/api/config").json()["investigations_enabled"] is True


def test_list_incidents(client):
    incidents = client.get("/api/incidents").json()

    assert {i["slug"] for i in incidents} == set(GROUND_TRUTH)
    assert set(incidents[0]) == {"slug", "title", "alert_summary", "incident_start", "last_run_status"}
    assert_no_ground_truth(incidents)


def test_incident_detail_shows_telemetry_but_no_answers(client):
    detail = client.get(f"/api/incidents/{SLUG}").json()

    assert detail["slug"] == SLUG
    assert any(d["version"] == "v2.14.0" for d in detail["deployments"])
    assert detail["logs"]["entries"] and all(e["level"] != "INFO" for e in detail["logs"]["entries"])
    assert any(m["evidence_id"] == "metric:payments-db:db_connections_in_use" for m in detail["metrics"])
    assert_no_ground_truth(detail)


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/api/incidents/no-such-incident", 404),
        ("/api/incidents/Bad_Slug!", 422),
        (f"/api/runs/{uuid.uuid4()}", 404),
        ("/api/runs/not-a-uuid", 422),
        (f"/api/runs/{uuid.uuid4()}/trace", 404),
    ],
)
def test_validation_and_not_found(client, path, code):
    response = client.get(path)

    assert response.status_code == code
    assert "detail" in response.json()


def test_full_investigation_flow(client):
    started = client.post(f"/api/incidents/{SLUG}/investigations")
    assert started.status_code == 202
    run_id = started.json()["run_id"]

    # TestClient runs background tasks before returning, so the run has finished here.
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["status"] == "completed", run["error"]
    assert run["report"]["status"] == "conclusive"
    assert run["report"]["affected_service"] == "payments-api"
    assert run["tool_calls"] == 3

    trace = client.get(f"/api/runs/{run_id}/trace").json()
    assert [s["type"] for s in trace] == ["llm_call", "tool_call", "tool_call", "retrieval", "llm_call", "final"]
    assert trace[1]["name"] == "get_recent_deployments" and trace[1]["output"]["ok"] is True

    evidence = client.get(f"/api/runs/{run_id}/evidence").json()
    assert evidence and all(item["record"] is not None for item in evidence)
    assert {item["record"]["kind"] for item in evidence} <= {"log", "deploy", "metric", "runbook"}

    runs = client.get(f"/api/incidents/{SLUG}/runs").json()
    assert runs[0]["id"] == run_id and runs[0]["report_status"] == "conclusive"
    assert client.get("/api/incidents").json()[0]["last_run_status"] in {"completed", None}

    for payload in (run, trace, evidence, runs):
        assert_no_ground_truth(payload)


def test_failed_run_is_visible_with_safe_error(test_db):
    with make_client(test_db, [final("no json"), final("still no json")]) as c:
        run_id = c.post(f"/api/incidents/{SLUG}/investigations").json()["run_id"]
        run = c.get(f"/api/runs/{run_id}").json()

    assert run["status"] == "failed"
    assert run["error"].startswith("malformed_response")
    assert run["report"] is None


def test_investigations_disabled_without_api_key(test_db):
    with make_client(test_db, enabled=False) as c:
        response = c.post(f"/api/incidents/{SLUG}/investigations")
        assert response.status_code == 503
        assert c.get("/api/config").json()["investigations_enabled"] is False


def test_concurrent_run_limit(test_db):
    with make_client(test_db, max_runs=1) as c:
        service = c.app.state.investigations
        service.start(1)  # occupies the only slot without executing
        response = c.post(f"/api/incidents/{SLUG}/investigations")

    assert response.status_code == 429


def test_hourly_run_limit(test_db):
    script = [GATHER, report_citing_seen]
    with make_client(test_db, script) as c:
        c.app.state.investigations._settings = c.app.state.settings.model_copy(update={"max_runs_per_hour": 1})
        first = c.post(f"/api/incidents/{SLUG}/investigations")
        second = c.post(f"/api/incidents/{SLUG}/investigations")

    assert first.status_code == 202
    assert second.status_code == 429


def test_interrupted_runs_are_failed_on_startup(test_db, admin_conn):
    run_id = admin_conn.execute(
        "INSERT INTO runs (scenario_id, model, status) SELECT id, 'm', 'running' FROM scenarios"
        f" WHERE slug = '{SLUG}' RETURNING id"
    ).fetchone()[0]
    admin_conn.commit()

    with make_client(test_db) as c:
        run = c.get(f"/api/runs/{run_id}").json()

    assert run["status"] == "failed" and run["error"].startswith("interrupted")

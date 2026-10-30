"""User-created incidents through the real API and database. Secrets here are fake test values."""

import json

import pytest

from app.eval.run import load_ground_truth
from app.tools.context import open_tool_context
from app.tools.evidence_lookup import resolve_evidence
from app.tools.investigation import run_tool
from tests.db.test_api import assert_no_ground_truth, make_client
from tests.fakes import FakeEmbedder
from tests.llm_fakes import calls, final, tool_call

pytestmark = pytest.mark.db

FAKE = "Zx9fakeSecretValue123"

LOGS = f"""\
2026-08-20T13:50:00Z INFO service=orders-api request ok
2026-08-20T13:58:30Z WARN service=orders-api db pool usage 92%
2026-08-20T14:00:02Z ERROR service=orders-api timeout acquiring db connection password={FAKE}
2026-08-20T14:00:09Z ERROR service=orders-api timeout acquiring db connection password={FAKE}
2026-08-20T14:00:15Z ERROR service=orders-db too many connections
"""
METRICS = """timestamp,service,metric,value,unit
2026-08-20T13:40:00Z,orders-db,connections,40,count
2026-08-20T14:00:00Z,orders-db,connections,100,count
"""
DEPLOYS = """timestamp,service,version,description
2026-08-20T13:30:00Z,orders-api,v7.1.0,New reporting query
"""


def new_incident(**overrides):
    return {
        "title": "Orders API failing",
        "alert_summary": "Orders 5xx above 20% since 14:00",
        "default_service": "orders-api",
        "logs": LOGS,
        "metrics_csv": METRICS,
        "deployments_csv": DEPLOYS,
        **overrides,
    }


@pytest.fixture
def client(test_db):
    with make_client(test_db, signed_in=True) as c:
        yield c


def create(client, **overrides):
    response = client.post("/api/incidents", json=new_incident(**overrides))
    assert response.status_code == 201, response.json()
    return response.json()


def test_create_incident_returns_a_parse_summary(client):
    body = create(client)

    assert body["slug"].startswith("u-") and body["kind"] == "user"
    assert body["incident_start"].startswith("2026-08-20T13:58:30")  # first WARN line (no start given)
    assert body["evidence"] == {
        "logs_parsed": 5, "metrics_parsed": 2, "deployments_parsed": 1, "timestamps_inferred": 0,
        "redactions": 2, "skipped": 0, "skipped_details": [], "outside_investigation_window": 0,
        "services": ["orders-api", "orders-db"],
    }
    assert FAKE not in json.dumps(body)


def test_created_incident_can_be_retrieved_and_listed(client):
    slug = create(client, incident_start="2026-08-20T14:00:00Z")["slug"]

    detail = client.get(f"/api/incidents/{slug}").json()
    assert detail["kind"] == "user" and detail["services"] == ["orders-api", "orders-db"]
    assert [d["version"] for d in detail["deployments"]] == ["v7.1.0"]
    assert any(m["evidence_id"] == "metric:orders-db:connections" for m in detail["metrics"])
    assert {e["level"] for e in detail["logs"]["entries"]} <= {"WARN", "ERROR", "FATAL"}

    listed = {i["slug"]: i for i in client.get("/api/incidents").json()}
    assert listed[slug]["kind"] == "user"
    assert all(i["kind"] == "demo" for s, i in listed.items() if not s.startswith("u-"))
    assert_no_ground_truth(detail)


def test_evidence_refs_are_stable_and_ordered(client, admin_conn):
    slug = create(client)["slug"]

    rows = admin_conn.execute(
        "SELECT l.ref, l.level FROM logs l JOIN scenarios s ON s.id = l.scenario_id WHERE s.slug = %s ORDER BY l.ts",
        (slug,),
    ).fetchall()
    assert [r[0] for r in rows] == ["log:0001", "log:0002", "log:0003", "log:0004", "log:0005"]
    deploys = admin_conn.execute(
        "SELECT d.ref FROM deployments d JOIN scenarios s ON s.id = d.scenario_id WHERE s.slug = %s", (slug,)
    ).fetchall()
    assert deploys == [("deploy:1",)]


def test_secrets_are_redacted_before_storage(client, admin_conn):
    slug = create(client, title=f"Orders down token={FAKE}")["slug"]

    stored = admin_conn.execute(
        "SELECT s.title, string_agg(l.message || l.attributes::text, ' ') FROM scenarios s"
        " JOIN logs l ON l.scenario_id = s.id WHERE s.slug = %s GROUP BY s.title",
        (slug,),
    ).fetchone()
    assert FAKE not in stored[0] and FAKE not in stored[1]
    assert "[REDACTED]" in stored[0] and "[REDACTED]" in stored[1]


def test_adding_evidence_continues_refs(client, admin_conn):
    slug = create(client)["slug"]

    response = client.post(f"/api/incidents/{slug}/evidence", json={
        "logs": "2026-08-20T14:05:00Z ERROR orders still failing", "default_service": "orders-api"})

    assert response.status_code == 200
    assert response.json()["evidence"]["logs_parsed"] == 1
    refs = admin_conn.execute(
        "SELECT max(l.ref) FROM logs l JOIN scenarios s ON s.id = l.scenario_id WHERE s.slug = %s", (slug,)
    ).fetchone()[0]
    assert refs == "log:0006"


def test_demo_incidents_cannot_receive_evidence(client):
    response = client.post("/api/incidents/checkout-db-pool-exhaustion/evidence",
                           json={"logs": "2026-03-10T14:00:00Z ERROR planted"})

    assert response.status_code == 403
    assert response.json()["detail"] == "demo incidents are read-only"


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"title": "x"}, "title"),
        ({"default_service": "Not A Service!"}, "default_service"),
        ({"unexpected": True}, "unexpected"),
        ({"metrics_csv": "no,header\n1,2"}, "metrics CSV needs a header row"),
        ({"title": f"password={FAKE} " * 20}, "title"),  # the invalid value itself holds a secret
    ],
    ids=["short-title", "bad-service", "unknown-field", "bad-csv", "secret-in-invalid-field"],
)
def test_invalid_requests_are_rejected_without_echoing_input(client, overrides, detail):
    response = client.post("/api/incidents", json=new_incident(**overrides))

    assert response.status_code == 422
    assert detail in json.dumps(response.json())
    assert FAKE not in json.dumps(response.json())  # validation errors never echo submitted text


def test_oversized_evidence_is_rejected(client):
    response = client.post("/api/incidents", json=new_incident(logs="x" * 1_000_001))

    assert response.status_code == 422
    assert FAKE not in response.text and "xxxx" not in response.text


def test_oversized_request_body_is_refused_before_parsing(client):
    response = client.post("/api/incidents", content=b" " * 4_000_001,
                           headers={"content-type": "application/json"})

    assert response.status_code == 413
    assert response.json() == {"detail": "request body too large"}


def test_incident_limits(test_db):
    with make_client(test_db, signed_in=True) as c:
        c.app.state.incident_limiter.limit = 1
        c.app.state.incident_limiter._events.clear()
        assert c.post("/api/incidents", json=new_incident()).status_code == 201
        assert c.post("/api/incidents", json=new_incident()).status_code == 429


def test_detail_reports_evidence_counts(client):
    slug = create(client, incident_start="2026-08-20T14:00:00Z")["slug"]
    empty = create(client, logs="", metrics_csv="", deployments_csv="")["slug"]

    full = client.get(f"/api/incidents/{slug}").json()["evidence"]
    assert {k: v for k, v in full.items() if k != "updated_at"} == {
        "logs": 5, "metric_points": 2, "deployments": 1, "usable": 8}
    assert full["updated_at"]  # when evidence was last stored (drives the "report outdated" hint)
    assert client.get(f"/api/incidents/{empty}").json()["evidence"] == {
        "logs": 0, "metric_points": 0, "deployments": 0, "usable": 0, "updated_at": None}
    assert client.get("/api/incidents/checkout-db-pool-exhaustion").json()["evidence"]["usable"] > 0


def test_cannot_investigate_without_usable_evidence(client):
    empty = create(client, logs="", metrics_csv="", deployments_csv="")["slug"]
    # Logs/metrics end >3h before this start, so the tools could never see them.
    far = create(client, incident_start="2026-08-20T23:00:00Z", deployments_csv="")["slug"]

    for slug in (empty, far):
        response = client.post(f"/api/incidents/{slug}/investigations")
        assert response.status_code == 422
        assert "no evidence the investigation can use" in response.json()["detail"]


def test_tools_only_see_the_selected_incident(client, test_db):
    a = create(client, incident_start="2026-08-20T14:00:00Z")["slug"]
    b = create(client, incident_start="2026-08-20T14:00:00Z", default_service="billing",
               logs="2026-08-20T14:00:01Z ERROR service=billing invoice job crashed",
               metrics_csv="", deployments_csv="")["slug"]

    with open_tool_context(test_db["tools_ro"], a, FakeEmbedder()) as ctx:
        assert ctx.services == ("orders-api", "orders-db")
        everything = run_tool(ctx, "query_logs", {"min_level": "DEBUG", "minutes_before": 180, "limit": 50})
        assert "invoice" not in json.dumps(everything.data)
        assert not run_tool(ctx, "query_logs", {"service": "billing"}).ok  # not in A's allowlist
        # B also has a log:0001, but lookups resolve within A only.
        assert "orders" in resolve_evidence(ctx, ["log:0001"])["log:0001"]["service"]

    with open_tool_context(test_db["tools_ro"], b, FakeEmbedder()) as ctx:
        assert ctx.services == ("billing",)


def test_demo_allowlist_is_limited_to_its_own_services(test_db):
    with open_tool_context(test_db["tools_ro"], "auth-certificate-expiry", FakeEmbedder()) as ctx:
        assert "orders-api" not in ctx.services and "payments-db" not in ctx.services
        assert "auth-service" in ctx.services


def test_user_incident_investigation_cites_user_evidence(test_db):
    def answer(messages):
        tool_text = " ".join(m["content"] for m in messages if m["role"] == "tool")
        cited = [i for i in ("log:0003", "deploy:1", "metric:orders-db:connections") if i in tool_text]
        return final({
            "status": "conclusive",
            "facts": [{"statement": "orders-db connections reached 100", "evidence_ids": cited[-1:]}],
            "root_cause": "orders-api v7.1.0 exhausts the orders-db pool",
            "affected_service": "orders-api", "confidence": 0.7, "explanation": "timeline",
            "evidence": [{"evidence_id": i, "why": "observed"} for i in cited],
        })

    script = [calls(tool_call("query_logs", {"min_level": "WARN"}),
                    tool_call("get_recent_deployments", {}),
                    tool_call("get_metrics", {"service": "orders-db"})), answer]
    with make_client(test_db, script, signed_in=True) as c:
        slug = create(c, incident_start="2026-08-20T14:00:00Z")["slug"]
        run_id = c.post(f"/api/incidents/{slug}/investigations").json()["run_id"]
        run = c.get(f"/api/runs/{run_id}").json()
        evidence = c.get(f"/api/runs/{run_id}/evidence").json()

    assert run["status"] == "completed", run["error"]
    assert run["report"]["affected_service"] == "orders-api"
    assert {e["evidence_id"] for e in evidence} == {"log:0003", "deploy:1", "metric:orders-db:connections"}
    assert all(e["record"] is not None for e in evidence)
    assert FAKE not in json.dumps(evidence)


def test_user_incidents_are_excluded_from_evaluation(client, test_db):
    create(client)

    cases = load_ground_truth(test_db["admin"], "all")

    assert len(cases) == 4
    assert all(not truth.slug.startswith("u-") for _, _, _, truth in cases)

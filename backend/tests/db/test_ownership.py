"""Account isolation: account B (and anonymous visitors) can neither see nor change anything that
belongs to account A, through any endpoint, and cannot tell A's incidents from ones that do not
exist. Demo incidents stay public and read-only. Two separate clients = two separate browsers."""

import json
import uuid

import pytest

from tests.db.test_agent import GATHER, report_citing_seen
from tests.db.test_api import make_client
from tests.db.test_user_incidents import new_incident
from tests.llm_fakes import ScriptedLLM

pytestmark = pytest.mark.db

DEMO = "checkout-db-pool-exhaustion"
NO_SUCH_SLUG = "u-0000000000000000"
NOT_FOUND = {"detail": "incident not found"}
RUN_NOT_FOUND = {"detail": "run not found"}


def create(client, **overrides) -> str:
    response = client.post("/api/incidents", json=new_incident(incident_start="2026-08-20T14:00:00Z", **overrides))
    assert response.status_code == 201, response.json()
    return response.json()["slug"]


def investigate(client, slug) -> str:
    response = client.post(f"/api/incidents/{slug}/investigations")
    assert response.status_code == 202, response.json()
    run_id = response.json()["run_id"]
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"
    return run_id


@pytest.fixture
def accounts(test_db):
    """A (with one incident and a completed run), B (with its own incident) and an anonymous visitor."""
    with (make_client(test_db, [GATHER, report_citing_seen], signed_in=True) as a,
          make_client(test_db, [GATHER, report_citing_seen], signed_in=True) as b,
          make_client(test_db, [GATHER, report_citing_seen]) as anonymous):
        slug = create(a)
        run_id = investigate(a, slug)
        yield {"a": a, "b": b, "anonymous": anonymous, "slug": slug, "run_id": run_id, "b_slug": create(b)}


def evidence_counts(admin_conn, slug):
    return admin_conn.execute(
        "SELECT (SELECT count(*) FROM logs l WHERE l.scenario_id = s.id),"
        " (SELECT count(*) FROM runs r WHERE r.scenario_id = s.id) FROM scenarios s WHERE s.slug = %s",
        (slug,),
    ).fetchone()


def test_incidents_are_owned_by_the_account_that_created_them(accounts, admin_conn):
    owner = admin_conn.execute("SELECT owner_id, kind FROM scenarios WHERE slug = %s", (accounts["slug"],)).fetchone()
    assert owner == (accounts["a"].user["id"], "user")


def test_the_owner_cannot_be_chosen_by_the_client(accounts):
    b = accounts["b"]
    forged = b.post("/api/incidents", json={**new_incident(), "owner_id": accounts["a"].user["id"]})
    assert forged.status_code == 422  # unknown fields are rejected outright


def test_each_account_lists_only_its_own_incidents_and_the_demos(accounts):
    a_list = {i["slug"]: i["kind"] for i in accounts["a"].get("/api/incidents").json()}
    b_list = {i["slug"]: i["kind"] for i in accounts["b"].get("/api/incidents").json()}
    anon_list = {i["slug"]: i["kind"] for i in accounts["anonymous"].get("/api/incidents").json()}

    assert accounts["slug"] in a_list and accounts["b_slug"] not in a_list
    assert accounts["b_slug"] in b_list and accounts["slug"] not in b_list
    assert set(anon_list.values()) == {"demo"}
    for listing in (a_list, b_list, anon_list):
        assert DEMO in listing
        assert {s for s, k in listing.items() if k == "demo"} == {s for s, k in anon_list.items()}


@pytest.mark.parametrize("who", ["b", "anonymous"])
def test_others_get_exactly_the_not_found_response_of_a_missing_incident(accounts, who):
    client, slug = accounts[who], accounts["slug"]
    for template in ("/api/incidents/{}", "/api/incidents/{}/runs"):
        theirs, missing = client.get(template.format(slug)), client.get(template.format(NO_SUCH_SLUG))
        assert (theirs.status_code, theirs.json()) == (missing.status_code, missing.json()) == (404, NOT_FOUND)

    theirs = client.post(f"/api/incidents/{slug}/investigations")
    missing = client.post(f"/api/incidents/{NO_SUCH_SLUG}/investigations")
    assert (theirs.status_code, theirs.json()) == (missing.status_code, missing.json()) == (404, NOT_FOUND)


@pytest.mark.parametrize("who", ["b", "anonymous"])
def test_others_cannot_read_runs_traces_reports_or_evidence(accounts, who):
    client, run_id = accounts[who], accounts["run_id"]
    for template in ("/api/runs/{}", "/api/runs/{}/trace", "/api/runs/{}/evidence"):
        theirs, missing = client.get(template.format(run_id)), client.get(template.format(uuid.uuid4()))
        assert (theirs.status_code, theirs.json()) == (missing.status_code, missing.json()) == (404, RUN_NOT_FOUND)


def test_b_cannot_add_evidence_or_delete_a(accounts, admin_conn):
    b, slug = accounts["b"], accounts["slug"]
    before = evidence_counts(admin_conn, slug)

    added = b.post(f"/api/incidents/{slug}/evidence", json={"logs": "2026-08-20T14:00:00Z ERROR planted by B"})
    deleted = b.delete(f"/api/incidents/{slug}")
    for response in (added, deleted):
        assert (response.status_code, response.json()) == (404, NOT_FOUND)

    admin_conn.rollback()
    assert evidence_counts(admin_conn, slug) == before
    assert accounts["a"].get(f"/api/incidents/{slug}").status_code == 200  # A is unaffected


def test_anonymous_visitors_cannot_change_anything(accounts):
    anonymous, slug = accounts["anonymous"], accounts["slug"]
    for method, path, body in [
        ("POST", "/api/incidents", new_incident()),
        ("POST", f"/api/incidents/{slug}/evidence", {"logs": "x"}),
        ("DELETE", f"/api/incidents/{slug}", None),
    ]:
        response = anonymous.request(method, path, json=body)
        assert (response.status_code, response.json()) == (401, {"detail": "login required"}), path


def test_a_stolen_csrf_token_does_not_help_b(accounts):
    b, slug = accounts["b"], accounts["slug"]
    a_token = accounts["a"].headers["X-CSRF-Token"]
    response = b.delete(f"/api/incidents/{slug}", headers={"X-CSRF-Token": a_token})
    assert response.status_code == 403  # B's session requires B's token


def test_changing_ids_never_reaches_another_account(accounts):
    """B walks its own URLs, swapping in A's identifiers: always 404, never A's data."""
    a, b = accounts["a"], accounts["b"]
    b_run = investigate(b, accounts["b_slug"])
    a_run = accounts["run_id"]

    assert b.get(f"/api/runs/{b_run}").status_code == 200
    assert b.get(f"/api/runs/{a_run}").status_code == 404
    assert a.get(f"/api/runs/{b_run}").status_code == 404
    b_runs = b.get(f"/api/incidents/{accounts['b_slug']}/runs").json()
    assert [r["id"] for r in b_runs] == [b_run]
    assert accounts["slug"] not in json.dumps(b.get(f"/api/runs/{b_run}").json())


def test_demos_are_public_and_read_only_for_everyone(accounts, admin_conn):
    before = evidence_counts(admin_conn, DEMO)
    for who in ("a", "b", "anonymous"):
        client = accounts[who]
        assert client.get(f"/api/incidents/{DEMO}").status_code == 200
        assert client.get(f"/api/incidents/{DEMO}/runs").status_code == 200
    for who in ("a", "b"):
        client = accounts[who]
        for response in (client.post(f"/api/incidents/{DEMO}/evidence", json={"logs": "planted"}),
                         client.delete(f"/api/incidents/{DEMO}")):
            assert (response.status_code, response.json()) == (403, {"detail": "demo incidents are read-only"})
    admin_conn.rollback()
    assert evidence_counts(admin_conn, DEMO)[0] == before[0]


def test_demo_runs_are_visible_to_everyone(accounts):
    demo_run = investigate(accounts["anonymous"], DEMO)
    for who in ("a", "b", "anonymous"):
        assert accounts[who].get(f"/api/runs/{demo_run}/trace").status_code == 200


def test_owner_deletes_an_incident_with_everything_in_it(accounts, admin_conn):
    a, slug, run_id = accounts["a"], accounts["slug"], accounts["run_id"]
    scenario_id = admin_conn.execute("SELECT id FROM scenarios WHERE slug = %s", (slug,)).fetchone()[0]
    admin_conn.rollback()

    assert a.delete(f"/api/incidents/{slug}").status_code == 204

    assert a.get(f"/api/incidents/{slug}").json() == NOT_FOUND
    assert a.get(f"/api/runs/{run_id}").json() == RUN_NOT_FOUND
    assert slug not in json.dumps(a.get("/api/incidents").json())
    for sql in ("SELECT count(*) FROM scenarios WHERE id = %s", "SELECT count(*) FROM logs WHERE scenario_id = %s",
                "SELECT count(*) FROM metrics WHERE scenario_id = %s",
                "SELECT count(*) FROM deployments WHERE scenario_id = %s",
                "SELECT count(*) FROM runs WHERE scenario_id = %s"):
        assert admin_conn.execute(sql, (scenario_id,)).fetchone()[0] == 0, sql
    assert admin_conn.execute("SELECT count(*) FROM trace_steps WHERE run_id = %s", (run_id,)).fetchone()[0] == 0
    assert a.delete(f"/api/incidents/{slug}").status_code == 404  # already gone
    assert accounts["b"].get(f"/api/incidents/{accounts['b_slug']}").status_code == 200  # B untouched


def test_an_incident_cannot_be_deleted_mid_investigation(accounts, admin_conn):
    a, slug = accounts["a"], accounts["slug"]
    admin_conn.execute("INSERT INTO runs (scenario_id, model, status) SELECT id, 'm', 'running'"
                       " FROM scenarios WHERE slug = %s", (slug,))
    admin_conn.commit()

    response = a.delete(f"/api/incidents/{slug}")
    assert response.status_code == 409 and "still running" in response.json()["detail"]
    assert a.get(f"/api/incidents/{slug}").status_code == 200


def test_per_account_incident_cap(test_db):
    with (make_client(test_db, max_incidents_per_user=2, signed_in=True) as a,
          make_client(test_db, max_incidents_per_user=2, signed_in=True) as b):
        create(a), create(a)
        refused = a.post("/api/incidents", json=new_incident())
        assert refused.status_code == 429 and "at most 2 incidents" in refused.json()["detail"]
        create(b)  # another account has its own allowance
        slug = a.get("/api/incidents").json()[0]["slug"]
        assert a.delete(f"/api/incidents/{slug}").status_code == 204
        create(a)  # deleting frees a slot


def test_per_account_investigation_budget(test_db):
    with (make_client(test_db, [GATHER, report_citing_seen], max_runs_per_user_per_hour=1, signed_in=True) as a,
          make_client(test_db, [GATHER, report_citing_seen], max_runs_per_user_per_hour=1, signed_in=True) as b):
        investigate(a, DEMO)
        refused = a.post(f"/api/incidents/{DEMO}/investigations")
        assert refused.status_code == 429 and "investigation limit" in refused.json()["detail"]
        investigate(b, DEMO)


def test_cached_answers_are_never_shared_between_accounts(test_db):
    calls = {"model": 0}

    class Counting(ScriptedLLM):
        def complete(self, messages, tools, tool_choice):
            calls["model"] += 1
            return super().complete(messages, tools, tool_choice)

    with (make_client(test_db, llm_cache_enabled=True, signed_in=True) as a,
          make_client(test_db, llm_cache_enabled=True, signed_in=True) as b):
        for client in (a, b):
            client.app.state.investigations._llm_factory = lambda: Counting([GATHER, report_citing_seen])
        investigate(a, create(a))
        after_a = calls["model"]
        investigate(b, create(b))  # byte-identical evidence in B's own incident

    assert after_a == 2 and calls["model"] == 4  # B's run got no cache hits from A's


def test_logging_out_revokes_access_to_owned_incidents(accounts):
    a, slug = accounts["a"], accounts["slug"]
    assert a.post("/api/auth/logout").status_code == 204
    assert a.get(f"/api/incidents/{slug}").json() == NOT_FOUND
    assert a.get(f"/api/runs/{accounts['run_id']}").json() == RUN_NOT_FOUND

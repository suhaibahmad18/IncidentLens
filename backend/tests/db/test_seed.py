import pytest

from app.db.seed import build_all, seed
from app.seed_data.ground_truth import GROUND_TRUTH

pytestmark = pytest.mark.db

COUNT_SQL = (
    "SELECT s.slug, (SELECT count(*) FROM logs l WHERE l.scenario_id = s.id),"
    " (SELECT count(*) FROM metrics m WHERE m.scenario_id = s.id),"
    " (SELECT count(*) FROM deployments d WHERE d.scenario_id = s.id),"
    " (SELECT array_agg(l.ref ORDER BY l.ref) FROM logs l WHERE l.scenario_id = s.id)"
    " FROM scenarios s ORDER BY s.slug"
)


def test_four_scenarios_with_one_holdout(admin_conn):
    rows = admin_conn.execute(
        "SELECT s.slug, g.split FROM scenarios s JOIN scenario_ground_truth g ON g.scenario_id = s.id"
    ).fetchall()

    assert len(rows) == 4
    assert [slug for slug, split in rows if split == "holdout"] == ["notification-email-backlog"]


def test_reseeding_is_idempotent_and_refs_are_stable(test_db, admin_conn):
    before = admin_conn.execute(COUNT_SQL).fetchall()
    admin_conn.rollback()

    seed(test_db["admin"])
    after = admin_conn.execute(COUNT_SQL).fetchall()

    assert after == before
    services = admin_conn.execute("SELECT count(*), count(DISTINCT name) FROM services").fetchone()
    assert services[0] == services[1] == 7


def test_key_evidence_exists_in_seeded_data(admin_conn):
    rows = admin_conn.execute(
        "SELECT s.id, s.slug, g.key_evidence_ids FROM scenarios s"
        " JOIN scenario_ground_truth g ON g.scenario_id = s.id"
    ).fetchall()

    for scenario_id, slug, key_ids in rows:
        assert len(key_ids) >= 3, slug
        for evidence_id in key_ids:
            kind, _, rest = evidence_id.partition(":")
            if kind == "log":
                query = "SELECT 1 FROM logs WHERE scenario_id = %s AND ref = %s"
                params = (scenario_id, evidence_id)
            elif kind == "deploy":
                query = "SELECT 1 FROM deployments WHERE scenario_id = %s AND ref = %s"
                params = (scenario_id, evidence_id)
            else:
                service, name = rest.split(":")
                query = ("SELECT 1 FROM metrics m JOIN services s ON s.id = m.service_id"
                         " WHERE m.scenario_id = %s AND s.name = %s AND m.name = %s LIMIT 1")
                params = (scenario_id, service, name)
            assert admin_conn.execute(query, params).fetchone(), f"{slug}: {evidence_id}"


def test_alert_text_does_not_give_away_the_answer():
    """Title + alert alone must not satisfy the scoring keywords (symptoms, not causes)."""
    for data in build_all():
        visible = f"{data.title} {data.alert_summary}".lower()
        groups = GROUND_TRUTH[data.slug]["keywords"]
        assert not all(any(k in visible for k in group) for group in groups), data.slug


def test_builders_are_deterministic():
    first = [(d.slug, d.log_refs()[:5], d.key_evidence_ids()) for d in build_all()]
    second = [(d.slug, d.log_refs()[:5], d.key_evidence_ids()) for d in build_all()]

    assert first == second

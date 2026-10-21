import pytest

pytestmark = pytest.mark.db


@pytest.mark.parametrize(
    ("fixture", "role"),
    [("admin_conn", "postgres"), ("app_conn", "incidentlens_app"), ("ro_conn", "tools_ro")],
)
def test_each_role_connects_to_test_database(request, fixture, role):
    conn = request.getfixturevalue(fixture)

    user, database = conn.execute("SELECT current_user, current_database()").fetchone()

    assert user == role
    assert database == "incidentlens_test"


def test_vector_extension_installed(admin_conn):
    assert admin_conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()


@pytest.mark.parametrize("role", ["incidentlens_app", "tools_ro"])
def test_application_roles_are_not_superusers(admin_conn, role):
    is_super = admin_conn.execute(
        "SELECT rolsuper FROM pg_roles WHERE rolname = %s", (role,)
    ).fetchone()[0]

    assert is_super is False

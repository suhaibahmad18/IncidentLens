"""Shared fixtures. Database fixtures use a throwaway, guarded test database only."""

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from app.db.connection import connect
from app.db.migrate import run_migrations
from app.db.seed import seed
from app.rag.ingest import ingest_runbooks
from app.tools.context import open_tool_context
from tests.fakes import FakeEmbedder
from tests.db_safety import (
    ALLOWED_TEST_DATABASE,
    OWNER_MARKER,
    TestDatabaseSettings,
    UnsafeTestDatabase,
    check_owner_marker,
    check_test_database_config,
)


def _with_dbname(url: SecretStr, dbname: str) -> SecretStr:
    return SecretStr(make_conninfo(url.get_secret_value(), dbname=dbname))


def _reset_test_database(admin_url: SecretStr) -> None:
    # Work from the maintenance database, never from the dev or test database itself.
    with connect(_with_dbname(admin_url, "postgres"), autocommit=True) as conn:
        row = conn.execute(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s",
            (ALLOWED_TEST_DATABASE,),
        ).fetchone()
        check_owner_marker(exists=row is not None, comment=row[0] if row else None)

        name = sql.Identifier(ALLOWED_TEST_DATABASE)
        if row is not None:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(name))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(name))
        conn.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(name, sql.Literal(OWNER_MARKER)))




@pytest.fixture(scope="session")
def test_db() -> dict[str, SecretStr]:
    """Create a fresh, migrated, seeded test database. Returns URLs for each role, pointing at it."""
    test_name = TestDatabaseSettings().test_database_name
    if not test_name:
        pytest.skip("TEST_DATABASE_NAME not set")

    from app.config import get_admin_settings, get_settings

    settings, admin = get_settings(), get_admin_settings()
    dev_urls = {
        "DATABASE_ADMIN_URL": admin.database_admin_url,
        "DATABASE_URL": settings.database_url,
        "TOOLS_RO_DATABASE_URL": settings.tools_ro_database_url,
    }
    try:
        check_test_database_config(test_name, dev_urls)
        _reset_test_database(admin.database_admin_url)
    except UnsafeTestDatabase as exc:
        pytest.exit(f"unsafe test database configuration: {exc}", returncode=2)
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL unreachable (is `docker compose up -d db` running?)")

    urls = {
        "admin": _with_dbname(admin.database_admin_url, ALLOWED_TEST_DATABASE),
        "app": _with_dbname(settings.database_url, ALLOWED_TEST_DATABASE),
        "tools_ro": _with_dbname(settings.tools_ro_database_url, ALLOWED_TEST_DATABASE),
    }
    run_migrations(urls["admin"])
    seed(urls["admin"])
    ingest_runbooks(urls["admin"], FakeEmbedder())
    return urls


def _connection(url: SecretStr):
    conn = connect(url)
    try:
        assert conn.execute("SELECT current_database()").fetchone()[0] == ALLOWED_TEST_DATABASE
        conn.rollback()
        yield conn
    finally:
        conn.rollback()  # every test's writes are discarded
        conn.close()


@pytest.fixture
def admin_conn(test_db):
    yield from _connection(test_db["admin"])


@pytest.fixture
def app_conn(test_db):
    yield from _connection(test_db["app"])


@pytest.fixture
def ro_conn(test_db):
    yield from _connection(test_db["tools_ro"])


@pytest.fixture
def tool_ctx(test_db):
    with open_tool_context(test_db["tools_ro"], "checkout-db-pool-exhaustion", FakeEmbedder()) as ctx:
        yield ctx

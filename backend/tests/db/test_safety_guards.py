"""Offline tests for the test-database guard. No database connections."""

import pytest
from pydantic import SecretStr

from tests.db_safety import (
    OWNER_MARKER,
    UnsafeTestDatabase,
    check_owner_marker,
    check_test_database_config,
)


def url(dbname="incidentlens", host="127.0.0.1"):
    return SecretStr(f"postgresql://postgres:fake-pw-000000000000@{host}:5432/{dbname}")


def dev_urls(**overrides):
    urls = {"DATABASE_ADMIN_URL": url(), "DATABASE_URL": url(), "TOOLS_RO_DATABASE_URL": url()}
    urls.update(overrides)
    return urls


def test_accepts_exact_test_database_name():
    check_test_database_config("incidentlens_test", dev_urls())


@pytest.mark.parametrize(
    "name",
    [None, "", "incidentlens", "other_test", "incidentlens_test2", "INCIDENTLENS_TEST", " incidentlens_test"],
    ids=["unset", "empty", "dev-name", "other-suffix", "extra-char", "case", "whitespace"],
)
def test_rejects_any_name_except_the_allowed_one(name):
    with pytest.raises(UnsafeTestDatabase, match="must be exactly"):
        check_test_database_config(name, dev_urls())


@pytest.mark.parametrize("setting", ["DATABASE_ADMIN_URL", "DATABASE_URL", "TOOLS_RO_DATABASE_URL"])
def test_rejects_dev_url_pointing_at_test_database(setting):
    urls = dev_urls(**{setting: url(dbname="incidentlens_test")})

    with pytest.raises(UnsafeTestDatabase, match=f"{setting} points at the test database"):
        check_test_database_config("incidentlens_test", urls)


def test_rejects_non_local_host():
    urls = dev_urls(DATABASE_ADMIN_URL=url(host="db.example.com"))

    with pytest.raises(UnsafeTestDatabase, match="not a local host"):
        check_test_database_config("incidentlens_test", urls)


def test_existing_database_without_marker_is_never_dropped():
    with pytest.raises(UnsafeTestDatabase, match="not created by pytest"):
        check_owner_marker(exists=True, comment=None)
    with pytest.raises(UnsafeTestDatabase):
        check_owner_marker(exists=True, comment="something else")


def test_marker_allows_reset():
    check_owner_marker(exists=True, comment=OWNER_MARKER)
    check_owner_marker(exists=False, comment=None)


def test_guard_messages_contain_no_password():
    with pytest.raises(UnsafeTestDatabase) as exc_info:
        check_test_database_config("incidentlens_test", dev_urls(DATABASE_URL=url(host="evil.example")))

    assert "fake-pw" not in str(exc_info.value)

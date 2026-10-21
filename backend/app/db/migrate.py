"""Apply backend/migrations/NNNN_name.sql files in order, each exactly once.

Run from backend/:  uv run python -m app.db.migrate

- Each file and its schema_migrations row commit together, or not at all.
- A checksum per file detects edits to already-applied migrations (aborts, never re-runs).
- An advisory lock serializes concurrent runs.
"""

import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg
from pydantic import SecretStr

from app.config import get_admin_settings
from app.db.connection import connect

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
FILENAME_PATTERN = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
ADVISORY_LOCK_KEY = 7_204_312


class MigrationError(Exception):
    """A failure whose message is safe to print."""


@dataclass(frozen=True)
class Migration:
    version: str
    sql: str
    checksum: str


def load_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        if not FILENAME_PATTERN.match(path.name):
            raise MigrationError(f"unexpected migration filename: {path.name}")
        # Normalize line endings so Git's CRLF conversion on Windows doesn't change checksums.
        sql = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        migrations.append(Migration(path.stem, sql, checksum))
    return migrations


def run_migrations(url: SecretStr, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations. Returns the versions applied by this call."""
    migrations = load_migrations(directory)
    applied_now = []
    with connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version text PRIMARY KEY,"
                " checksum text NOT NULL,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )
            applied = dict(conn.execute("SELECT version, checksum FROM schema_migrations"))

            unknown = sorted(set(applied) - {m.version for m in migrations})
            if unknown:
                raise MigrationError(f"database has migrations missing on disk: {', '.join(unknown)}")

            for migration in migrations:
                if migration.version in applied:
                    if applied[migration.version] != migration.checksum:
                        raise MigrationError(
                            f"{migration.version} was edited after being applied; "
                            "add a new migration instead"
                        )
                    continue
                with conn.transaction():
                    conn.execute(migration.sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                        (migration.version, migration.checksum),
                    )
                applied_now.append(migration.version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
    return applied_now


def main() -> int:
    step = "loading settings"
    try:
        url = get_admin_settings().database_admin_url
        step = "applying migrations"
        applied = run_migrations(url)
    except MigrationError as exc:
        print(f"migrate failed while {step}: {exc}", file=sys.stderr)
        return 1
    except psycopg.errors.DatabaseError as exc:
        # SQL errors from migration files are server messages about schema, not credentials.
        detail = exc.diag.message_primary if exc.diag else None
        print(f"migrate failed while {step}: {type(exc).__name__}: {detail}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"migrate failed while {step}: {type(exc).__name__}", file=sys.stderr)
        return 1

    print(f"{len(applied)} migration(s) applied" + (f": {', '.join(applied)}" if applied else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

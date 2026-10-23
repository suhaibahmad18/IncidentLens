"""One-shot release task: bootstrap roles -> migrate -> seed scenarios -> ingest runbooks.

Run from backend/ (or as the platform's pre-deploy command):  python -m app.db.release

Every step is idempotent, so this is safe to run on every deploy. It needs
DATABASE_ADMIN_URL; the running API never reads that variable.
"""

import sys

from app.db import bootstrap, migrate, seed
from app.rag import ingest

STEPS = (
    ("bootstrap roles", bootstrap.main),
    ("apply migrations", migrate.main),
    ("seed scenarios", seed.main),
    ("ingest runbooks", ingest.main),
)


def main() -> int:
    for name, step in STEPS:
        print(f"== {name}", flush=True)
        code = step()
        if code != 0:
            print(f"release stopped: '{name}' failed", file=sys.stderr)
            return code
    print("release complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())

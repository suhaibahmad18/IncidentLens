"""Static guard: only seeding (and the offline evaluator) may touch ground truth.

seed_data/ holds the scenario definitions and answers; it is used by the seed command only.
Every other module must neither import it nor mention ground-truth names.
"""

from pathlib import Path

import app

APP_DIR = Path(app.__file__).parent
SEED_ONLY = {Path("db/seed.py")}
FORBIDDEN_WORDS = ("scenario_ground_truth", "ground_truth", "expected_root_cause", "key_evidence_ids")


def _agent_facing_modules():
    for path in APP_DIR.rglob("*.py"):
        relative = path.relative_to(APP_DIR)
        if relative in SEED_ONLY or relative.parts[0] in ("seed_data", "eval"):
            continue
        yield relative, path.read_text(encoding="utf-8")


def test_no_agent_facing_code_mentions_ground_truth():
    offenders = [
        f"{relative}: {word}"
        for relative, text in _agent_facing_modules()
        for word in FORBIDDEN_WORDS
        if word in text
    ]

    assert offenders == []


def test_no_agent_facing_code_imports_seed_data_or_the_evaluator():
    offenders = [
        str(relative)
        for relative, text in _agent_facing_modules()
        if "app.seed_data" in text or "app.eval" in text
    ]

    assert offenders == []

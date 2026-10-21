"""Evaluate the agent on every scenario against hidden ground truth.

Run from backend/:  uv run python -m app.eval.run [--split dev|holdout|all] [--no-cache]

This is the ONLY runtime code that reads ground truth, and it does so through the admin
connection. The agent itself runs exactly as in production (tools_ro tools, incidentlens_app
for runs), so it cannot see the answers. Results are stored in eval_runs/eval_results.
"""

import argparse
import subprocess
import sys
from collections.abc import Callable

from psycopg.types.json import Jsonb
from pydantic import SecretStr

from app.agent.llm import ChatClient
from app.config import Settings, get_admin_settings, get_settings
from app.db.connection import connect
from app.eval.scoring import GroundTruth, Score, score_report
from app.rag.embeddings import Embedder, get_embedder
from app.runs.service import InvestigationService, groq_client_factory


def load_ground_truth(admin_url: SecretStr, split: str) -> list[tuple[int, str, str, GroundTruth]]:
    with connect(admin_url) as conn:
        rows = conn.execute(
            "SELECT s.id, s.slug, s.title, s.alert_summary, g.split, g.expected_root_cause, sv.name,"
            " g.root_cause_keywords, g.key_evidence_ids"
            " FROM scenario_ground_truth g JOIN scenarios s ON s.id = g.scenario_id"
            " JOIN services sv ON sv.id = g.root_cause_service_id"
            " WHERE %s = 'all' OR g.split = %s ORDER BY g.split, s.slug",
            (split, split),
        ).fetchall()
    return [
        (sid, title, alert, GroundTruth(slug, gsplit, expected, service, keywords, list(key_ids)))
        for sid, slug, title, alert, gsplit, expected, service, keywords, key_ids in rows
    ]


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5, check=True).stdout.strip() or None
    except Exception:
        return None


def run_evaluation(
    settings: Settings,
    admin_url: SecretStr,
    llm_factory: Callable[[], ChatClient],
    embedder: Embedder,
    split: str = "all",
) -> tuple[str, list[tuple[GroundTruth, Score, str]]]:
    cases = load_ground_truth(admin_url, split)
    service = InvestigationService(settings, embedder, llm_factory)
    config = {
        "split": split,
        "max_iterations": settings.agent_max_iterations,
        "max_tool_calls": settings.agent_max_tool_calls,
        "max_tool_result_chars": settings.agent_max_tool_result_chars,
        "llm_cache": settings.llm_cache_enabled,
    }
    with connect(settings.database_url, autocommit=True) as conn:
        eval_run_id = conn.execute(
            "INSERT INTO eval_runs (model, config, git_commit) VALUES (%s, %s, %s) RETURNING id",
            (settings.groq_model, Jsonb(config), _git_commit()),
        ).fetchone()[0]

        results = []
        for scenario_id, title, alert, truth in cases:
            run_id = service.start(scenario_id)
            service.execute(run_id, truth.slug, title, alert)
            run_status, report = conn.execute(
                "SELECT status, final_report FROM runs WHERE id = %s", (run_id,)
            ).fetchone()
            score = score_report(report if run_status == "completed" else None, truth)
            conn.execute(
                "INSERT INTO eval_results (eval_run_id, scenario_id, run_id, root_cause_correct, score, details)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (eval_run_id, scenario_id, run_id, score.root_cause_correct, score.score,
                 Jsonb({"split": truth.split, **score.as_dict()})),
            )
            results.append((truth, score, str(run_id)))

        conn.execute("UPDATE eval_runs SET status = 'completed', finished_at = now() WHERE id = %s", (eval_run_id,))
    return str(eval_run_id), results


def format_results(results: list[tuple[GroundTruth, Score, str]]) -> str:
    lines = [
        f"{'scenario':30} {'split':8} {'done':5} {'status':13} {'service':8} {'keywords':9} "
        f"{'correct':8} {'key-recall':10} {'cited/dropped':14} {'unsupported':11} {'score':5}"
    ]
    for truth, s, _ in results:
        lines.append(
            f"{truth.slug:30} {truth.split:8} {str(s.completed):5} "
            f"{('conclusive' if s.conclusive else 'inconclusive') if s.completed else '-':13} "
            f"{str(s.service_correct):8} {f'{s.keyword_groups_matched}/{s.keyword_groups_total}':9} "
            f"{str(s.root_cause_correct):8} {s.key_evidence_recall:<10} "
            f"{f'{s.citations_valid}/{s.citations_dropped}':14} {str(s.unsupported_claim):11} {s.score:<5}"
        )
    for split in ("dev", "holdout"):
        subset = [s for truth, s, _ in results if truth.split == split]
        if subset:
            correct = sum(s.root_cause_correct for s in subset)
            mean = sum(s.score for s in subset) / len(subset)
            lines.append(f"{split}: root cause correct {correct}/{len(subset)}, mean score {mean:.2f}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate IncidentLens against hidden ground truth.")
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    parser.add_argument("--no-cache", action="store_true", help="always call the model (ignore llm_cache)")
    args = parser.parse_args()

    try:
        settings = get_settings()
        if args.no_cache:
            settings = settings.model_copy(update={"llm_cache_enabled": False})
        llm_factory = groq_client_factory(settings)
        if llm_factory is None:
            print("evaluation needs GROQ_API_KEY in .env", file=sys.stderr)
            return 1
        eval_run_id, results = run_evaluation(
            settings, get_admin_settings().database_admin_url, llm_factory, get_embedder(), args.split
        )
    except Exception as exc:
        print(f"evaluation failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    print(f"eval run {eval_run_id} (model {settings.groq_model})")
    print(format_results(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Score one investigation report against hidden ground truth. Deterministic, no LLM judge."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class GroundTruth:
    slug: str
    split: str
    expected_root_cause: str
    service: str
    keywords: list[list[str]]
    key_evidence_ids: list[str]


@dataclass(frozen=True)
class Score:
    completed: bool
    conclusive: bool
    service_correct: bool
    keyword_groups_matched: int
    keyword_groups_total: int
    root_cause_correct: bool
    citations_valid: int
    citations_dropped: int
    key_evidence_recall: float
    unsupported_claim: bool
    score: float

    def as_dict(self) -> dict:
        return asdict(self)


def score_report(report: dict | None, truth: GroundTruth) -> Score:
    if not report:
        return Score(False, False, False, 0, len(truth.keywords), False, 0, 0, 0.0, False, 0.0)

    text = f"{report.get('root_cause', '')} {report.get('explanation', '')}".lower()
    matched = sum(any(keyword in text for keyword in group) for group in truth.keywords)
    conclusive = report.get("status") == "conclusive"
    service_correct = report.get("affected_service") == truth.service
    root_cause_correct = conclusive and service_correct and matched == len(truth.keywords)

    cited = [c["evidence_id"] for c in report.get("evidence", [])]
    dropped = list(report.get("dropped_citations", []))
    key = set(truth.key_evidence_ids)
    recall = len(key & set(cited)) / len(key) if key else 0.0
    # A conclusive claim is unsupported if it cites nothing valid, or cited evidence no tool returned.
    unsupported = conclusive and (not cited or bool(dropped))
    citation_validity = len(cited) / (len(cited) + len(dropped)) if cited or dropped else 0.0

    score = (
        0.5 * root_cause_correct
        + 0.2 * service_correct
        + 0.2 * recall
        + 0.1 * citation_validity
    )
    return Score(True, conclusive, service_correct, matched, len(truth.keywords), root_cause_correct,
                 len(cited), len(dropped), round(recall, 3), unsupported, round(score, 3))

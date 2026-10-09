"""Verdict decision rule — the single place a conclusion is drawn (G5/G6).

``decide`` is a pure function of the evidence list, the coverage record and
the grade. The rules are evaluated strictly in order; the first that fires
wins. Numbers never decide on their own: a statistical item participates
only when it carries a calibration id, the corpus it was measured on, and a
probability at or above the profile threshold.

1. grade == reference                                   -> undetermined
2. any deterministic + synthetic + strong item          -> manipulation_evidence
   (kept even when some check failed; the failure stays in coverage)
3. any coverage entry with status failed                -> undetermined
4. any calibrated statistical synthetic item whose
   probability >= its profile threshold (a calibration
   id without one never fires)                          -> manipulation_evidence
5. any deterministic + authentic + strong item, and no
   synthetic-direction item other than lexical ones     -> authenticity_evidence
6. otherwise                                            -> undetermined
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .result_types import (
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceStrength,
    Grade,
    Verdict,
)

def _threshold_for(item: EvidenceItem, thresholds: Mapping[str, float] | None) -> float | None:
    """The profile threshold for the item's calibration id, or None (G8).

    Round 5: there is no fallback cut any more (the former 0.5 default let
    rule 4 fire for a calibration id no profile had measured a threshold
    for). Thresholds come from the loaded profiles' ``threshold`` via
    ``model_adapter.profile_probability_thresholds``.
    """
    if thresholds and item.calibration_id and item.calibration_id in thresholds:
        return float(thresholds[item.calibration_id])
    return None


# Korean description of each rule, for ``deepfake-lens explain`` (D1).
RULE_DESCRIPTIONS = {
    1: "규칙 1 — 참고 등급 결과(텍스트 등)는 결론을 내리지 않습니다 → 판단 불가",
    2: "규칙 2 — 결정적·생성 방향·강함 근거가 있습니다 → 조작·생성 근거 있음",
    3: "규칙 3 — 실패한 검사가 있어 결론을 유보합니다(fail-closed) → 판단 불가",
    4: "규칙 4 — 보정·측정된 통계 근거가 임계값 이상입니다 → 조작·생성 근거 있음",
    5: "규칙 5 — 결정적·원본 방향·강함 근거가 있고 반대 방향 근거가 없습니다 → 원본성 근거 있음",
    6: "규칙 6 — 결론을 뒷받침할 근거가 없습니다 → 판단 불가",
}


def decide(
    evidence: Iterable[EvidenceItem],
    coverage: Iterable[CoverageEntry],
    grade: Grade,
    thresholds: Mapping[str, float] | None = None,
) -> Verdict:
    """Return the verdict for one file. Pure: no I/O, no globals."""
    return decide_with_rule(evidence, coverage, grade, thresholds)[0]


def decide_with_rule(
    evidence: Iterable[EvidenceItem],
    coverage: Iterable[CoverageEntry],
    grade: Grade,
    thresholds: Mapping[str, float] | None = None,
) -> tuple[Verdict, int]:
    """``decide`` plus the number of the rule that fired (1-6)."""
    items = list(evidence)
    entries = list(coverage)

    # Rule 1 — reference-grade results (all text) never conclude.
    if grade == Grade.REFERENCE:
        return Verdict.UNDETERMINED, 1

    # Rule 2 — strong deterministic synthetic evidence stands on its own.
    if any(
        item.kind == EvidenceKind.DETERMINISTIC
        and item.direction == EvidenceDirection.SYNTHETIC
        and item.strength == EvidenceStrength.STRONG
        for item in items
    ):
        return Verdict.MANIPULATION_EVIDENCE, 2

    # Rule 3 — fail closed: a crashed check means we do not know.
    if any(entry.status == CoverageStatus.FAILED for entry in entries):
        return Verdict.UNDETERMINED, 3

    # Rule 4 — calibrated statistical evidence above its measured threshold.
    # A calibration id with no profile threshold never fires (G8).
    for item in items:
        threshold = _threshold_for(item, thresholds)
        if (
            item.kind == EvidenceKind.STATISTICAL
            and item.direction == EvidenceDirection.SYNTHETIC
            and item.calibration_id
            and item.measured_on
            and item.probability is not None
            and threshold is not None
            and item.probability >= threshold
        ):
            return Verdict.MANIPULATION_EVIDENCE, 4

    # Rule 5 — authenticity needs strong deterministic support and no
    # non-lexical evidence pointing the other way.
    has_authentic = any(
        item.kind == EvidenceKind.DETERMINISTIC
        and item.direction == EvidenceDirection.AUTHENTIC
        and item.strength == EvidenceStrength.STRONG
        for item in items
    )
    contradicted = any(
        item.direction == EvidenceDirection.SYNTHETIC and item.kind != EvidenceKind.LEXICAL
        for item in items
    )
    if has_authentic and not contradicted:
        return Verdict.AUTHENTICITY_EVIDENCE, 5

    # Rule 6.
    return Verdict.UNDETERMINED, 6

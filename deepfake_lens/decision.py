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
   probability >= its threshold                         -> manipulation_evidence
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

# Fallback decision threshold for a calibrated statistical item whose
# calibration id has no entry in ``thresholds``. 0.5 is the Bayes-optimal
# cut for a calibrated probability under equal priors and costs; profiles
# override it per calibration id once WP-I measures them.
DEFAULT_PROBABILITY_THRESHOLD = 0.5


def _threshold_for(item: EvidenceItem, thresholds: Mapping[str, float] | None) -> float:
    if thresholds and item.calibration_id and item.calibration_id in thresholds:
        return float(thresholds[item.calibration_id])
    return DEFAULT_PROBABILITY_THRESHOLD


def decide(
    evidence: Iterable[EvidenceItem],
    coverage: Iterable[CoverageEntry],
    grade: Grade,
    thresholds: Mapping[str, float] | None = None,
) -> Verdict:
    """Return the verdict for one file. Pure: no I/O, no globals."""
    items = list(evidence)
    entries = list(coverage)

    # Rule 1 — reference-grade results (all text) never conclude.
    if grade == Grade.REFERENCE:
        return Verdict.UNDETERMINED

    # Rule 2 — strong deterministic synthetic evidence stands on its own.
    if any(
        item.kind == EvidenceKind.DETERMINISTIC
        and item.direction == EvidenceDirection.SYNTHETIC
        and item.strength == EvidenceStrength.STRONG
        for item in items
    ):
        return Verdict.MANIPULATION_EVIDENCE

    # Rule 3 — fail closed: a crashed check means we do not know.
    if any(entry.status == CoverageStatus.FAILED for entry in entries):
        return Verdict.UNDETERMINED

    # Rule 4 — calibrated statistical evidence above its measured threshold.
    for item in items:
        if (
            item.kind == EvidenceKind.STATISTICAL
            and item.direction == EvidenceDirection.SYNTHETIC
            and item.calibration_id
            and item.measured_on
            and item.probability is not None
            and item.probability >= _threshold_for(item, thresholds)
        ):
            return Verdict.MANIPULATION_EVIDENCE

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
        return Verdict.AUTHENTICITY_EVIDENCE

    # Rule 6.
    return Verdict.UNDETERMINED

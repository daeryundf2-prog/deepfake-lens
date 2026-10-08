"""Shared Korean wording for verdict, evidence and coverage (contract v2).

CLI tables, HTML/PDF reports and the evidence statement all render a
result through these helpers so the three verdicts, the
deterministic/statistical/lexical split and the coverage gaps read the
same everywhere (G5/G6/G12/G24).
"""

from __future__ import annotations

from .result_types import (
    EVIDENCE_DIRECTION_LABELS,
    EVIDENCE_KIND_LABELS,
    EVIDENCE_STRENGTH_LABELS,
    VERDICT_LABELS,
    BatchScanSummary,
    ClassificationResult,
    CoverageEntry,
    CoverageStatus,
    EvidenceItem,
    EvidenceKind,
    Grade,
    Verdict,
)

# Stated first on every text result and in every report that contains one.
TEXT_LEGAL_LIMITATION = "텍스트 생성 여부 판별은 2026년 현재 증거능력이 없으며 참고 정보입니다."

KIND_ORDER = (EvidenceKind.DETERMINISTIC, EvidenceKind.STATISTICAL, EvidenceKind.LEXICAL)

# Short ASCII codes for Latin-1-only outputs (minimal PDF).
VERDICT_CODES_ASCII = {
    Verdict.MANIPULATION_EVIDENCE: "MANIPULATION-EVIDENCE",
    Verdict.AUTHENTICITY_EVIDENCE: "AUTHENTICITY-EVIDENCE",
    Verdict.UNDETERMINED: "UNDETERMINED",
}


def verdict_heading(result: ClassificationResult) -> str:
    """e.g. "조작·생성 근거 있음 [감정 근거로 사용 가능]"."""
    return f"{VERDICT_LABELS[result.verdict_code]} [{result.grade_label}]"


def evidence_line(item: EvidenceItem) -> str:
    qualifiers = f"{EVIDENCE_DIRECTION_LABELS[item.direction]}·{EVIDENCE_STRENGTH_LABELS[item.strength]}"
    if item.probability is not None and item.calibration_id:
        ci = f", 95% CI {item.probability_ci[0]:.2f}–{item.probability_ci[1]:.2f}" if item.probability_ci else ""
        qualifiers += f", p={item.probability:.2f}{ci}, 보정 {item.calibration_id}"
    return f"{item.title} ({qualifiers}) — {item.detail}"


def evidence_groups(result: ClassificationResult) -> list[tuple[str, list[str]]]:
    """Evidence grouped by kind, in fixed order; empty kinds omitted."""
    groups: list[tuple[str, list[str]]] = []
    for kind in KIND_ORDER:
        lines = [evidence_line(item) for item in result.evidence if item.kind == kind]
        if lines:
            groups.append((EVIDENCE_KIND_LABELS[kind], lines))
    return groups


def evidence_counts(result: ClassificationResult) -> dict[EvidenceKind, int]:
    return {kind: sum(1 for item in result.evidence if item.kind == kind) for kind in KIND_ORDER}


def evidence_counts_text(result: ClassificationResult) -> str:
    counts = evidence_counts(result)
    return f"결정 {counts[EvidenceKind.DETERMINISTIC]}·통계 {counts[EvidenceKind.STATISTICAL]}·어휘 {counts[EvidenceKind.LEXICAL]}"


def deciding_evidence(result: ClassificationResult) -> EvidenceItem | None:
    """The first evidence item worth naming next to the verdict."""
    return result.evidence[0] if result.evidence else None


def coverage_gaps(result: ClassificationResult) -> list[CoverageEntry]:
    """Failed entries first, then skipped ones."""
    failed = [entry for entry in result.coverage if entry.status == CoverageStatus.FAILED]
    skipped = [entry for entry in result.coverage if entry.status == CoverageStatus.SKIPPED]
    return failed + skipped


def coverage_lines(result: ClassificationResult) -> list[str]:
    return [entry.describe() for entry in result.coverage]


def coverage_counts_text(result: ClassificationResult) -> str:
    ran = sum(1 for entry in result.coverage if entry.status == CoverageStatus.RAN)
    skipped = sum(1 for entry in result.coverage if entry.status == CoverageStatus.SKIPPED)
    failed = sum(1 for entry in result.coverage if entry.status == CoverageStatus.FAILED)
    return f"실행 {ran}·미실행 {skipped}·실패 {failed}"


def leading_limitations(result: ClassificationResult) -> list[str]:
    """Limitations with the fixed legal sentence first for reference grade."""
    if result.grade != Grade.REFERENCE:
        return list(result.limitations)
    return [TEXT_LEGAL_LIMITATION, *(lim for lim in result.limitations if lim != TEXT_LEGAL_LIMITATION)]


def summary_line(summary: BatchScanSummary) -> str:
    return (
        f"총 {summary.total}건 — 조작·생성 근거 있음 {summary.manipulation_evidence}건, "
        f"원본성 근거 있음 {summary.authenticity_evidence}건, 판단 불가 {summary.undetermined}건"
        f"(검사 실패 포함 {summary.checks_failed}건), 미지원/분석 실패 {summary.unsupported_or_failed}건, "
        f"중복 {summary.duplicates}건, 건너뜀 {summary.skipped}건"
    )


def summary_line_ascii(summary: BatchScanSummary) -> str:
    return (
        f"Scanned {summary.total} files: manipulation_evidence={summary.manipulation_evidence}, "
        f"authenticity_evidence={summary.authenticity_evidence}, undetermined={summary.undetermined} "
        f"(with failed checks={summary.checks_failed}), unsupported/failed={summary.unsupported_or_failed}, "
        f"duplicates={summary.duplicates}, skipped={summary.skipped}, cached={summary.cached}"
    )

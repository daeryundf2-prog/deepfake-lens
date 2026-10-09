"""Shared Korean wording for verdict, evidence and coverage (contract v2).

CLI tables, HTML/PDF reports and the evidence statement all render a
result through these helpers so the three verdicts, the
deterministic/statistical/lexical split and the coverage gaps read the
same everywhere (G5/G6/G12/G24).
"""

from __future__ import annotations

from pathlib import PurePath

from .result_types import (
    COVERAGE_STATUS_LABELS,
    GRADE_LABELS,
    EVIDENCE_DIRECTION_LABELS,
    EVIDENCE_KIND_LABELS,
    EVIDENCE_STRENGTH_LABELS,
    VERDICT_LABELS,
    BatchScanSummary,
    ClassificationResult,
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    Grade,
    check_label,
)

# Stated first on every text result and in every report that contains one.
TEXT_LEGAL_LIMITATION = "텍스트 생성 여부 판별은 2026년 현재 증거능력이 없으며 참고 정보입니다."

KIND_ORDER = (EvidenceKind.DETERMINISTIC, EvidenceKind.STATISTICAL, EvidenceKind.LEXICAL)


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
    # N5: archive container rows are counted by their verdict (R5); the
    # header says how many of the rows are such roll-up rows.
    containers = f"(압축 파일 {summary.container_rows}건 포함)" if summary.container_rows else ""
    return (
        f"총 {summary.total}건{containers} — 조작·생성 근거 있음 {summary.manipulation_evidence}건, "
        f"원본성 근거 있음 {summary.authenticity_evidence}건, 판단 불가 {summary.undetermined}건"
        f"(검사 실패 포함 {summary.checks_failed}건), 미지원/분석 실패 {summary.unsupported_or_failed}건, "
        f"중복 {summary.duplicates}건, 건너뜀 {summary.skipped}건"
    )


# B4: compact Korean evidence qualifiers for one-line renderings (legal
# report): "[결정적/합성/강]". Keyed by the JSON values so rows read back
# from a report body render the same as live results.
EVIDENCE_KIND_SHORT = {
    EvidenceKind.DETERMINISTIC.value: "결정적",
    EvidenceKind.STATISTICAL.value: "통계적",
    EvidenceKind.LEXICAL.value: "어휘적",
}
EVIDENCE_DIRECTION_SHORT = {
    EvidenceDirection.SYNTHETIC.value: "합성",
    EvidenceDirection.AUTHENTIC.value: "원본",
    EvidenceDirection.NEUTRAL.value: "중립",
}
EVIDENCE_STRENGTH_SHORT = {strength.value: label for strength, label in EVIDENCE_STRENGTH_LABELS.items()}


def evidence_qualifiers_short(kind: object, direction: object, strength: object) -> str:
    """e.g. "결정적/합성/강" from the JSON values (unknown values kept as is)."""
    return "/".join((
        EVIDENCE_KIND_SHORT.get(str(kind), str(kind)),
        EVIDENCE_DIRECTION_SHORT.get(str(direction), str(direction)),
        EVIDENCE_STRENGTH_SHORT.get(str(strength), str(strength)),
    ))


def grade_label_text(grade: object) -> str:
    """Korean label of a grade value ("reference" -> "참고"; G1)."""
    for key, label in GRADE_LABELS.items():
        if key.value == str(grade):
            return label
    return GRADE_LABELS[Grade.REFERENCE] if grade is None else str(grade)


def coverage_status_label(status: object) -> str:
    """Korean label of a coverage status value ("ran" -> "실행"; B3)."""
    for key, label in COVERAGE_STATUS_LABELS.items():
        if key.value == str(status):
            return label
    return str(status)


def coverage_entry_line(entry: dict[str, object]) -> str:
    """Korean line for a coverage entry read from JSON: "얼굴 검사: 미실행 — 얼굴 미검출"."""
    reason = f" — {entry['reason']}" if entry.get("reason") else ""
    return f"{check_label(str(entry.get('check', '')))}: {coverage_status_label(entry.get('status'))}{reason}"


# S1: archive members are rows "<container>::<member>". Every rendering
# names them in full so the container is never lost and a reference such as
# "evil.zip::inner/a.png" points at a row that exists.
ARCHIVE_MEMBER_SEPARATOR = "::"


def display_path(path: str, *, redact_paths: bool = False) -> str:
    """The row path as shown in a report.

    ``redact_paths`` keeps only the container's file name — a member keeps
    its path inside the archive ("evil.zip::inner/a1111.png"), which is not
    a path of the examiner's machine.
    """
    if ARCHIVE_MEMBER_SEPARATOR in path:
        container, member = path.split(ARCHIVE_MEMBER_SEPARATOR, 1)
        shown = PurePath(container).name if redact_paths else container
        return f"{shown}{ARCHIVE_MEMBER_SEPARATOR}{member}"
    return PurePath(path).name if redact_paths else path


def row_label(path: str) -> str:
    """Short row name: the file name, or "<container name>::<member path>" (S1)."""
    return display_path(path, redact_paths=True)


# Why a row has no SHA-256 (N2, S2) — the same wording in the evidence
# statement and the forensic PDF.
HASH_UNAVAILABLE_ACCESS = "해시 불가 — 원본 파일 접근 실패 (동일성 확인 요망)"
HASH_UNAVAILABLE_SYMLINK = "해시 불가(심볼릭 링크 — 링크를 따라가지 않음)"
HASH_UNAVAILABLE_MEMBER = "해시 불가(압축 파일 구성원 — 압축 파일 행의 해시로 동일성을 확인하십시오)"
SYMLINK_ROW_ERROR_PREFIX = "심볼릭 링크"


def is_symlink_row(status: object, error: object) -> bool:
    """A row the scan skipped because it is a symbolic link (never followed)."""
    return str(status) == "skipped" and str(error or "").startswith(SYMLINK_ROW_ERROR_PREFIX)


# S6/B2: the in-sample caveat printed wherever threshold provenance is
# shown (CLI header, HTML, forensic PDF, evidence statement, GUI).
IN_SAMPLE_CAVEAT = "적합에 쓴 같은 표본에서 평가된 값이라 감정 근거가 아닙니다"


def threshold_provenance_lines(thresholds: object | None) -> list[str]:
    """Korean threshold provenance shared by every report (B2, S6, N17).

    Accepts a ThresholdProfile, its ``to_json()`` dict or the scan JSON's
    ``thresholds`` block (``core._thresholds_json``). N17: an in-sample
    profile is labelled in-sample — never "측정됨" — and its caveat is a line
    of its own (the forensic PDF printed "측정됨 … — in-sample(참고): …" on one
    line, two contradicting states side by side).
    """
    from .calibration import IN_SAMPLE_LABEL

    to_json = getattr(thresholds, "to_json", None)
    payload = to_json() if callable(to_json) else (thresholds if isinstance(thresholds, dict) else {})
    if thresholds is None or not isinstance(payload, dict) or payload.get("source") == "builtin_defaults":
        return ["판정 임계값: 내장 기본값(미측정 — 잠정값, 보정 미적용)"]
    samples = int(payload.get("samples", 0) or 0)
    if payload.get("in_sample"):
        state = IN_SAMPLE_LABEL  # G28: fitted on the evaluated sample — not a measurement
    else:
        state = "잠정(미검증)" if payload.get("provisional", True) else "측정됨"
    version = str(payload.get("version", "") or "").strip()
    fingerprint = str(payload.get("dataset_fingerprint", "") or "")[:16]
    line = f"판정 임계값: 프로필 {version or '버전 미기재'} — {state}, 표본 n={samples}"
    if fingerprint:
        line += f", 코퍼스 지문 {fingerprint}"
    lines = [line]
    if payload.get("in_sample"):
        lines.append(f"{IN_SAMPLE_LABEL}: {IN_SAMPLE_CAVEAT}")  # G28
    return lines


def threshold_provenance_line(thresholds: object | None) -> str:
    """:func:`threshold_provenance_lines` joined with newlines (text renderers)."""
    return "\n".join(threshold_provenance_lines(thresholds))


# Korean names of the scan row kinds (CLI table, CSV kind_label, reports).
# N8: every kind a row can carry has a label — "unsupported" and "duplicate"
# were printed raw in the scan table's type column.
ITEM_KIND_LABELS = {
    "image": "이미지",
    "video": "영상",
    "audio": "오디오",
    "text": "텍스트",
    "document": "문서",
    "archive": "압축",
    "unsupported": "미지원 형식",
    "duplicate": "중복",
    "unknown": "알 수 없음",
}
# A kind outside the table (a row from a newer tool version) is never shown raw.
ITEM_KIND_FALLBACK = "기타"


def item_kind_label(kind: object) -> str:
    return ITEM_KIND_LABELS.get(str(kind), ITEM_KIND_FALLBACK)

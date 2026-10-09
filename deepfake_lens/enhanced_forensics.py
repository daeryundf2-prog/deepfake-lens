"""Enhanced forensics module for legal evidence.

Provides comprehensive forensic analysis for legal and evidentiary use.

``build_legal_report`` is what the ``legal-report`` CLI command prints (D4):
it is built from the scan result (``analysis_api.analyze_path``) — the same
verdict, evidence and coverage as ``scan`` — plus the file hash, the
package tool version and an HMAC signature when a report key is set.

``analyze_forensic`` is the older provenance-only packaging (hashing,
report ID, legal text, checksum) kept for library callers. Its evidence
collection delegates to the canonical provenance path in ``c2pa.py``
(``analyze_metadata_forensic``), so a raw ``b"c2pa"`` substring is a
reference-level hint — never the 0.9 confidence that only an
SDK-validated manifest earns. See ``docs/consolidation-notes.md``.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from .report_time import REPORT_ID_STAMP, report_moment, report_timestamp
from pathlib import Path
from typing import Any

from .c2pa import MetadataForensicAnalysis, ProvenanceRecord, analyze_metadata_forensic
from .layer_diagnostic import UNAVAILABLE_BAND


@dataclass(frozen=True)
class ForensicEvidence:
    evidence_type: str
    description: str
    confidence: float
    details: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ForensicReport:
    file_path: str
    file_hash: str
    file_size: int
    analysis_timestamp: str
    evidences: list[ForensicEvidence]
    overall_confidence: float
    legal_notes: list[str]
    # Legal elements
    analyst_id: str = "system"
    tool_version: str = ""
    jurisdiction: str = "KR"
    # Checksum of the report contents. This is NOT a digital signature: an
    # unkeyed SHA-256 provides integrity binding for the report text only,
    # and cannot prove authorship. A real signature requires a key pair.
    integrity_checksum: str = ""
    checksum_timestamp: str = ""
    # Standardized report format
    report_format_version: str = "1.0"
    report_id: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
    
    def generate_legal_text(self) -> str:
        """Generate legal-style report text."""
        lines = [
            "=== 포렌식 분석 보고서 ===",
            f"보고서 ID: {self.report_id}",
            f"분석 일시: {self.analysis_timestamp}",
            f"분석자: {analyst_label(self.analyst_id)}",
            f"도구 버전: {self.tool_version}",
            f"관할권: {self.jurisdiction}",
            "",
            "=== 파일 정보 ===",
            f"파일 경로: {self.file_path}",
            f"파일 해시 (SHA-256): {self.file_hash}",
            f"파일 크기: {self.file_size} bytes",
            "",
            "=== 발견된 증거 ===",
        ]
        
        for i, evidence in enumerate(self.evidences, 1):
            lines.append(f"{i}. {evidence.evidence_type}: {evidence.description}")
            lines.append(f"   신뢰도: {evidence.confidence:.2f}")
            lines.append(f"   상세: {evidence.details}")
        
        lines.extend([
            "",
            "=== 종합 판단 ===",
            f"전체 신뢰도: {self.overall_confidence:.2f}",
            "",
            "=== 법적 참고사항 ===",
        ])
        
        for note in self.legal_notes:
            lines.append(f"- {note}")
        
        lines.extend([
            "",
            "=== 무결성 체크섬 ===",
            f"체크섬 (보고서 내용 기반 SHA-256, 전자서명 아님): {self.integrity_checksum[:32]}...",
            f"생성 일시: {self.checksum_timestamp}",
            f"파일 해시 검증: SHA-256 ({self.file_hash[:16]}...)",
        ])

        return "\n".join(lines)


# G16 (round 5): the default analyst id "system" means "no named analyst —
# produced automatically"; the text renderings say so in Korean (the JSON
# keeps the id).
SYSTEM_ANALYST_ID = "system"
SYSTEM_ANALYST_LABEL = "시스템(자동)"


def analyst_label(analyst_id: object) -> str:
    text = str(analyst_id or "").strip()
    return SYSTEM_ANALYST_LABEL if text in ("", SYSTEM_ANALYST_ID) else text

def analyze_forensic(path: Path | str) -> ForensicReport:
    """Perform comprehensive forensic analysis."""
    file_path = Path(path)
    if not file_path.is_file():
        return _error_report(f"파일이 존재하지 않습니다: {file_path}")

    # Calculate file hash
    file_hash = _calculate_hash(file_path)
    
    # Get file info
    try:
        file_size = file_path.stat().st_size
    except OSError:
        file_size = 0

    evidences: list[ForensicEvidence] = []
    legal_notes: list[str] = []

    # Provenance/metadata evidence comes from the canonical SDK-first scan
    # in c2pa.py: a single calibration point, the 256 MB read cap, and the
    # official SDK validator when the `provenance` extra is installed.
    forensic = analyze_metadata_forensic(file_path)
    evidences.extend(_evidence_from_forensic_analysis(forensic))
    if forensic.reference_band == UNAVAILABLE_BAND:
        legal_notes.append(f"출처 메타데이터 분석을 수행하지 못했습니다: {forensic.reference_note}")
    else:
        legal_notes.extend(forensic.limitations)

    # File-structure checks not covered by the provenance path.
    structure_evidence = _analyze_structure(file_path)
    if structure_evidence:
        evidences.extend(structure_evidence)

    # Calculate overall confidence
    if evidences:
        overall_confidence = sum(e.confidence for e in evidences) / len(evidences)
    else:
        overall_confidence = 0.0

    # Legal notes
    legal_notes.append("이 분석은 로컬 휴리스틱 기반입니다.")
    legal_notes.append("법적 효력을 위해서는 공인된 검증 기관의 확인이 필요합니다.")
    legal_notes.append("파일 무결성은 SHA-256 해시로 검증되었습니다.")

    # Generate report ID and content checksum
    moment = report_moment()  # R12-9: one aware moment for the id and the timestamps
    report_id = f"FR-{moment.strftime(REPORT_ID_STAMP)}-{file_hash[:8]}"
    checksum_timestamp = report_timestamp(moment)
    checksum_data = f"{file_hash}:{checksum_timestamp}:{package_version()}"
    integrity_checksum = hashlib.sha256(checksum_data.encode()).hexdigest()

    return ForensicReport(
        file_path=str(file_path.absolute()),
        file_hash=file_hash,
        file_size=file_size,
        analysis_timestamp=report_timestamp(moment),
        evidences=evidences,
        overall_confidence=overall_confidence,
        legal_notes=legal_notes,
        analyst_id="system",
        tool_version=package_version(),
        jurisdiction="KR",
        integrity_checksum=integrity_checksum,
        checksum_timestamp=checksum_timestamp,
        report_format_version="1.0",
        report_id=report_id,
    )


def _error_report(message: str) -> ForensicReport:
    return ForensicReport(
        file_path="",
        file_hash="",
        file_size=0,
        analysis_timestamp=report_timestamp(),
        evidences=[],
        overall_confidence=0.0,
        legal_notes=[message],
        analyst_id="system",
        tool_version=package_version(),
        jurisdiction="KR",
        integrity_checksum="",
        checksum_timestamp="",
        report_format_version="1.0",
        report_id="",
    )


def _calculate_hash(path: Path) -> str:
    """Calculate SHA-256 hash of file."""
    sha256 = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except OSError:
        return ""


# Signals produced by c2pa.py that have no companion ProvenanceRecord —
# every other signal is already represented by a translated record, so
# reporting it again would double-count the same finding.
_SIGNAL_ONLY_EVIDENCE = {
    "PNG 출처 청크": ("png_metadata", 0.3),
    "EXIF 메타데이터": ("exif_metadata", 0.5),
}


def _evidence_from_forensic_analysis(analysis: MetadataForensicAnalysis) -> list[ForensicEvidence]:
    """Translate the canonical provenance scan into report evidence."""
    evidences = [_evidence_from_record(record) for record in analysis.provenance_records]
    for signal in analysis.signals:
        mapped = _SIGNAL_ONLY_EVIDENCE.get(signal.title)
        if mapped is None:
            continue
        evidence_type, confidence = mapped
        evidences.append(
            ForensicEvidence(
                evidence_type=evidence_type,
                description=f"{signal.title}: {signal.detail}",
                confidence=confidence,
                details={"signal": signal.title, "detail": signal.detail},
            )
        )
    return evidences


def _evidence_from_record(record: ProvenanceRecord) -> ForensicEvidence:
    """Map one ProvenanceRecord to ForensicEvidence with honest confidence.

    Only an SDK-validated C2PA manifest earns 0.9. A manifest the SDK read
    but could not fully validate (e.g. untrusted signer) is 0.5, and a
    bare byte-marker match without the SDK is a 0.3 hint — the old local
    scan scored that substring 0.9, which outranked verified manifests.
    """
    details: dict[str, Any] = {
        "standard": record.standard,
        "provider": record.provider,
        "signed": record.signed,
        "claim_url": record.claim_url,
        **record.details,
    }
    if record.standard == "C2PA":
        if record.details.get("present"):
            if str(record.details.get("state", "")).lower() == "valid":
                return ForensicEvidence(
                    evidence_type="c2pa_manifest",
                    description="C2PA 매니페스트가 공식 SDK로 검증되었습니다.",
                    confidence=0.9,
                    details=details,
                )
            return ForensicEvidence(
                evidence_type="c2pa_manifest",
                description="C2PA 매니페스트가 감지되었지만 SDK 검증이 완료되지 않았습니다(신뢰 저장소에 없는 서명자일 수 있습니다).",
                confidence=0.5,
                details=details,
            )
        return ForensicEvidence(
            evidence_type="c2pa_marker",
            description="C2PA 관련 문자열이 발견되었습니다(매니페스트 검증이 아닌 문자열 탐지입니다).",
            confidence=0.3,
            details=details,
        )
    if record.standard == "ExifTool":
        return ForensicEvidence(
            evidence_type="exiftool_metadata",
            description="ExifTool 형식 메타데이터 임베딩이 발견되었습니다.",
            confidence=0.4,
            details=details,
        )
    return ForensicEvidence(
        evidence_type="provenance_marker",
        description=f"{record.provider} 생성 도구 식별 문자열입니다(암호학적 워터마크 검증이 아닙니다).",
        confidence=0.4,
        details=details,
    )


def _analyze_structure(path: Path) -> list[ForensicEvidence]:
    """Structural checks the provenance path does not cover (size, gzip).

    Not a duplicate of c2pa.py: those findings describe file shape, not
    provenance markers.
    """
    evidences = []

    try:
        size = path.stat().st_size
        if size < 100:
            evidences.append(ForensicEvidence(
                evidence_type="file_size",
                description="비정상적으로 작은 파일",
                confidence=0.3,
                details={"size": size},
            ))

        # Only the magic bytes are needed — no full-file read here; the
        # provenance path above already enforces MAX_FORENSIC_FILE_BYTES.
        with path.open("rb") as handle:
            magic = handle.read(2)
        if magic == b"\x1f\x8b":  # gzip
            evidences.append(ForensicEvidence(
                evidence_type="compression",
                description="gzip 압축 파일",
                confidence=0.4,
                details={"format": "gzip"},
            ))
    except Exception:
        pass

    return evidences


def package_version() -> str:
    """The deepfake-lens package version (pyproject), for every report."""
    from .core import TOOL_VERSION

    return TOOL_VERSION


LEGAL_REPORT_TYPE = "legal-report"
LEGAL_REPORT_FORMAT_VERSION = "2.0"
LEGAL_REPORT_NOTES = (
    "결론은 scan과 같은 결정 규칙(decision.decide)으로 산출되었습니다: 결정적 근거(메타데이터·C2PA)만 결론을 바꿀 수 있고, "
    "측정되지 않은 통계·휴리스틱 신호는 참고 신호로만 기록됩니다.",
    "'판단 불가'는 원본이라는 뜻이 아닙니다. 검사 범위(coverage)에 실행·건너뜀·실패한 검사가 모두 기록되어 있습니다.",
    "법적 효력을 위해서는 공인된 검증 기관의 확인이 필요합니다.",
    "파일 무결성은 SHA-256 해시로 기록되었으며, 보고서 서명(HMAC-SHA256)은 서명 키 보유자에 대한 무결성 증명입니다.",
)


def build_legal_report(
    path: Path | str,
    options: Any = None,
    *,
    analyst_id: str = "system",
    jurisdiction: str = "KR",
    key: bytes | None = None,
) -> dict[str, Any]:
    """Legal report for one file, built from the scan result (D4).

    The conclusion, evidence list and coverage are exactly what
    ``analysis_api.analyze_rows`` returns for the file — an a1111 PNG shows
    its deterministic generator-metadata evidence and
    ``manipulation_evidence``. B1: an archive is expanded like ``scan``
    expands it; the conclusion is its container row and ``rows`` carries
    every raw scan row (members and container) inside the signed body. The
    body is signed with ``key`` (HMAC-SHA256) when one is given, otherwise
    it carries the "서명 없음" note; verify it with
    ``deepfake-lens verify-report``.
    """
    from .analysis_api import AnalysisOptions, load_thresholds, provenance
    from .cli_standalone import analysis_result_for_path
    from .result_text import unrecorded_files
    from .signing import sign_report

    file_path = Path(path)
    opts = options if options is not None else AnalysisOptions()
    thresholds = load_thresholds(opts)
    result, _ = analysis_result_for_path(file_path, opts, command=LEGAL_REPORT_TYPE, thresholds=thresholds)
    try:
        # G6: a symbolic link is reported as itself (lstat), never its target.
        size = file_path.lstat().st_size if file_path.is_symlink() else file_path.stat().st_size
    except OSError:
        size = 0
    now = report_moment()  # R12-9: ISO 8601 with the UTC offset
    sha = str(result.get("sha256") or "")
    report: dict[str, Any] = {
        "report_type": LEGAL_REPORT_TYPE,
        "report_format_version": LEGAL_REPORT_FORMAT_VERSION,
        "report_id": f"LR-{now.strftime(REPORT_ID_STAMP)}-{sha[:8] or 'nohash'}",
        "generated_at": report_timestamp(now),
        "analyst_id": analyst_id,
        "jurisdiction": jurisdiction,
        "tool_version": package_version(),
        "file": {
            "path": str(file_path.absolute()),
            "sha256": sha or None,
            "size_bytes": size,
            "kind": result.get("file_kind"),
            "status": result.get("status"),
        },
        "conclusion": {
            "verdict_code": result.get("verdict_code"),
            "verdict_label": result.get("verdict_label"),
            "verdict": result.get("verdict"),
            "grade": result.get("grade"),
            "grade_label": result.get("grade_label"),
        },
        "evidence": result.get("evidence", []),
        "coverage": result.get("coverage", []),
        "limitations": result.get("limitations", []),
        "reference_signals": result.get("reference_signals", []),
        # B1: the scan rows for the file (an archive: member rows + container row).
        "rows": result.get("rows", []),
        # X1: the "기록되지 않은 파일" section (a single file: its own row —
        # a symlink, unsupported or skipped file has no analysis result).
        "unrecorded_files": unrecorded_files(list(result.get("rows", []) or [])).to_json(),
        "provenance": provenance(opts, thresholds),
        "legal_notes": list(LEGAL_REPORT_NOTES),
    }
    return sign_report(report, key)


def legal_report_text(report: dict[str, Any]) -> str:
    """Korean plain-text rendering of :func:`build_legal_report`.

    B4: evidence qualifiers ("[결정적/합성/강]"), coverage statuses
    (실행/미실행/실패) and check names (CHECK_LABELS, model display names)
    are Korean; the JSON body keeps the raw codes.
    """
    from .result_text import coverage_entry_line, display_name, escape_controls, evidence_qualifiers_short

    raw_file, raw_conclusion = report.get("file"), report.get("conclusion")
    file_info: dict[str, Any] = raw_file if isinstance(raw_file, dict) else {}
    conclusion: dict[str, Any] = raw_conclusion if isinstance(raw_conclusion, dict) else {}
    lines = [
        "=== 포렌식 분석 보고서 ===",
        f"보고서 ID: {report.get('report_id', '')}",
        f"분석 일시: {report.get('generated_at', '')}",
        f"분석자: {analyst_label(report.get('analyst_id', ''))}",
        f"도구 버전: {report.get('tool_version', '')}",
        f"관할권: {report.get('jurisdiction', '')}",
        "",
        "=== 파일 정보 ===",
        f"파일 경로: {display_name(file_info.get('path', ''))}",
        f"파일 해시 (SHA-256): {file_info.get('sha256') or '해시 불가'}",
        f"파일 크기: {file_info.get('size_bytes', 0)} 바이트",
        "",
        "=== 결론 ===",
        f"{conclusion.get('verdict_label', '')} · 등급: {conclusion.get('grade_label', '')}",
        str(conclusion.get("verdict", "")),
        "",
        "=== 근거 ===",
    ]
    raw_evidence = report.get("evidence")
    evidence: list[Any] = raw_evidence if isinstance(raw_evidence, list) else []
    if not evidence:
        lines.append("(기록된 근거 없음)")
    for index, item in enumerate(evidence, 1):
        if isinstance(item, dict):
            lines.append(
                f"{index}. [{evidence_qualifiers_short(item.get('kind'), item.get('direction'), item.get('strength'))}] "
                f"{escape_controls(item.get('title'))}: {escape_controls(item.get('detail'))}"
            )
    lines.extend(["", "=== 검사 범위 ==="])
    for entry in report.get("coverage") or []:
        if isinstance(entry, dict):
            lines.append(f"- {escape_controls(coverage_entry_line(entry))}")
    lines.extend(["", "=== 한계 ==="])
    lines.extend(f"- {escape_controls(item)}" for item in report.get("limitations") or [])
    # X1: the "기록되지 않은 파일" section, always present.
    from .result_text import UNRECORDED_SECTION_TITLE, UnrecordedFiles

    unrecorded = UnrecordedFiles.from_json(report.get("unrecorded_files")) or UnrecordedFiles({})
    lines.extend(["", f"=== {UNRECORDED_SECTION_TITLE} ===", unrecorded.headline()])
    lines.extend(f"- {line}" for line in unrecorded.reason_lines())
    lines.extend(["", "=== 법적 참고사항 ==="])
    lines.extend(f"- {note}" for note in report.get("legal_notes") or [])
    lines.extend(["", "=== 서명 ==="])
    if report.get("signature"):
        lines.append(f"HMAC-SHA256 {report.get('signature_key_id')}: {report.get('signature')}")
    lines.append(str(report.get("signature_note", "")))
    return "\n".join(lines)


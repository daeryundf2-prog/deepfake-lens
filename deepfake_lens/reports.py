from __future__ import annotations

import base64
import hashlib
import json
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable

from .core import BatchScanSummary, ScanItem
from .evaluation_metrics import format_ci
from .result_text import (
    TEXT_LEGAL_LIMITATION,
    VERDICT_CODES_ASCII,
    coverage_gaps,
    deciding_evidence,
    evidence_counts,
    evidence_counts_text,
    evidence_groups,
    leading_limitations,
    summary_line,
    summary_line_ascii,
    verdict_heading,
)
from .result_types import EVIDENCE_KIND_LABELS, VERDICT_LABELS, CoverageStatus, EvidenceKind, Grade, Verdict
from .signing import REPORT_KEY_ENV, resolve_report_key, sign_report, signed_body_sha256


def _has_reference_grade(items: list[ScanItem]) -> bool:
    return any(item.result is not None and item.result.grade == Grade.REFERENCE for item in items)


def _threshold_provenance_line(thresholds: object | None) -> str:
    """One-line calibration provenance for report headers.

    A scan has no reason to hide that its cutoffs were the unmeasured
    builtins; a loaded profile reports its sample count so provisional
    (n < MIN_CALIBRATION_SAMPLES) fits are visibly marked unvalidated.
    """
    to_json = getattr(thresholds, "to_json", None)
    payload = to_json() if callable(to_json) else (thresholds if isinstance(thresholds, dict) else {})
    if thresholds is None or not isinstance(payload, dict) or payload.get("source") == "builtin_defaults":
        return "Decision thresholds: builtin defaults (unmeasured - provisional)."
    samples = int(payload.get("samples", 0) or 0)
    state = "PROVISIONAL (unvalidated)" if payload.get("provisional", True) else "measured"
    if payload.get("in_sample"):
        # G28: fitted on the same rows it was evaluated on.
        state += ", in-sample (reference only)"
    fp = str(payload.get("dataset_fingerprint", ""))[:16]
    suffix = f", corpus fp {fp}" if fp else ""
    return f"Decision thresholds: threshold profile {payload.get('version', '?')} — {state}, n={samples}{suffix}."


SIGNED_REPORT_SCRIPT_ID = "deepfake-lens-signed-report"


def _thresholds_payload(thresholds: object | None) -> object:
    to_json = getattr(thresholds, "to_json", None)
    if callable(to_json):
        return to_json()
    return thresholds if isinstance(thresholds, (dict, type(None))) else str(thresholds)


def build_report_body(
    summary: BatchScanSummary,
    items: list[ScanItem],
    *,
    thresholds: object | None = None,
    coverage: dict[str, object] | None = None,
    report_format: str = "html",
    redact_paths: bool = False,
) -> dict[str, object]:
    """The JSON body an HTML/PDF report renders — what its signature covers.

    Every row carries ``sha256`` exactly as the item holds it: the scanner's
    content hash, or None. Nothing is re-read here — a row path is often
    relative to a scan root this function does not know (resolving it
    against the working directory could hash the wrong file), and the web
    report hashes under the read roots before calling.
    """
    rows: list[dict[str, object]] = []
    for item in items:
        row = item.to_json()
        row["sha256"] = item.sha256
        if redact_paths:
            # The embedded/signed body must not undo --redact-paths.
            row["path"] = _display_path(item.path, redact_paths=True)
            if item.duplicate_of:
                row["duplicate_of"] = _display_path(item.duplicate_of, redact_paths=True)
        rows.append(row)
    from .core import SCAN_JSON_SCHEMA_VERSION

    return {
        "schema_version": SCAN_JSON_SCHEMA_VERSION,
        "report_format": report_format,
        "summary": summary.to_json(),
        "thresholds": _thresholds_payload(thresholds),
        "coverage": coverage,
        "items": rows,
    }


def signed_report_body(
    summary: BatchScanSummary,
    items: list[ScanItem],
    *,
    thresholds: object | None = None,
    coverage: dict[str, object] | None = None,
    report_format: str = "html",
    model_pins: list[dict[str, object]] | None = None,
    key: bytes | None = None,
    redact_paths: bool = False,
) -> dict[str, object]:
    """``build_report_body`` signed with ``key`` or DEEPFAKE_LENS_REPORT_KEY (G30).

    Without a key the body is returned with ``signature: null`` and a
    "서명 없음" note — never silently unsigned.
    """
    body = build_report_body(summary, items, thresholds=thresholds, coverage=coverage, report_format=report_format, redact_paths=redact_paths)
    return sign_report(body, key if key is not None else resolve_report_key(), model_pins=model_pins)


def signature_lines_ko(signed: dict[str, object]) -> list[str]:
    """Korean signature status lines for HTML/forensic-PDF renderings."""
    if signed.get("signature"):
        return [
            f"보고서 서명: HMAC-SHA256 서명됨 — 키 ID {signed.get('signature_key_id')}",
            f"서명값: {signed.get('signature')}",
            f"서명 본문 SHA-256: {signed_body_sha256(signed)}",
        ]
    return [
        f"보고서 서명: 서명 없음 — {REPORT_KEY_ENV}가 설정되지 않아 이 보고서는 서명되지 않았습니다.",
        f"본문 SHA-256(참고, 서명 아님): {signed_body_sha256(signed)}",
    ]


def signature_lines_ascii(signed: dict[str, object]) -> list[str]:
    """Latin-1 signature lines for the minimal PDF writer."""
    if signed.get("signature"):
        return [
            f"Signature: HMAC-SHA256, key id {signed.get('signature_key_id')}",
            f"  {signed.get('signature')}",
            f"Signed body SHA-256: {signed_body_sha256(signed)}",
        ]
    return [
        f"Signature: UNSIGNED - no report key ({REPORT_KEY_ENV} not set)",
        f"Body SHA-256 (not a signature): {signed_body_sha256(signed)}",
    ]


def extract_signed_report(html_text: str) -> dict[str, object] | None:
    """The signed JSON body embedded in an HTML report, for ``verify_report``."""
    marker = f'<script type="application/json" id="{SIGNED_REPORT_SCRIPT_ID}">'
    start = html_text.find(marker)
    if start == -1:
        return None
    end = html_text.find("</script>", start)
    if end == -1:
        return None
    try:
        loaded = json.loads(html_text[start + len(marker):end])
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _signature_html(signed: dict[str, object]) -> str:
    lines = "".join(f"<p class=\"note\">{escape(line)}</p>" for line in signature_lines_ko(signed))
    # "</" is escaped so the JSON cannot close the script element; json.loads
    # reads "<\/" back as "</", so the extracted body verifies unchanged.
    embedded = json.dumps(signed, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return (
        f'<section class="signature"><h2>보고서 서명</h2>{lines}</section>\n'
        f'<script type="application/json" id="{SIGNED_REPORT_SCRIPT_ID}">{embedded}</script>'
    )


def write_html_report(
    path: Path | str,
    summary: BatchScanSummary,
    items: list[ScanItem],
    *,
    redact_paths: bool = False,
    thresholds: object | None = None,
    allow_path: Callable[[str], bool] | None = None,
    signed_report: dict[str, object] | None = None,
) -> None:
    """Write the HTML report.

    ``allow_path`` (G31) decides which heatmap files may be read and inlined;
    a heatmap it rejects renders as a placeholder. None trusts every path
    (CLI use on the examiner's own scan). ``signed_report`` is the signed
    body to embed; when omitted it is built and signed here with
    DEEPFAKE_LENS_REPORT_KEY (G30), or marked "서명 없음".
    """
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if signed_report is None:
        signed_report = signed_report_body(summary, items, thresholds=thresholds, report_format="html", redact_paths=redact_paths)
    rows = "\n".join(_html_row(item, redact_paths=redact_paths, allow_path=allow_path) for item in items)
    legal_note = f'<p class="legal">{escape(TEXT_LEGAL_LIMITATION)}</p>' if _has_reference_grade(items) else ""
    body = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Deepfake Lens Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 32px; color: #1f2933; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #d8dee9; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f5f7fa; }}
    .note {{ color: #5f6b7a; }}
    .legal {{ font-weight: 700; color: #8a4b00; }}
    .v-manipulation_evidence {{ color: #b42318; font-weight: 700; }}
    .v-authenticity_evidence {{ color: #067647; font-weight: 700; }}
    .v-undetermined {{ color: #475467; font-weight: 700; }}
    .kind {{ font-size: 12px; font-weight: 700; color: #344054; margin-top: 4px; }}
    .gap-failed {{ color: #b42318; }}
    ul {{ margin: 2px 0 2px 16px; padding: 0; }}
    img.heatmap {{ width: 96px; height: 96px; object-fit: cover; image-rendering: pixelated; border: 1px solid #d8dee9; }}
  </style>
</head>
<body>
  <h1>Deepfake Lens Report</h1>
  <p>{escape(summary_line(summary))}</p>
  <p class="note">결론은 세 가지뿐입니다 — 조작·생성 근거 있음 / 원본성 근거 있음 / 판단 불가. 결정적 근거(메타데이터·C2PA)만 결론을 내리고, 통계적(모델)·어휘적(키워드) 근거는 보정 전까지 참고로만 표시합니다. 검사가 실패한 파일은 판단 불가로 남습니다.</p>
  {legal_note}
  <p class="note">{_threshold_provenance_line(thresholds)}</p>
  <table>
    <thead><tr><th>결론</th><th>근거(종류별)</th><th>검사 범위(미실행·실패)</th><th>파일</th><th>참고 신호</th><th>heatmap</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  {_signature_html(signed_report)}
</body>
</html>
"""
    output.write_text(body, encoding="utf-8")


def _evidence_sha256(
    path_text: str,
    allow: "Callable[[str], bool] | None" = None,
    resolve: "Callable[[str], Path | None] | None" = None,
) -> str | None:
    """Hash the evidence file fully, streaming — or return None.

    Never fabricate a digest: a path string is not evidence. Callers must
    render ``None`` as "hash unavailable", not as a hex value.
    ``resolve`` maps a stored row path (often relative) to the real
    evidence file so web reports hash the same bytes the CLI hashes.
    """

    p = Path(path_text)
    if resolve is not None:
        resolved = resolve(path_text)
        if resolved is None:
            return None
        p = resolved
    if allow is not None and not allow(path_text):
        return None
    try:
        if not p.is_file():
            return None
        digest = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _threshold_provenance_ko(thresholds: object | None) -> str:
    """Korean calibration-provenance line for the forensic PDF header."""
    to_json = getattr(thresholds, "to_json", None)
    payload = to_json() if callable(to_json) else (thresholds if isinstance(thresholds, dict) else {})
    if thresholds is None or not isinstance(payload, dict) or payload.get("source") == "builtin_defaults":
        return "판정 임계값: 내장 기본값 (비측정 — 잠정; calibration 미적용)"
    samples = int(payload.get("samples", 0) or 0)
    state = "잠정(미검증)" if payload.get("provisional", True) else "측정됨"
    if payload.get("in_sample"):
        state += " · in-sample(참고)"  # G28
    return f"판정 임계값: 프로파일 {payload.get('version', '?')} — {state}, 표본 n={samples}"


def write_pdf_report(
    path: Path | str,
    summary: BatchScanSummary,
    items: list[ScanItem],
    *,
    redact_paths: bool = False,
    thresholds: object | None = None,
    degrade_note: str | None = None,
    signed_report: dict[str, object] | None = None,
) -> None:
    if signed_report is None:
        signed_report = signed_report_body(summary, items, thresholds=thresholds, report_format="pdf", redact_paths=redact_paths)
    lines = [
        "Deepfake Lens Report",
        summary_line_ascii(summary),
        "Verdicts: MANIPULATION-EVIDENCE / AUTHENTICITY-EVIDENCE / UNDETERMINED. Only deterministic",
        "evidence (metadata, C2PA) concludes; statistical (model) and lexical (keyword) evidence is",
        "reference-only until calibrated. A failed check leaves the file UNDETERMINED.",
        _threshold_provenance_line(thresholds),
        "",
    ]
    if _has_reference_grade(items):
        lines.insert(2, "TEXT RESULTS: reference grade only - text-generation detection has no evidentiary value (2026).")
    if degrade_note:
        lines.append(f"NOTE: {degrade_note}")
        lines.append("")
    shown = items[:80]
    if len(items) > len(shown):
        lines.append(f"NOTE: {len(items)} items scanned; first {len(shown)} shown — see JSON/CSV output for the remainder.")
        lines.append("")
    for item in shown:
        result = item.result
        if result is None:
            lines.append(f"{item.status} {_display_path(item.path, redact_paths=redact_paths)} {item.error or ''}")
            continue
        counts = evidence_counts(result)
        failed = sum(1 for entry in result.coverage if entry.status == CoverageStatus.FAILED)
        skipped = sum(1 for entry in result.coverage if entry.status == CoverageStatus.SKIPPED)
        top = deciding_evidence(result)
        lines.append(
            f"{VERDICT_CODES_ASCII[result.verdict_code]} grade={result.grade.value} "
            f"det={counts[EvidenceKind.DETERMINISTIC]} stat={counts[EvidenceKind.STATISTICAL]} lex={counts[EvidenceKind.LEXICAL]} "
            f"checks_failed={failed} skipped={skipped} {_display_path(item.path, redact_paths=redact_paths)}"
            + (f" [{top.kind.value}] {top.title}" if top else "")
        )
    lines.append("")
    lines.extend(signature_lines_ascii(signed_report))
    if any(ord(char) > 255 for line in lines for char in line):
        # The minimal PDF writer is Latin-1 only; state the limitation
        # instead of silently turning Korean labels into '?'.
        lines = [
            "NOTE: this simple PDF is Latin-1 only; non-Latin text",
            "(e.g. Korean evidence titles) appears as '?'.",
            "Use --html-out for a full Unicode report.",
            "",
        ] + lines
    _write_minimal_pdf(Path(path), lines)


def write_forensic_pdf_report(
    path: Path | str,
    summary: BatchScanSummary,
    items: list[ScanItem],
    *,
    redact_paths: bool = False,
    exhibit_no: str = "갑 제        호증",
    thresholds: object | None = None,
    coverage: dict[str, object] | None = None,
    allow_path: "Callable[[str], bool] | None" = None,
    resolve_path: "Callable[[str], Path | None] | None" = None,
    signed_report: dict[str, object] | None = None,
) -> None:
    """Generate a court-admissible forensic PDF report with ECFS exhibit stamp,
    SHA-256 evidence integrity hashes, and Daeryun Law Firm forensic signoff.

    The signature block (G30) states the HMAC signature and the signed body's
    SHA-256, or "서명 없음" when no report key is configured.
    """
    if signed_report is None:
        signed_report = signed_report_body(summary, items, thresholds=thresholds, coverage=coverage, report_format="pdf", redact_paths=redact_paths)
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            write_pdf_report(
                path, summary, items,
                redact_paths=redact_paths,
                thresholds=thresholds,
                signed_report=signed_report,
                degrade_note="pymupdf not installed — this is a simplified text report, NOT the ECFS-stamped forensic layout. Install the 'forensic' extra for the court artifact.",
            )
            return

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open()
    font_ko = "korea"
    font_en = "helv"

    page_w, page_h = 595.0, 842.0
    margin_l, margin_r = 45.0, 550.0

    def create_page() -> Any:
        page = doc.new_page(width=page_w, height=page_h)
        page.insert_text(pymupdf.Point(margin_l, 35), "법무법인(유한) 대륜 디지털포렌식 감정센터", fontname=font_ko, fontsize=9, color=(0.15, 0.25, 0.45))
        page.insert_text(pymupdf.Point(margin_l, 46), "서울특별시 강남구 테헤란로 114, 역삼빌딩 | 대표전화: 02-780-1128", fontname=font_ko, fontsize=7.5, color=(0.5, 0.5, 0.5))
        page.draw_line(pymupdf.Point(margin_l, 52), pymupdf.Point(margin_r, 52), color=(0.85, 0.88, 0.92), width=0.8)
        return page

    page = create_page()

    # Court Exhibit Box (Top Right)
    page.draw_rect(pymupdf.Rect(415, 60, 550, 112), color=(0.18, 0.32, 0.55), width=1.2)
    page.draw_rect(pymupdf.Rect(415, 60, 550, 75), color=(0.93, 0.95, 0.98), fill=(0.93, 0.95, 0.98))
    page.insert_text(pymupdf.Point(423, 71), "증거 표찰 (ECFS 규격)", fontname=font_ko, fontsize=8, color=(0.18, 0.32, 0.55))
    page.insert_text(pymupdf.Point(423, 93), exhibit_no, fontname=font_ko, fontsize=11, color=(0.1, 0.1, 0.1))
    page.insert_text(pymupdf.Point(423, 106), "증거명: 디지털 미디어 AI 감정서", fontname=font_ko, fontsize=7.5, color=(0.4, 0.4, 0.4))

    # Title Banner (Left)
    page.insert_text(pymupdf.Point(margin_l, 80), "디지털 포렌식 AI 감정보고서", fontname=font_ko, fontsize=16, color=(0.08, 0.15, 0.32))
    page.insert_text(pymupdf.Point(margin_l, 98), "DEEPFAKE LENS FORENSIC AI DETECTION REPORT", fontname=font_en, fontsize=8, color=(0.4, 0.45, 0.5))
    page.insert_text(pymupdf.Point(margin_l, 110), f"문서 번호: DFL-EVID-{int(time.time())}", fontname=font_en, fontsize=7.5, color=(0.5, 0.5, 0.5))

    # Evidence hashes are computed once, up front, so the header's
    # integrity claim can state the real verified/total count.
    # The scanner's content hash is reused when the row has one (G11) —
    # each evidence file is read for hashing at most once.
    hash_map = {item.path: item.sha256 or _evidence_sha256(item.path, allow_path, resolve_path) for item in items}
    hashed = sum(1 for v in hash_map.values() if v)

    # Metadata & Case Overview Box
    meta_box = pymupdf.Rect(margin_l, 122, margin_r, 196)
    page.draw_rect(meta_box, color=(0.85, 0.88, 0.92), fill=(0.98, 0.98, 0.99))

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    page.insert_text(pymupdf.Point(margin_l + 10, 137), "감정 의뢰: (의뢰사 상호명 입력) / (담당자 부서·직위·성명) 귀하", fontname=font_ko, fontsize=8.5, color=(0.2, 0.2, 0.2))
    page.insert_text(pymupdf.Point(margin_l + 10, 151), f"감정 일시: {now_str} (KST)  |  분석 엔진: Deepfake Lens Forensic Suite v0.1.0", fontname=font_ko, fontsize=8.5, color=(0.2, 0.2, 0.2))
    page.insert_text(
        pymupdf.Point(margin_l + 10, 165),
        f"감정 결과: 총 {summary.total}개 — 조작·생성 근거 {summary.manipulation_evidence}건, 원본성 근거 {summary.authenticity_evidence}건, 판단 불가 {summary.undetermined}건(검사 실패 {summary.checks_failed}건), 미지원/오류 {summary.unsupported_or_failed}건",
        fontname=font_ko,
        fontsize=8.5,
        color=(0.1, 0.2, 0.4),
    )
    page.insert_text(
        pymupdf.Point(margin_l + 10, 178),
        f"무결성 확인: {hashed}/{len(items)} 파일 SHA-256 전체 해시 계산" + ("  |  보안 등급: 사법기관 제출용 대외비" if hashed == len(items) else "  |  해시 불가 항목 포함 — 원본 접근 필요"),
        fontname=font_ko,
        fontsize=8,
        color=(0.45, 0.45, 0.45),
    )
    page.insert_text(
        pymupdf.Point(margin_l + 10, 191),
        _threshold_provenance_ko(thresholds),
        fontname=font_ko,
        fontsize=8,
        color=(0.45, 0.45, 0.45),
    )

    # Table Header
    y = 208.0
    page.draw_rect(pymupdf.Rect(margin_l, y, margin_r, y + 20), color=(0.8, 0.85, 0.9), fill=(0.92, 0.94, 0.97))
    page.insert_text(pymupdf.Point(margin_l + 5, y + 14), "No.", fontname=font_en, fontsize=8, color=(0.15, 0.2, 0.35))
    page.insert_text(pymupdf.Point(margin_l + 30, y + 14), "증거 파일명 및 SHA-256 무결성 해시", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
    page.insert_text(pymupdf.Point(margin_l + 250, y + 14), "결론", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
    page.insert_text(pymupdf.Point(margin_l + 300, y + 14), "등급", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
    page.insert_text(pymupdf.Point(margin_l + 345, y + 14), "근거 종류", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
    page.insert_text(pymupdf.Point(margin_l + 420, y + 14), "주요 근거/검사 실패", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
    y += 20.0

    row_h = 28.0

    for idx, item in enumerate(items, start=1):
        if y + row_h > 720:
            page = create_page()
            y = 70.0
            page.draw_rect(pymupdf.Rect(margin_l, y, margin_r, y + 20), color=(0.8, 0.85, 0.9), fill=(0.92, 0.94, 0.97))
            page.insert_text(pymupdf.Point(margin_l + 5, y + 14), "No.", fontname=font_en, fontsize=8, color=(0.15, 0.2, 0.35))
            page.insert_text(pymupdf.Point(margin_l + 30, y + 14), "증거 파일명 및 SHA-256 무결성 해시", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
            page.insert_text(pymupdf.Point(margin_l + 250, y + 14), "결론", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
            page.insert_text(pymupdf.Point(margin_l + 300, y + 14), "등급", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
            page.insert_text(pymupdf.Point(margin_l + 345, y + 14), "근거 종류", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
            page.insert_text(pymupdf.Point(margin_l + 420, y + 14), "주요 근거/검사 실패", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
            y += 20.0

        if idx % 2 == 0:
            page.draw_rect(pymupdf.Rect(margin_l, y, margin_r, y + row_h), color=(0.96, 0.97, 0.98), fill=(0.96, 0.97, 0.98))
        page.draw_line(pymupdf.Point(margin_l, y + row_h), pymupdf.Point(margin_r, y + row_h), color=(0.9, 0.92, 0.94), width=0.5)

        res = item.result
        band_str = VERDICT_LABELS[res.verdict_code] if res else (item.status or "-")
        score_val = ("참고" if res.grade == Grade.REFERENCE else "근거") if res else "-"
        source_str = evidence_counts_text(res) if res else "-"
        failed_checks = [entry for entry in res.coverage if entry.status == CoverageStatus.FAILED] if res else []
        top_item = deciding_evidence(res) if res else None
        if failed_checks:
            sig_str = "실패: " + failed_checks[0].check
        elif top_item is not None:
            sig_str = f"[{EVIDENCE_KIND_LABELS[top_item.kind][:2]}] {top_item.title}"
        else:
            sig_str = item.error or "근거 항목 없음"

        if res and res.verdict_code == Verdict.MANIPULATION_EVIDENCE:
            band_color = (0.8, 0.15, 0.15)
        elif res and res.verdict_code == Verdict.AUTHENTICITY_EVIDENCE:
            band_color = (0.1, 0.55, 0.25)
        else:
            band_color = (0.4, 0.4, 0.4)

        sha256_hex = hash_map.get(item.path)
        hash_line = f"SHA-256: {sha256_hex[:32]}…" if sha256_hex else "SHA-256: 해시 불가 — 원본 파일 접근 실패"

        disp_path = Path(item.path).name if redact_paths else item.path
        if len(disp_path) > 36:
            disp_path = disp_path[:33] + "…"

        page.insert_text(pymupdf.Point(margin_l + 5, y + 12), str(idx), fontname=font_en, fontsize=8, color=(0.3, 0.3, 0.3))
        page.insert_text(pymupdf.Point(margin_l + 30, y + 12), disp_path, fontname=font_ko, fontsize=8, color=(0.1, 0.1, 0.1))
        page.insert_text(pymupdf.Point(margin_l + 30, y + 24), hash_line, fontname=font_en if sha256_hex else font_ko, fontsize=6.5, color=(0.5, 0.5, 0.5) if sha256_hex else (0.7, 0.3, 0.3))

        page.insert_text(pymupdf.Point(margin_l + 250, y + 15), band_str[:8], fontname=font_ko, fontsize=7, color=band_color)
        page.insert_text(pymupdf.Point(margin_l + 300, y + 15), score_val, fontname=font_ko, fontsize=8, color=(0.1, 0.1, 0.1))
        page.insert_text(pymupdf.Point(margin_l + 345, y + 15), source_str[:12], fontname=font_ko, fontsize=7.5, color=(0.3, 0.3, 0.3))
        page.insert_text(pymupdf.Point(margin_l + 420, y + 15), sig_str[:18], fontname=font_ko, fontsize=7.5, color=(0.2, 0.2, 0.2))

        y += row_h

    if y + 120 > 750:
        page = create_page()
        y = 70.0

    if _has_reference_grade(items):
        y += 12.0
        page.insert_text(pymupdf.Point(margin_l, y), TEXT_LEGAL_LIMITATION, fontname=font_ko, fontsize=8, color=(0.55, 0.3, 0.0))
    y += 12.0
    page.insert_text(
        pymupdf.Point(margin_l, y),
        "결론은 결정적 근거(메타데이터·C2PA)로만 내리며, 통계적·어휘적 근거와 검사 실패 내역은 JSON 산출물의 evidence/coverage에 전부 기록됩니다.",
        fontname=font_ko, fontsize=7, color=(0.4, 0.4, 0.4),
    )

    y += 15.0
    sign_box = pymupdf.Rect(margin_l, y, margin_r, y + 65)
    page.draw_rect(sign_box, color=(0.8, 0.85, 0.9), fill=(0.97, 0.98, 0.99))
    _wa = coverage.get("weights_available", 0) if coverage else 0
    if isinstance(_wa, (int, float)) and _wa > 0:
        engine_text = "사법절차 적격성 고지: 본 감정서는 법무법인(유한) 대륜 디지털포렌식 감정센터의 뉴럴 앙상블 + 로컬 휴리스틱 분석에 따른 스크리닝 결과입니다."
    elif coverage is not None:
        engine_text = "사법절차 적격성 고지: 본 감정서는 신경망 가중치 미탑재 상태의 로컬 휴리스틱 분석에 따른 스크리닝 결과입니다 (뉴럴 엔진 미실행)."
    else:
        engine_text = "사법절차 적격성 고지: 본 감정서는 법무법인(유한) 대륜 디지털포렌식 감정센터의 로컬 스크리닝 분석 결과입니다."
    page.insert_text(
        pymupdf.Point(margin_l + 10, y + 16),
        engine_text,
        fontname=font_ko,
        fontsize=7.5,
        color=(0.4, 0.4, 0.4),
    )
    if hashed == len(items):
        integrity_text = "무결성 확약: 상기 기재된 증거물 일체는 SHA-256 해시 검증을 필하였으며, 채증·보존 과정에서 위변조되지 않았음을 확인합니다."
    else:
        integrity_text = f"무결성 고지: 해시가 계산된 {hashed}건은 채증 시점 값을 기재하였으며, 해시 불가 {len(items) - hashed}건은 별도 표기하였습니다."
    page.insert_text(
        pymupdf.Point(margin_l + 10, y + 29),
        integrity_text,
        fontname=font_ko,
        fontsize=7.5,
        color=(0.4, 0.4, 0.4),
    )
    page.insert_text(
        pymupdf.Point(margin_l + 280, y + 50),
        "법무법인(유한) 대륜 디지털포렌식 감정관 (직인생략)",
        fontname=font_ko,
        fontsize=9,
        color=(0.15, 0.25, 0.45),
    )

    sig_y = y + 76.0
    for line in signature_lines_ko(signed_report):
        page.insert_text(pymupdf.Point(margin_l, sig_y), line, fontname=font_ko, fontsize=6.5, color=(0.35, 0.35, 0.35))
        sig_y += 9.0

    total_pages = doc.page_count
    for i in range(total_pages):
        p = doc[i]
        p.insert_text(
            pymupdf.Point(page_w / 2 - 20, page_h - 25),
            f"- {i + 1} / {total_pages} -",
            fontname=font_en,
            fontsize=8,
            color=(0.5, 0.5, 0.5),
        )

    _atomic_write_bytes(output, doc.tobytes())
    doc.close()


def write_eval_html_report(path: Path | str, payload: dict[str, object], *, redact_paths: bool = False) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    metrics_raw = payload.get("metrics")
    confusion_raw = payload.get("confusion")
    case_raw = payload.get("case_summary")
    metrics: dict = dict(metrics_raw) if isinstance(metrics_raw, dict) else {}
    confusion: dict = dict(confusion_raw) if isinstance(confusion_raw, dict) else {}
    case_summary: dict = dict(case_raw) if isinstance(case_raw, dict) else {}
    fp_raw = case_summary.get("false_positives")
    fn_raw = case_summary.get("false_negatives")
    items_raw = payload.get("items")
    false_positives = list(fp_raw) if isinstance(fp_raw, list) else []
    false_negatives = list(fn_raw) if isinstance(fn_raw, list) else []
    rows = "\n".join(_eval_row(row, redact_paths=redact_paths) for row in (items_raw if isinstance(items_raw, list) else []) if isinstance(row, dict))
    body = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>Deepfake Lens Benchmark</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 32px; color: #1f2933; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border-bottom: 1px solid #d8dee9; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f5f7fa; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }}
    .metric {{ border: 1px solid #d8dee9; padding: 10px; }}
  </style>
</head>
<body>
  <h1>Deepfake Lens Benchmark</h1>
  <div class="grid">
    <div class="metric">accuracy<br><strong>{escape(str(metrics.get("accuracy", "-")))}</strong></div>
    <div class="metric">precision<br><strong>{escape(str(metrics.get("precision", "-")))}</strong></div>
    <div class="metric">recall [95% CI]<br><strong>{escape(format_ci(metrics.get("recall"), metrics.get("recall_ci")))}</strong></div>
    <div class="metric">FPR [95% CI]<br><strong>{escape(format_ci(metrics.get("false_positive_rate"), metrics.get("false_positive_rate_ci")))}</strong></div>
    <div class="metric">AUROC [95% CI]<br><strong>{escape(format_ci(metrics.get("auroc"), metrics.get("auroc_ci")))}</strong></div>
    <div class="metric">n_pos / n_neg<br><strong>{escape(str(metrics.get("n_pos", "-")))} / {escape(str(metrics.get("n_neg", "-")))}</strong></div>
    <div class="metric">FP/FN<br><strong>{len(false_positives)} / {len(false_negatives)}</strong></div>
  </div>
  <p>점수 기준: {escape(str(payload.get("score_basis", "raw, uncalibrated")))} — {escape(str(payload.get("score_basis_note", "")))}</p>
  <p>Confusion: {escape(str(confusion))}</p>
  <table>
    <thead><tr><th>label</th><th>predicted</th><th>score</th><th>source</th><th>guess</th><th>file</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""
    output.write_text(body, encoding="utf-8")


def _html_row(item: ScanItem, *, redact_paths: bool, allow_path: Callable[[str], bool] | None = None) -> str:
    result = item.result
    path_cell = escape(_display_path(item.path, redact_paths=redact_paths))
    if result is None:
        return (
            "<tr>"
            f"<td>{escape(item.status)}</td>"
            f"<td>{escape(item.error or '')}</td>"
            "<td></td>"
            f"<td>{path_cell}</td>"
            "<td></td><td></td>"
            "</tr>"
        )
    verdict_cell = (
        f'<span class="v-{escape(result.verdict_code.value)}">{escape(verdict_heading(result))}</span>'
        f"<br>{escape(result.verdict)}"
    )
    evidence_parts: list[str] = []
    if result.grade == Grade.REFERENCE:
        evidence_parts.append(f'<div class="legal">{escape(leading_limitations(result)[0])}</div>')
    for kind_label, lines in evidence_groups(result):
        evidence_parts.append(f'<div class="kind">{escape(kind_label)}</div><ul>' + "".join(f"<li>{escape(line)}</li>" for line in lines) + "</ul>")
    if not evidence_parts:
        evidence_parts.append("근거 항목 없음")
    gaps = coverage_gaps(result)
    gap_cell = "<ul>" + "".join(
        f'<li class="{"gap-failed" if entry.status == CoverageStatus.FAILED else ""}">{escape(entry.describe())}</li>' for entry in gaps
    ) + "</ul>" if gaps else "전 검사 실행"
    reference = "; ".join(f"{signal.title} ({signal.weight})" for signal in result.reference_signals) or "-"
    heatmap = ""
    if result.pixel_analysis and result.pixel_analysis.available:
        heatmap = _heatmap_img(result.pixel_analysis.heatmap_path, allow_path=allow_path)
    return (
        "<tr>"
        f"<td>{verdict_cell}</td>"
        f"<td>{''.join(evidence_parts)}</td>"
        f"<td>{gap_cell}</td>"
        f"<td>{path_cell}</td>"
        f"<td>{escape(reference)}</td>"
        f"<td>{heatmap}</td>"
        "</tr>"
    )


def _eval_row(row: dict[str, object], *, redact_paths: bool) -> str:
    return (
        "<tr>"
        f"<td>{escape(str(row.get('label', '')))}</td>"
        f"<td>{escape(str(row.get('predicted', '')))}</td>"
        f"<td>{escape(str(row.get('score', '')))}</td>"
        f"<td>{escape(str(row.get('source', '')))}</td>"
        f"<td>{escape(str(row.get('source_guess', '')))}</td>"
        f"<td>{escape(_display_path(str(row.get('path', '')), redact_paths=redact_paths))}</td>"
        "</tr>"
    )


def _display_path(path: str, *, redact_paths: bool) -> str:
    return Path(path).name if redact_paths else path


HEATMAP_PLACEHOLDER = "(히트맵 생략: 허용되지 않은 경로)"


def _heatmap_img(path: str | None, *, allow_path: Callable[[str], bool] | None = None) -> str:
    """Inline a heatmap PNG, or a placeholder when ``allow_path`` rejects it (G31).

    The check runs before any filesystem access, so a rejected path is never
    stat'ed or read — not even its name is echoed.
    """
    if not path:
        return ""
    if allow_path is not None and not allow_path(str(path)):
        return escape(HEATMAP_PLACEHOLDER)
    heatmap = Path(path)
    try:
        if heatmap.suffix.lower() != ".png" or heatmap.stat().st_size > 512 * 1024:
            return escape(heatmap.name)
        encoded = base64.b64encode(heatmap.read_bytes()).decode("ascii")
    except OSError:
        return escape(heatmap.name)
    return f'<img class="heatmap" alt="heatmap" src="data:image/png;base64,{encoded}">'


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write via temp file + os.replace so a crash cannot leave a half-written artifact."""
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _write_minimal_pdf(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content_lines = ["BT", "/F1 11 Tf", "50 780 Td"]
    for index, line in enumerate(lines):
        if index:
            content_lines.append("0 -15 Td")
        content_lines.append(f"({_pdf_escape(line[:110])}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines).encode("latin-1", errors="replace")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        b"5 0 obj << /Length " + str(len(stream)).encode("ascii") + b" >> stream\n" + stream + b"\nendstream endobj\n",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(output))
        output.extend(obj)
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    _atomic_write_bytes(path, bytes(output))


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

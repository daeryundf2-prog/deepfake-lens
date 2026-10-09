from __future__ import annotations

import base64
import json
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable

from .core import TOOL_VERSION, BatchScanSummary, ScanItem
from .evaluation_metrics import format_ci
from .office_config import OfficeIdentity, office_identity
from .result_text import (
    HASH_UNAVAILABLE_ACCESS,
    HASH_UNAVAILABLE_MEMBER,
    HASH_UNAVAILABLE_SYMLINK,
    TEXT_LEGAL_LIMITATION,
    coverage_gaps,
    deciding_evidence,
    display_path,
    evidence_counts_text,
    evidence_groups,
    is_symlink_row,
    leading_limitations,
    summary_line,
    threshold_provenance_line,
    threshold_provenance_lines,
    unrecorded_files,
    UNRECORDED_SECTION_TITLE,
    verdict_heading,
)
from .result_types import EVIDENCE_KIND_LABELS, VERDICT_LABELS, CoverageStatus, Grade, Verdict, check_label, is_verdict_row, status_label
from .signing import REPORT_KEY_ENV, resolve_report_key, sign_report, signed_body_sha256

# G9: Korean label of the benchmark ``score_basis`` code (the JSON keeps the code).
SCORE_BASIS_LABELS = {"raw, uncalibrated": "보정 전 원점수"}

# Report title (B2): the product name is an identifier, the rest Korean.
HTML_REPORT_TITLE = "Deepfake Lens 감정 보고서"


def _has_reference_grade(items: list[ScanItem]) -> bool:
    return any(item.result is not None and item.result.grade == Grade.REFERENCE for item in items)


def _threshold_provenance_line(thresholds: object | None) -> str:
    """One-line calibration provenance for report headers — Korean (B2).

    A scan has no reason to hide that its cutoffs were the unmeasured
    builtins; a loaded profile reports its sample count so provisional
    (n < MIN_CALIBRATION_SAMPLES) fits are visibly marked unvalidated, and an
    in-sample fit carries the same caveat as the CLI header (G28).
    """
    return threshold_provenance_line(thresholds)


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
        # X1: inside the signed body, like the scan JSON.
        "unrecorded_files": unrecorded_files(list(items), summary).to_json(),
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
  <title>{escape(HTML_REPORT_TITLE)}</title>
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
  <h1>{escape(HTML_REPORT_TITLE)}</h1>
  <p>{escape(summary_line(summary))}</p>
  <p class="note">결론은 세 가지뿐입니다 — 조작·생성 근거 있음 / 원본성 근거 있음 / 판단 불가. 결정적 근거(메타데이터·C2PA)만 결론을 내리고, 통계적(모델)·어휘적(키워드) 근거는 보정 전까지 참고로만 표시합니다. 검사가 실패한 파일은 판단 불가로 남습니다.</p>
  {legal_note}
  <p class="note">{"<br>".join(escape(line) for line in threshold_provenance_lines(thresholds))}</p>
  {_unrecorded_html(summary, items)}
  <table>
    <thead><tr><th>결론</th><th>근거(종류별)</th><th>검사 범위(미실행·실패)</th><th>파일</th><th>참고 신호</th><th>히트맵</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  {_signature_html(signed_report)}
</body>
</html>
"""
    output.write_text(body, encoding="utf-8")


def _unrecorded_html(summary: BatchScanSummary, items: list[ScanItem]) -> str:
    """The HTML "기록되지 않은 파일" section (X1): count and reasons, always present."""
    unrecorded = unrecorded_files(list(items), summary)
    reasons = "".join(f"<li>{escape(line)}</li>" for line in unrecorded.reason_lines())
    return (
        f'<section class="unrecorded" id="unrecorded-files"><h2>{escape(UNRECORDED_SECTION_TITLE)}</h2>'
        f"<p>{escape(unrecorded.headline())}</p>"
        + (f"<ul>{reasons}</ul>" if reasons else "")
        + "</section>"
    )


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
    N2: a symbolic link is never followed — the path is checked with
    ``lstat`` and opened with ``O_NOFOLLOW``, so a link row stays None
    ("해시 불가(심볼릭 링크 …)") instead of carrying its target's digest.
    """
    from .evidence_statement import _compute_sha256, _SymlinkRefused

    p = Path(path_text)
    if resolve is not None:
        resolved = resolve(path_text)
        if resolved is None:
            return None
        p = resolved
    if allow is not None and not allow(path_text):
        return None
    try:
        return _compute_sha256(p)
    except _SymlinkRefused:
        return None


def _threshold_provenance_ko(thresholds: object | None) -> str:
    """Korean calibration-provenance line for the forensic PDF header (same as HTML)."""
    return threshold_provenance_line(thresholds)


def _hash_unavailable_reason(item: ScanItem) -> str:
    """Why a forensic-PDF row has no hash — the evidence statement's wording (S2)."""
    if "::" in item.path:
        return HASH_UNAVAILABLE_MEMBER
    if is_symlink_row(item.status, item.error):
        return HASH_UNAVAILABLE_SYMLINK
    return HASH_UNAVAILABLE_ACCESS


def write_pdf_report(
    path: Path | str,
    summary: BatchScanSummary,
    items: list[ScanItem],
    *,
    redact_paths: bool = False,
    thresholds: object | None = None,
    coverage: dict[str, object] | None = None,
    exhibit_no: str = "갑 제        호증",
    signed_report: dict[str, object] | None = None,
    law_firm: str | None = None,
    contact: str | None = None,
) -> None:
    """``scan --pdf-out``: the Korean forensic PDF renderer (B8).

    Without pymupdf this raises :class:`~deepfake_lens.evidence_statement.PdfDependencyMissing`
    with a Korean message — it never writes the old Latin-1-only (English)
    PDF. The CLI checks this before the scan starts (exit 2, as R6).
    """
    write_forensic_pdf_report(
        path, summary, items,
        redact_paths=redact_paths,
        exhibit_no=exhibit_no,
        thresholds=thresholds,
        coverage=coverage,
        signed_report=signed_report,
        law_firm=law_firm,
        contact=contact,
    )


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
    law_firm: str | None = None,
    contact: str | None = None,
) -> None:
    """Generate a court-admissible forensic PDF report with ECFS exhibit stamp,
    SHA-256 evidence integrity hashes and the examiner's office signoff.

    N17: the office name and phone come from ``law_firm`` / ``contact`` or the
    operator config (:mod:`deepfake_lens.office_config`) — blank otherwise,
    never a built-in firm.

    The signature block (G30) states the HMAC signature and the signed body's
    SHA-256, or "서명 없음" when no report key is configured.

    Without pymupdf this raises :class:`~deepfake_lens.evidence_statement.PdfDependencyMissing`
    (Korean message) and writes nothing — there is no Latin-1 fallback (B8).
    """
    if signed_report is None:
        signed_report = signed_report_body(summary, items, thresholds=thresholds, coverage=coverage, report_format="pdf", redact_paths=redact_paths)
    from .evidence_statement import PDF_REPORT_DEPENDENCY_MESSAGE, PdfDependencyMissing
    from .pdf_backend import import_pymupdf

    try:
        pymupdf = import_pymupdf()  # N4: pymupdf first; a legacy fitz import never prints to stdout
    except ImportError as exc:
        # B8: no English Latin-1 fallback PDF — refuse in Korean; nothing is written.
        raise PdfDependencyMissing(PDF_REPORT_DEPENDENCY_MESSAGE) from exc

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(output, _render_forensic_pdf(
        pymupdf, summary, items,
        redact_paths=redact_paths, exhibit_no=exhibit_no, thresholds=thresholds, coverage=coverage,
        allow_path=allow_path, resolve_path=resolve_path, signed_report=signed_report,
        office=office_identity(law_firm, contact),
    ))


# Forensic PDF text (G2: every string is measured and wrapped by pdf_layout —
# no fixed x offsets, no character cuts).
FORENSIC_PDF_TITLE = "디지털 포렌식 AI 감정보고서"
FORENSIC_TABLE_HEADERS = ("번호", "증거 파일명 및 SHA-256 무결성 해시", "결론", "등급", "근거 종류", "주요 근거/검사 실패")
# Relative column widths of the evidence table (번호 … 주요 근거).
FORENSIC_TABLE_WEIGHTS = (5.0, 35.0, 12.0, 7.0, 14.0, 27.0)
FORENSIC_DECISION_NOTE = (
    "결론은 결정적 근거(메타데이터·C2PA)로만 내리며, 통계적·어휘적 근거와 검사 실패 내역은 "
    "JSON 산출물의 evidence/coverage에 전부 기록됩니다."
)


def _render_forensic_pdf(
    pymupdf: Any,
    summary: BatchScanSummary,
    items: list[ScanItem],
    *,
    redact_paths: bool,
    exhibit_no: str,
    thresholds: object | None,
    coverage: dict[str, object] | None,
    allow_path: "Callable[[str], bool] | None",
    resolve_path: "Callable[[str], Path | None] | None",
    signed_report: dict[str, object],
    office: OfficeIdentity,
) -> bytes:
    """Lay out the forensic PDF with :class:`pdf_layout.PdfLayout` (G2) and return its bytes."""
    from .pdf_layout import Cell, PdfLayout

    header_blue = (0.15, 0.25, 0.45)
    firm_name = office.header
    contact_line = f"대표전화: {office.contact}" if office.contact else ""

    def page_header(layout: PdfLayout) -> None:
        layout.text(layout.left, layout.right, firm_name, 9.0, header_blue, gap=0.5)
        if contact_line:
            layout.text(layout.left, layout.right, contact_line, 7.5, (0.5, 0.5, 0.5), gap=2.0)
        layout.page.draw_line(pymupdf.Point(layout.left, layout.y), pymupdf.Point(layout.right, layout.y), color=(0.85, 0.88, 0.92), width=0.8)
        layout.y += 6.0

    layout = PdfLayout(pymupdf, header=page_header)
    layout.new_page()

    # Title (left) and the ECFS exhibit box (right), side by side.
    box_w = 150.0
    title_x1 = layout.right - box_w - 12.0
    top = layout.y
    layout.text(layout.left, title_x1, FORENSIC_PDF_TITLE, 16.0, (0.08, 0.15, 0.32), gap=2.0)
    layout.text(layout.left, title_x1, f"{HTML_REPORT_TITLE} — 디지털 미디어 AI 생성·조작 감정", 8.0, (0.4, 0.45, 0.5), gap=1.0)
    layout.text(layout.left, title_x1, f"문서 번호: DFL-EVID-{int(time.time())}", 7.5, (0.5, 0.5, 0.5), gap=1.0)
    title_bottom = layout.y
    box_x0 = layout.right - box_w
    pad = 6.0
    label_lines = layout.wrap("증거 표찰 (ECFS 규격)", box_w - 2 * pad, 8.0)
    exhibit_lines = layout.wrap(exhibit_no, box_w - 2 * pad, 11.0)
    name_lines = layout.wrap("증거명: 디지털 미디어 AI 감정서", box_w - 2 * pad, 7.5)
    label_h = layout.block_height(label_lines, 8.0) + 4.0
    box_h = label_h + layout.block_height(exhibit_lines, 11.0) + layout.block_height(name_lines, 7.5) + 3 * pad
    layout.page.draw_rect(pymupdf.Rect(box_x0, top, layout.right, top + label_h), color=(0.93, 0.95, 0.98), fill=(0.93, 0.95, 0.98))
    layout.page.draw_rect(pymupdf.Rect(box_x0, top, layout.right, top + box_h), color=(0.18, 0.32, 0.55), width=1.2)
    y = top + 2.0
    y += layout.draw_lines(box_x0 + pad, layout.right - pad, y, label_lines, 8.0, (0.18, 0.32, 0.55)) + pad
    y += layout.draw_lines(box_x0 + pad, layout.right - pad, y, exhibit_lines, 11.0, (0.1, 0.1, 0.1)) + pad / 2
    layout.draw_lines(box_x0 + pad, layout.right - pad, y, name_lines, 7.5, (0.4, 0.4, 0.4))
    layout.y = max(title_bottom, top + box_h) + 8.0

    # Evidence hashes are computed once, up front, so the header's
    # integrity claim can state the real verified/total count.
    # The scanner's content hash is reused when the row has one (G11) —
    # each evidence file is read for hashing at most once.
    hash_map = {item.path: item.sha256 or _evidence_sha256(item.path, allow_path, resolve_path) for item in items}
    hashed = sum(1 for v in hash_map.values() if v)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result_line = (
        f"감정 결과: 총 {summary.total}개"
        + (f"(압축 파일 {summary.container_rows}건 포함)" if summary.container_rows else "")
        + f" — 조작·생성 근거 {summary.manipulation_evidence}건, 원본성 근거 {summary.authenticity_evidence}건, "
        f"판단 불가 {summary.undetermined}건(검사 실패 {summary.checks_failed}건), 미지원/오류 {summary.unsupported_or_failed}건"
    )
    integrity_line = f"무결성 확인: {hashed}/{len(items)} 파일 SHA-256 전체 해시 계산" + (
        "  |  보안 등급: 사법기관 제출용 대외비" if hashed == len(items) else "  |  해시 불가 항목 포함 — 원본 접근 필요"
    )
    layout.boxed_text("", [
        ("감정 의뢰: (의뢰사 상호명 입력) / (담당자 부서·직위·성명) 귀하", 8.5, (0.2, 0.2, 0.2)),
        (f"감정 일시: {now_str} (KST)  |  분석 엔진: Deepfake Lens v{TOOL_VERSION}", 8.5, (0.2, 0.2, 0.2)),
        (result_line, 8.5, (0.1, 0.2, 0.4)),
        (integrity_line, 8.0, (0.45, 0.45, 0.45)),
        # N17: one PDF line per provenance line (the in-sample caveat apart).
        *((line, 7.5, (0.45, 0.45, 0.45)) for line in threshold_provenance_lines(thresholds)),
    ])
    # X1: the "기록되지 않은 파일" section — count and reasons, always present.
    unrecorded = unrecorded_files(list(items), summary)
    layout.boxed_text(UNRECORDED_SECTION_TITLE, [
        (line, 8.0 if index == 0 else 7.5, (0.1, 0.2, 0.4) if index == 0 else (0.3, 0.3, 0.3))
        for index, line in enumerate(unrecorded.lines())
    ])

    columns = layout.columns(FORENSIC_TABLE_WEIGHTS)
    header_color = (0.15, 0.2, 0.35)

    def table_header(current: PdfLayout) -> None:
        current.draw_row(
            columns,
            [Cell(index, label, 7.5, header_color) for index, label in enumerate(FORENSIC_TABLE_HEADERS)],
            fill=(0.92, 0.94, 0.97), rule=(0.8, 0.85, 0.9),
        )

    layout.ensure_space(60.0)
    table_header(layout)
    for idx, item in enumerate(items, start=1):
        res = item.result
        # B3: a row without a verdict shows its Korean status, never the raw code.
        band_str = VERDICT_LABELS[res.verdict_code] if res and is_verdict_row(item.status, True) else status_label(item.status or "failed")
        grade_str = ("참고" if res.grade == Grade.REFERENCE else "근거") if res else "-"
        kinds_str = evidence_counts_text(res) if res else "-"
        failed_checks = [entry for entry in res.coverage if entry.status == CoverageStatus.FAILED] if res else []
        top_item = deciding_evidence(res) if res else None
        if failed_checks:
            sig_str = "실패: " + ", ".join(check_label(entry.check) for entry in failed_checks)
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
        # G16/S2: the full 64-hex digest (wrapped inside the column), or the
        # evidence statement's "why no hash" wording.
        hash_line = f"SHA-256: {sha256_hex}" if sha256_hex else f"SHA-256: {_hash_unavailable_reason(item)}"
        # S1: archive members keep "<container>::<member>" — shown in full, wrapped.
        disp_path = display_path(item.path, redact_paths=redact_paths)
        layout.draw_row(
            columns,
            [
                Cell(0, str(idx), 7.5, (0.3, 0.3, 0.3)),
                Cell(1, f"{disp_path}\n{hash_line}", 7.0, (0.1, 0.1, 0.1)),
                Cell(2, band_str, 7.5, band_color),
                Cell(3, grade_str, 7.5, (0.1, 0.1, 0.1)),
                Cell(4, kinds_str, 7.0, (0.3, 0.3, 0.3)),
                Cell(5, sig_str, 7.0, (0.2, 0.2, 0.2)),
            ],
            fill=(0.96, 0.97, 0.98) if idx % 2 == 0 else None,
            on_new_page=table_header,
        )
    layout.y += 6.0

    if _has_reference_grade(items):
        layout.text(layout.left, layout.right, TEXT_LEGAL_LIMITATION, 8.0, (0.55, 0.3, 0.0))
    layout.text(layout.left, layout.right, FORENSIC_DECISION_NOTE, 7.0, (0.4, 0.4, 0.4), gap=4.0)

    _wa = coverage.get("weights_available", 0) if coverage else 0
    if isinstance(_wa, (int, float)) and _wa > 0:
        engine_text = f"사법절차 적격성 고지: 본 감정서는 {firm_name}의 뉴럴 앙상블 + 로컬 휴리스틱 분석에 따른 스크리닝 결과입니다."
    elif coverage is not None:
        engine_text = "사법절차 적격성 고지: 본 감정서는 신경망 가중치 미탑재 상태의 로컬 휴리스틱 분석에 따른 스크리닝 결과입니다 (뉴럴 엔진 미실행)."
    else:
        engine_text = f"사법절차 적격성 고지: 본 감정서는 {firm_name}의 로컬 스크리닝 분석 결과입니다."
    if hashed == len(items):
        integrity_text = "무결성 확약: 상기 기재된 증거물 일체는 SHA-256 해시 검증을 필하였으며, 채증·보존 과정에서 위변조되지 않았음을 확인합니다."
    else:
        integrity_text = f"무결성 고지: 해시가 계산된 {hashed}건은 채증 시점 값을 기재하였으며, 해시 불가 {len(items) - hashed}건은 별도 표기하였습니다."
    layout.boxed_text("", [
        (engine_text, 7.5, (0.4, 0.4, 0.4)),
        (integrity_text, 7.5, (0.4, 0.4, 0.4)),
    ])
    layout.ensure_space(layout.line_height(9.0) + 4.0)
    layout.text(layout.left, layout.right, f"{firm_name} 감정관 (직인생략)", 9.0, header_blue, align=2, gap=6.0)
    for line in signature_lines_ko(signed_report):
        layout.text(layout.left, layout.right, line, 6.5, (0.35, 0.35, 0.35), gap=0.5)

    layout.footer(lambda page_no, total: f"- {page_no} / {total} -")
    return layout.to_bytes()


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
    <div class="metric">정확도<br><strong>{escape(str(metrics.get("accuracy", "-")))}</strong></div>
    <div class="metric">정밀도<br><strong>{escape(str(metrics.get("precision", "-")))}</strong></div>
    <div class="metric">재현율 [95% CI]<br><strong>{escape(format_ci(metrics.get("recall"), metrics.get("recall_ci")))}</strong></div>
    <div class="metric">오탐률(FPR) [95% CI]<br><strong>{escape(format_ci(metrics.get("false_positive_rate"), metrics.get("false_positive_rate_ci")))}</strong></div>
    <div class="metric">AUROC [95% CI]<br><strong>{escape(format_ci(metrics.get("auroc"), metrics.get("auroc_ci")))}</strong></div>
    <div class="metric">양성 / 음성 표본 수<br><strong>{escape(str(metrics.get("n_pos", "-")))} / {escape(str(metrics.get("n_neg", "-")))}</strong></div>
    <div class="metric">오탐 / 미탐<br><strong>{len(false_positives)} / {len(false_negatives)}</strong></div>
  </div>
  <p>점수 기준: {escape(SCORE_BASIS_LABELS.get(str(payload.get("score_basis", "")), str(payload.get("score_basis", "보정 전 원점수"))))} — {escape(str(payload.get("score_basis_note", "")))}</p>
  <p>혼동 행렬: {escape(str(confusion))}</p>
  <table>
    <thead><tr><th>라벨</th><th>예측</th><th>점수</th><th>출처</th><th>출처 추정</th><th>파일</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""
    output.write_text(body, encoding="utf-8")


def _html_row(item: ScanItem, *, redact_paths: bool, allow_path: Callable[[str], bool] | None = None) -> str:
    result = item.result
    path_cell = escape(display_path(item.path, redact_paths=redact_paths))
    if result is None or not is_verdict_row(item.status, True):
        # B3: Korean status (건너뜀/미지원/실패/중복), never the raw code.
        return (
            "<tr>"
            f"<td>{escape(status_label(item.status))}</td>"
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
    # S1: archive members keep "<container>::<member>" even when redacted.
    return display_path(path, redact_paths=redact_paths)


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
    return f'<img class="heatmap" alt="히트맵" src="data:image/png;base64,{encoded}">'


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

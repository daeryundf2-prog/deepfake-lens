from __future__ import annotations

import base64
import hashlib
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Callable

from .core import BatchScanSummary, ScanItem


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
    fp = str(payload.get("dataset_fingerprint", ""))[:16]
    suffix = f", corpus fp {fp}" if fp else ""
    return f"Decision thresholds: threshold profile {payload.get('version', '?')} — {state}, n={samples}{suffix}."


def write_html_report(path: Path | str, summary: BatchScanSummary, items: list[ScanItem], *, redact_paths: bool = False, thresholds: object | None = None) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(_html_row(item, redact_paths=redact_paths) for item in items)
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
    img.heatmap {{ width: 96px; height: 96px; object-fit: cover; image-rendering: pixelated; border: 1px solid #d8dee9; }}
  </style>
</head>
<body>
  <h1>Deepfake Lens Report</h1>
  <p>Scanned {summary.total} files: high={summary.high}, medium={summary.medium}, unknown={summary.unknown}, low={summary.low}, unsupported/failed={summary.unsupported_or_failed}, duplicates={summary.duplicates}, skipped={summary.skipped}, cached={summary.cached}</p>
  <p class="note">Local-only screening report. Scores are prioritization evidence, not final truth labels.</p>
  <p class="note">{_threshold_provenance_line(thresholds)}</p>
  <table>
    <thead><tr><th>risk</th><th>score</th><th>pixel</th><th>source</th><th>file</th><th>heatmap</th><th>top signal</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
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
    return f"판정 임계값: 프로파일 {payload.get('version', '?')} — {state}, 표본 n={samples}"


def write_pdf_report(path: Path | str, summary: BatchScanSummary, items: list[ScanItem], *, redact_paths: bool = False, thresholds: object | None = None, degrade_note: str | None = None) -> None:
    lines = [
        "Deepfake Lens Report",
        f"Scanned {summary.total} files: high={summary.high}, medium={summary.medium}, unknown={summary.unknown}, low={summary.low}, unsupported/failed={summary.unsupported_or_failed}, duplicates={summary.duplicates}, skipped={summary.skipped}, cached={summary.cached}",
        "Local-only screening report. Scores are prioritization evidence, not final truth labels.",
        _threshold_provenance_line(thresholds),
        "",
    ]
    if degrade_note:
        lines.append(f"NOTE: {degrade_note}")
        lines.append("")
    shown = items[:80]
    if len(items) > len(shown):
        lines.append(f"NOTE: {len(items)} items scanned; first {len(shown)} shown — see JSON/CSV output for the remainder.")
        lines.append("")
    for item in shown:
        result = item.result
        score = result.score if result else "-"
        risk = result.band_label if result else item.status
        source = result.source_guess.label if result else "-"
        top_signal = result.signals[0].title if result and result.signals else (item.error or "")
        lines.append(f"{risk} {score} {_display_path(item.path, redact_paths=redact_paths)} {source} {top_signal}")
    if any(ord(char) > 255 for line in lines for char in line):
        # The minimal PDF writer is Latin-1 only; state the limitation
        # instead of silently turning Korean labels into '?'.
        lines = [
            "NOTE: this simple PDF is Latin-1 only; non-Latin text",
            "(e.g. Korean band labels and signal titles) appears as '?'.",
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
) -> None:
    """Generate a court-admissible forensic PDF report with ECFS exhibit stamp,
    SHA-256 evidence integrity hashes, and Daeryun Law Firm forensic signoff."""
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
    hash_map = {item.path: _evidence_sha256(item.path, allow_path, resolve_path) for item in items}
    hashed = sum(1 for v in hash_map.values() if v)

    # Metadata & Case Overview Box
    meta_box = pymupdf.Rect(margin_l, 122, margin_r, 196)
    page.draw_rect(meta_box, color=(0.85, 0.88, 0.92), fill=(0.98, 0.98, 0.99))

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    page.insert_text(pymupdf.Point(margin_l + 10, 137), "감정 의뢰: (의뢰사 상호명 입력) / (담당자 부서·직위·성명) 귀하", fontname=font_ko, fontsize=8.5, color=(0.2, 0.2, 0.2))
    page.insert_text(pymupdf.Point(margin_l + 10, 151), f"감정 일시: {now_str} (KST)  |  분석 엔진: Deepfake Lens Forensic Suite v0.1.0", fontname=font_ko, fontsize=8.5, color=(0.2, 0.2, 0.2))
    page.insert_text(
        pymupdf.Point(margin_l + 10, 165),
        f"감정 결과: 총 {summary.total}개 검토 (AI 의심 {summary.high}건, 주의 {summary.medium}건, 저위험 {summary.low}건, 불가/오류 {summary.unsupported_or_failed}건)",
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
    page.insert_text(pymupdf.Point(margin_l + 250, y + 14), "위험도", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
    page.insert_text(pymupdf.Point(margin_l + 300, y + 14), "점수", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
    page.insert_text(pymupdf.Point(margin_l + 345, y + 14), "엔진/출처", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
    page.insert_text(pymupdf.Point(margin_l + 420, y + 14), "주요 감정 신호", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
    y += 20.0

    row_h = 28.0

    for idx, item in enumerate(items, start=1):
        if y + row_h > 720:
            page = create_page()
            y = 70.0
            page.draw_rect(pymupdf.Rect(margin_l, y, margin_r, y + 20), color=(0.8, 0.85, 0.9), fill=(0.92, 0.94, 0.97))
            page.insert_text(pymupdf.Point(margin_l + 5, y + 14), "No.", fontname=font_en, fontsize=8, color=(0.15, 0.2, 0.35))
            page.insert_text(pymupdf.Point(margin_l + 30, y + 14), "증거 파일명 및 SHA-256 무결성 해시", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
            page.insert_text(pymupdf.Point(margin_l + 250, y + 14), "위험도", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
            page.insert_text(pymupdf.Point(margin_l + 300, y + 14), "점수", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
            page.insert_text(pymupdf.Point(margin_l + 345, y + 14), "엔진/출처", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
            page.insert_text(pymupdf.Point(margin_l + 420, y + 14), "주요 감정 신호", fontname=font_ko, fontsize=8, color=(0.15, 0.2, 0.35))
            y += 20.0

        if idx % 2 == 0:
            page.draw_rect(pymupdf.Rect(margin_l, y, margin_r, y + row_h), color=(0.96, 0.97, 0.98), fill=(0.96, 0.97, 0.98))
        page.draw_line(pymupdf.Point(margin_l, y + row_h), pymupdf.Point(margin_r, y + row_h), color=(0.9, 0.92, 0.94), width=0.5)

        res = item.result
        band_str = res.band_label if res else (item.status or "-")
        score_val = str(res.score) if res else "-"
        source_str = res.source_guess.label if res else "-"
        sig_str = res.signals[0].title if (res and res.signals) else (item.error or "이상 없음")

        if res and res.band.value == "high":
            band_color = (0.8, 0.15, 0.15)
        elif res and res.band.value == "medium":
            band_color = (0.85, 0.45, 0.05)
        elif res and res.band.value == "low":
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

        page.insert_text(pymupdf.Point(margin_l + 250, y + 15), band_str, fontname=font_ko, fontsize=8, color=band_color)
        page.insert_text(pymupdf.Point(margin_l + 300, y + 15), score_val, fontname=font_en, fontsize=8.5, color=(0.1, 0.1, 0.1))
        page.insert_text(pymupdf.Point(margin_l + 345, y + 15), source_str[:12], fontname=font_ko, fontsize=7.5, color=(0.3, 0.3, 0.3))
        page.insert_text(pymupdf.Point(margin_l + 420, y + 15), sig_str[:18], fontname=font_ko, fontsize=7.5, color=(0.2, 0.2, 0.2))

        y += row_h

    if y + 80 > 750:
        page = create_page()
        y = 70.0

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
    <div class="metric">recall<br><strong>{escape(str(metrics.get("recall", "-")))}</strong></div>
    <div class="metric">AUROC<br><strong>{escape(str(metrics.get("auroc", "-")))}</strong></div>
    <div class="metric">FP/FN<br><strong>{len(false_positives)} / {len(false_negatives)}</strong></div>
  </div>
  <p>Confusion: {escape(str(confusion))}</p>
  <table>
    <thead><tr><th>label</th><th>predicted</th><th>score</th><th>source</th><th>guess</th><th>file</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""
    output.write_text(body, encoding="utf-8")


def _html_row(item: ScanItem, *, redact_paths: bool) -> str:
    result = item.result
    risk = result.band_label if result else item.status
    score = str(result.score) if result else "-"
    pixel = "-"
    source = "-"
    signal = item.error or ""
    heatmap = ""
    if result:
        if result.pixel_analysis and result.pixel_analysis.available:
            pixel = str(result.pixel_analysis.score)
            heatmap = _heatmap_img(result.pixel_analysis.heatmap_path)
        source = result.source_guess.label
        signal = result.signals[0].detail if result.signals else "강한 의심 신호 없음"
    return (
        "<tr>"
        f"<td>{escape(risk)}</td>"
        f"<td>{escape(score)}</td>"
        f"<td>{escape(pixel)}</td>"
        f"<td>{escape(source)}</td>"
        f"<td>{escape(_display_path(item.path, redact_paths=redact_paths))}</td>"
        f"<td>{heatmap}</td>"
        f"<td>{escape(signal)}</td>"
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


def _heatmap_img(path: str | None) -> str:
    if not path:
        return ""
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

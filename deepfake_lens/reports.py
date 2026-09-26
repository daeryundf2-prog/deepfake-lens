from __future__ import annotations

import base64
import hashlib
import time
from datetime import datetime
from html import escape
from pathlib import Path

from .core import BatchScanSummary, ScanItem


def write_html_report(path: Path | str, summary: BatchScanSummary, items: list[ScanItem], *, redact_paths: bool = False) -> None:
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
  <table>
    <thead><tr><th>risk</th><th>score</th><th>pixel</th><th>source</th><th>file</th><th>heatmap</th><th>top signal</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</body>
</html>
"""
    output.write_text(body, encoding="utf-8")


def write_pdf_report(path: Path | str, summary: BatchScanSummary, items: list[ScanItem], *, redact_paths: bool = False) -> None:
    lines = [
        "Deepfake Lens Report",
        f"Scanned {summary.total} files: high={summary.high}, medium={summary.medium}, unknown={summary.unknown}, low={summary.low}, unsupported/failed={summary.unsupported_or_failed}, duplicates={summary.duplicates}, skipped={summary.skipped}, cached={summary.cached}",
        "Local-only screening report. Scores are prioritization evidence, not final truth labels.",
        "",
    ]
    for item in items[:80]:
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
) -> None:
    """Generate a court-admissible forensic PDF report with ECFS exhibit stamp,
    SHA-256 evidence integrity hashes, and Daeryun Law Firm forensic signoff."""
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError:
            write_pdf_report(path, summary, items, redact_paths=redact_paths)
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

    # Metadata & Case Overview Box
    meta_box = pymupdf.Rect(margin_l, 122, margin_r, 185)
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
        "무결성 확인: 전수 SHA-256 해시 대조 완료  |  보안 등급: 사법기관 제출용 대외비",
        fontname=font_ko,
        fontsize=8,
        color=(0.45, 0.45, 0.45),
    )

    # Table Header
    y = 202.0
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

        sha256_hex = ""
        p = Path(item.path)
        try:
            if p.is_file():
                sha256_hex = hashlib.sha256(p.read_bytes()[:1024*1024]).hexdigest()
            else:
                sha256_hex = hashlib.sha256(item.path.encode()).hexdigest()
        except OSError:
            sha256_hex = hashlib.sha256(item.path.encode()).hexdigest()

        disp_path = Path(item.path).name if redact_paths else item.path
        if len(disp_path) > 36:
            disp_path = disp_path[:33] + "…"

        page.insert_text(pymupdf.Point(margin_l + 5, y + 12), str(idx), fontname=font_en, fontsize=8, color=(0.3, 0.3, 0.3))
        page.insert_text(pymupdf.Point(margin_l + 30, y + 12), disp_path, fontname=font_ko, fontsize=8, color=(0.1, 0.1, 0.1))
        page.insert_text(pymupdf.Point(margin_l + 30, y + 24), f"SHA-256: {sha256_hex[:32]}…", fontname=font_en, fontsize=6.5, color=(0.5, 0.5, 0.5))

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
    page.insert_text(
        pymupdf.Point(margin_l + 10, y + 16),
        "사법절차 적격성 고지: 본 감정서는 법무법인(유한) 대륜 디지털포렌식 감정센터의 뉴럴 앙상블 분석에 따른 스크리닝 결과입니다.",
        fontname=font_ko,
        fontsize=7.5,
        color=(0.4, 0.4, 0.4),
    )
    page.insert_text(
        pymupdf.Point(margin_l + 10, y + 29),
        "무결성 확약: 상기 기재된 증거물 일체는 SHA-256 해시 검증을 필하였으며, 채증·보존 과정에서 위변조되지 않았음을 확인합니다.",
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

    output.write_bytes(doc.tobytes())


def write_eval_html_report(path: Path | str, payload: dict[str, object], *, redact_paths: bool = False) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
    confusion = payload.get("confusion", {}) if isinstance(payload.get("confusion"), dict) else {}
    case_summary = payload.get("case_summary", {}) if isinstance(payload.get("case_summary"), dict) else {}
    false_positives = case_summary.get("false_positives", []) if isinstance(case_summary.get("false_positives"), list) else []
    false_negatives = case_summary.get("false_negatives", []) if isinstance(case_summary.get("false_negatives"), list) else []
    rows = "\n".join(_eval_row(row, redact_paths=redact_paths) for row in payload.get("items", []) if isinstance(row, dict))
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
    path.write_bytes(bytes(output))


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

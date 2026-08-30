from __future__ import annotations

import base64
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

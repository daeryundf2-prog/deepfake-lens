"""Output shapes of the standalone subcommands (D1).

Two shapes only:

* **analysis_result** — the three-verdict contract produced by
  ``analysis_api.analyze_path`` (the same path as ``scan``). Used by every
  command that answers "is it fake": ``forensic``, ``classify``,
  ``multimodal FILE…``, ``explain FILE``, ``agent``, ``legal-report``.
* **layer_diagnostic** — one layer's raw, unmeasured numbers with the fixed
  notice (:mod:`deepfake_lens.layer_diagnostic`). Used by the per-layer
  commands (``audio``, ``video-analysis``, ``text-advanced``,
  ``pixel-analysis``, ``inpaint``, ``prnu``, ``rppg``, ``face``, ``avatar``,
  ``3d``, ``realtime``, ``faceswap-seam``, ``compare``, ``ml-classify``).

Neither shape carries a ``band`` key; the analysis_result carries
``verdict_code`` (manipulation_evidence / authenticity_evidence /
undetermined) and never a score that was not calibrated.

B1: an analysis_result built from a path (:func:`analysis_result_for_path`)
also carries ``rows`` — the raw scan rows ``scan`` reports for the file
(one row, or an archive's member rows plus its container row). The
top-level verdict is the file's own row (the container row of an
archive); the text rendering lists the member rows in the scan table's
wording.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from .layer_diagnostic import (
    ANALYSIS_RESULT_KIND,
    LAYER_DIAGNOSTIC_NOTICE,
    UNAVAILABLE_BAND,
    format_layer_diagnostic,
    to_layer_diagnostic,
)
from .result_text import coverage_entry_line, display_name, escape_controls, evidence_qualifiers_short, grade_label_text
from .result_types import VERDICT_LABELS, GRADE_LABELS, Grade, ScanItem, Verdict, status_label
from .json_text import json_dumps

ANALYSIS_RESULT_NOTICE = "결론은 `scan`과 같은 경로(analysis_api.analyze_path)로 산출되었습니다."
# Result fields that make up the three-verdict contract. Legacy derived
# fields (band, band_label, score, ai_score, signals) are left out on purpose.
_RESULT_FIELDS = (
    "verdict_code",
    "verdict",
    "grade",
    "evidence",
    "coverage",
    "limitations",
    "reference_signals",
    "source_guess",
    "probability",
    "probability_ci",
    "score_is_calibrated",
    "next_checks",
)
_HASH_CHUNK_BYTES = 1024 * 1024


def file_sha256(path: Path | str) -> str | None:
    """SHA-256 of a file's bytes (None when it cannot be read)."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def analysis_result_payload(item: ScanItem, *, command: str, sha256: str | None = None) -> dict[str, Any]:
    """The analysis_result JSON for one analyzed file."""
    payload: dict[str, Any] = {
        "kind": ANALYSIS_RESULT_KIND,
        "command": command,
        "notice": ANALYSIS_RESULT_NOTICE,
        "path": item.path,
        "file_kind": item.kind,
        "status": item.status,
        "sha256": item.sha256 or sha256,
    }
    if item.result is None:
        # Unsupported or failed file: no conclusion is possible.
        payload.update(
            verdict_code=Verdict.UNDETERMINED.value,
            verdict_label=VERDICT_LABELS[Verdict.UNDETERMINED],
            # G1: the Korean status label, never the raw status code.
            verdict=f"{VERDICT_LABELS[Verdict.UNDETERMINED]} — {item.error or status_label(item.status)}",
            grade=Grade.REFERENCE.value,
            grade_label=GRADE_LABELS[Grade.REFERENCE],
            evidence=[],
            coverage=[],
            limitations=[item.error] if item.error else [],
        )
        return payload
    result = item.result.to_json()
    for field in _RESULT_FIELDS:
        if field in result:
            payload[field] = result[field]
    payload["verdict_label"] = VERDICT_LABELS[item.result.verdict_code]
    payload["grade_label"] = GRADE_LABELS[item.result.grade]
    return payload


def analysis_result_from_rows(
    rows: list[ScanItem], *, command: str, sha256: str | None = None, path: str | None = None,
) -> dict[str, Any]:
    """analysis_result for one file from its scan rows (B1).

    The conclusion fields come from the file's own row (an archive's
    container row); ``rows`` holds every row raw, exactly as ``scan``
    reports it. ``path`` overrides the displayed target (the path the
    user named), ``sha256`` is the fallback digest when the row has none.
    """
    from .analysis_api import primary_row

    primary = primary_row(rows)
    payload = analysis_result_payload(primary, command=command, sha256=sha256)
    if path is not None:
        payload["path"] = path
    payload["rows"] = [standalone_row(row) for row in rows]
    return payload


# Legacy result keys a standalone shape never carries (D1): the band is
# derived from verdict_code (band_for_verdict) and adds no information.
STANDALONE_ROW_DROPPED_KEYS = ("band", "band_label")


def standalone_row(row: ScanItem) -> dict[str, Any]:
    """A scan row as ``scan`` emits it, minus the legacy band keys (B1/D1)."""
    data = row.to_json()
    result = data.get("result")
    if isinstance(result, dict):
        for key in STANDALONE_ROW_DROPPED_KEYS:
            result.pop(key, None)
    return data


def analysis_result_for_path(
    path: Path | str, options: Any, *, command: str, thresholds: Any = None,
) -> tuple[dict[str, Any], list[ScanItem]]:
    """``(analysis_result, rows)`` for a file — the folder scan's rows for it (B1)."""
    from .analysis_api import analyze_rows, is_symlink_path

    rows = analyze_rows(path, options, thresholds=thresholds)
    # G6: a symbolic link is a skipped row; its target is never hashed.
    digest = None if is_symlink_path(path) else file_sha256(path)
    payload = analysis_result_from_rows(rows, command=command, sha256=digest, path=str(path))
    return payload, rows


SYMLINK_LAYER_NOTE = "심볼릭 링크 — 링크를 따라가지 않으므로 이 계층을 실행하지 않았습니다(scan과 같은 규칙)"


def symlink_layer(layer: str, layer_label: str) -> dict[str, Any]:
    """The layer diagnostic for a symbolic-link target: not run, target not read (G6)."""
    return to_layer_diagnostic(
        layer,
        {"reference_band": UNAVAILABLE_BAND, "reference_note": SYMLINK_LAYER_NOTE, "signals": [], "limitations": [SYMLINK_LAYER_NOTE]},
        layer_label=layer_label,
    )


def member_rows_text(raw_rows: Any) -> list[str]:
    """The member rows of an archive result in the scan table's wording (B1).

    ``raw_rows`` is the ``rows`` list of an analysis_result / legal report.
    Empty for a regular file (one row).
    """
    if not isinstance(raw_rows, list) or len(raw_rows) < 2:
        return []
    from .cli_render import TABLE_HEADER, TABLE_RULE, table_row_text
    from .serialization import _scan_item_from_json

    items = [_scan_item_from_json(dict(row)) for row in raw_rows if isinstance(row, Mapping)]
    members = [item for item in items if item.member is not None]  # R9-1: by the member field
    if not members:
        return []
    return [
        f"구성 파일 {len(members)}개 (scan 표와 같은 행):",
        TABLE_HEADER,
        TABLE_RULE,
        *(table_row_text(item) for item in members),
    ]


def analyze_text_payload(text: str, options: Any, *, command: str, thresholds: Any = None) -> dict[str, Any]:
    """analysis_result for raw text: written to a temp .txt, analyzed like a file."""
    from .analysis_api import analyze_path

    tmp_name = ""
    try:
        # delete=False: Windows cannot reopen a delete=True temp file.
        with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as tmp:
            tmp.write(text)
            tmp_name = tmp.name
        item = analyze_path(tmp_name, options, thresholds=thresholds)
        payload = analysis_result_payload(item, command=command, sha256=file_sha256(tmp_name))
    finally:
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)
    payload["path"] = "(입력 텍스트)"
    return payload


def format_analysis_result(payload: Mapping[str, Any]) -> str:
    """Korean text rendering of an analysis_result (``--format table``).

    G1 (round 5): only labels reach the examiner — the verdict and grade
    labels, the evidence qualifiers ("결정적/합성/강") and the coverage
    check/status labels ("얼굴 검사: 미실행 — …"); the JSON codes
    (``manipulation_evidence``, ``deterministic``, ``ran``, ``model:<name>``)
    stay in ``--format json``.
    """
    verdict_code = str(payload.get("verdict_code") or Verdict.UNDETERMINED.value)
    verdict_label = payload.get("verdict_label") or VERDICT_LABELS.get(Verdict(verdict_code), VERDICT_LABELS[Verdict.UNDETERMINED])
    grade_text = payload.get("grade_label") or grade_label_text(payload.get("grade"))
    lines = [
        f"[결론] {verdict_label} · 등급: {grade_text}",
        # R10-1: the file name and every echoed string through display_name /
        # escape_controls — a CR/LF/ESC in a name cannot add or rewrite a line.
        f"대상: {display_name(payload.get('path'))}",
    ]
    if payload.get("sha256"):
        lines.append(f"SHA-256: {payload['sha256']}")
    if payload.get("verdict"):
        lines.append(str(payload["verdict"]))
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), list) else []
    if evidence:
        lines.append("근거:")
        for item in evidence:
            if isinstance(item, Mapping):
                qualifiers = evidence_qualifiers_short(item.get("kind"), item.get("direction"), item.get("strength"))
                lines.append(f"  - [{qualifiers}] {escape_controls(item.get('title'))}: {escape_controls(item.get('detail'))}")
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), list) else []
    if coverage:
        lines.append("검사 범위:")
        for entry in coverage:
            if isinstance(entry, Mapping):
                lines.append(f"  - {escape_controls(coverage_entry_line(dict(entry)))}")
    for key, title in (("rule", "결정 규칙"),):
        if payload.get(key):
            lines.append(f"{title}: {payload[key]}")
    member_rules = payload.get("member_rules")
    if isinstance(member_rules, list) and member_rules:
        lines.append("구성 파일별 결정 규칙:")
        for entry in member_rules:
            if isinstance(entry, Mapping):
                lines.append(f"  - {display_name(entry.get('path'))}: {escape_controls(entry.get('rule'))}")
    lines.extend(member_rows_text(payload.get("rows")))
    lines.append(str(payload.get("notice", ANALYSIS_RESULT_NOTICE)))
    return "\n".join(lines)


def emit(payload: Mapping[str, Any], *, fmt: str, json_out: Path | None, text: str | None = None) -> None:
    """Print ``payload`` as JSON or text and optionally write it to ``json_out``."""
    rendered = json_dumps(payload, ensure_ascii=False, indent=2)
    if json_out is not None:
        from .cli_render import _write_json_out

        _write_json_out(json_out, rendered + "\n")
    if fmt == "json":
        print(rendered)
        return
    if text is None:
        text = (
            format_layer_diagnostic(payload)
            if payload.get("kind") != ANALYSIS_RESULT_KIND
            else format_analysis_result(payload)
        )
    print(text)


def emit_layer(
    args: Any,
    layer: str,
    layer_label: str,
    raw: Mapping[str, Any],
    *,
    subject: str | None = None,
) -> int:
    """Print one layer diagnostic for a standalone command; exit code 0."""
    diag = to_layer_diagnostic(layer, raw, layer_label=layer_label, subject=subject)
    emit(diag, fmt=getattr(args, "format", "json"), json_out=getattr(args, "json_out", None))
    return 0


def gated_pixel_layer(path: Path) -> dict[str, Any]:
    """Quick pixel pre-screen behind the photo/non-photo gate (WP-D, D3).

    A non-photo (or too small) image is not pre-screened at all: the layer
    reports ``reference_band: unavailable`` with the gate reason, exactly as
    ``scan`` records ``pixel: skipped``.
    """
    from .image_class import classify_image
    from .pixel_analyzer import analyze_pixels

    try:
        image_class = classify_image(path)
    except Exception as exc:  # noqa: BLE001 - the gate failing is reported, not hidden
        from .checks import failure_reason

        return {
            "score": 0,
            "reference_band": UNAVAILABLE_BAND,
            "reference_note": f"이미지 유형 판별 실패 — {failure_reason(exc)}",
            "signals": [],
            "limitations": ["사진/비사진 판별이 실패해 픽셀 사전 선별을 수행하지 않았습니다."],
        }
    if not image_class.is_photo:
        reason = image_class.skip_reason() or f"사진 아님: {image_class.kind}"
        return {
            "score": 0,
            "reference_band": UNAVAILABLE_BAND,
            "reference_note": reason,
            "image_class": image_class.kind,
            "signals": [],
            "limitations": [f"{reason} — 생성 탐지기는 사진에서만 측정 의미가 있습니다."],
        }
    data = analyze_pixels(path).to_json()
    data["image_class"] = image_class.kind
    return data


# Bytes read for tool-marker attribution (same cap as the former /api/classify).
TOOL_ATTRIBUTION_MAX_BYTES = 64 * 1024 * 1024
_TOOL_TEXT_EXTENSIONS = frozenset({".txt", ".md", ".py", ".js", ".json", ".csv", ".log"})


def tool_attribution(path: Path) -> Any:
    """Marker-string tool attribution (classifier.py) for ``classify``.

    Reference only: a marker match names a candidate tool; whether the file
    is generated is the analysis_result verdict next to it.
    """
    from .classifier import classify_metadata, classify_text_content
    from .png import read_png_metadata

    with Path(path).open("rb") as handle:
        data = handle.read(TOOL_ATTRIBUTION_MAX_BYTES)
    if Path(path).suffix.lower() in _TOOL_TEXT_EXTENSIONS:
        return classify_text_content(data.decode("utf-8", errors="ignore"))
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return classify_metadata(read_png_metadata(data))
    if data[:2] == b"\xff\xd8":
        return classify_metadata({"format": "jpeg", "size": str(len(data))})
    return classify_metadata({})


def combined_verdict(payloads: Iterable[Mapping[str, Any]]) -> tuple[str, str]:
    """Combine per-file analysis results with the same rule order as ``decide``.

    Any file with manipulation evidence → manipulation_evidence (rule 2/4);
    otherwise any undetermined file → undetermined; authenticity only when
    every file has it. Returns ``(verdict_code, explanation)``.
    """
    codes = [str(p.get("verdict_code") or Verdict.UNDETERMINED.value) for p in payloads]
    if not codes:
        return Verdict.UNDETERMINED.value, "분석한 파일이 없습니다."
    if Verdict.MANIPULATION_EVIDENCE.value in codes:
        return Verdict.MANIPULATION_EVIDENCE.value, "하나 이상의 파일에 조작·생성 근거가 있습니다."
    if all(code == Verdict.AUTHENTICITY_EVIDENCE.value for code in codes):
        return Verdict.AUTHENTICITY_EVIDENCE.value, "모든 파일에 원본성 근거가 있습니다."
    return Verdict.UNDETERMINED.value, "판단 불가인 파일이 있어 종합 결론을 유보합니다."


__all__ = [
    "ANALYSIS_RESULT_NOTICE",
    "LAYER_DIAGNOSTIC_NOTICE",
    "analysis_result_for_path",
    "analysis_result_from_rows",
    "analysis_result_payload",
    "SYMLINK_LAYER_NOTE",
    "analyze_text_payload",
    "combined_verdict",
    "emit",
    "emit_layer",
    "file_sha256",
    "format_analysis_result",
    "gated_pixel_layer",
    "member_rows_text",
    "symlink_layer",
]

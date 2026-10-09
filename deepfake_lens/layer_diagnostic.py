"""Layer diagnostics: the unmeasured per-layer output of standalone commands (D1).

Before phase-0 fix 1 every layer module (audio, video temporal, inpainting,
pixel, rPPG, PRNU, avatar, 3D, realtime, face-swap seam …) turned its raw
heuristic sum into a ``band`` with the old 67/35 cutoffs and a verdict
sentence ("의심 신호가 강합니다/적습니다"). Those cutoffs were never
measured, and the standalone subcommands printed them as if they were a
conclusion — the "낮음" a phase-0 scan can never produce.

A layer module now reports only:

* ``reference_band`` — :data:`REFERENCE_BAND` when the layer ran (its numbers
  are reference only) or :data:`UNAVAILABLE_BAND` when it could not run;
  never high/medium/low.
* ``reference_note`` — a neutral sentence: the raw score with the
  "미측정" caveat, or the reason the layer could not run.

:func:`to_layer_diagnostic` wraps any layer payload into the JSON shape the
standalone commands and the API print (``kind: "layer_diagnostic"``,
``measured: false`` and the fixed :data:`LAYER_DIAGNOSTIC_NOTICE`). No layer
module of this package emits ``band``/``band_label``/``verdict`` any more
(``tests/test_no_legacy_bands.py`` enforces it); the wrapper still strips
those keys at every depth so an old saved payload or an embedded legacy
record can never carry an old-contract band to a front end.

Conclusions come only from ``scan`` / ``analysis_api.analyze_path``.
"""

from __future__ import annotations

from typing import Any, Mapping

LAYER_DIAGNOSTIC_KIND = "layer_diagnostic"
ANALYSIS_RESULT_KIND = "analysis_result"
LAYER_DIAGNOSTIC_TITLE = "계층 진단(참고 신호 · 미측정)"
LAYER_DIAGNOSTIC_NOTICE = "이 출력은 측정되지 않은 참고 신호이며 결론이 아닙니다. 결론은 `scan`을 사용하십시오."

# reference_band values. Deliberately not band words: a layer is either
# "ran — numbers are reference" or "could not run".
REFERENCE_BAND = "reference"
UNAVAILABLE_BAND = "unavailable"
REFERENCE_BANDS = frozenset({REFERENCE_BAND, UNAVAILABLE_BAND})

# Keys of the pre-phase-0 layer contract. Stripped from every diagnostic.
LEGACY_RESULT_KEYS = frozenset({"band", "band_label", "verdict"})
# Legacy band value that meant "could not analyze".
_LEGACY_UNKNOWN = "unknown"


def raw_score_note(layer_label: str, score: int | float) -> str:
    """Neutral reference_note for a layer that ran: the raw sum, no cutoff."""
    return f"{layer_label} 참고 원점수 {score}/100 — 측정되지 않은 휴리스틱 합계이며 결론이 아닙니다."


def reference_band_for(available: bool) -> str:
    return REFERENCE_BAND if available else UNAVAILABLE_BAND


def strip_legacy(value: Any) -> Any:
    """Drop band/band_label/verdict keys at every depth of a JSON value."""
    if isinstance(value, Mapping):
        return {str(k): strip_legacy(v) for k, v in value.items() if k not in LEGACY_RESULT_KEYS}
    if isinstance(value, (list, tuple)):
        return [strip_legacy(v) for v in value]
    return value


def _legacy_reference(payload: Mapping[str, Any], layer_label: str) -> tuple[str, str]:
    """reference_band/note for a payload that still carries band/verdict."""
    band = str(payload.get("band") or "")
    score = payload.get("score")
    if band == _LEGACY_UNKNOWN:
        return UNAVAILABLE_BAND, str(payload.get("verdict") or f"{layer_label} 분석을 수행하지 못했습니다.")
    if isinstance(score, (int, float)):
        return REFERENCE_BAND, raw_score_note(layer_label, score)
    return REFERENCE_BAND, f"{layer_label} 참고 신호 — 측정되지 않았으며 결론이 아닙니다."


def to_layer_diagnostic(
    layer: str,
    payload: Mapping[str, Any],
    *,
    layer_label: str | None = None,
    subject: str | None = None,
) -> dict[str, Any]:
    """Wrap one layer's raw output in the ``layer_diagnostic`` JSON shape.

    ``payload`` is the layer's ``to_json()``. Its ``score`` becomes
    ``raw_score``; legacy ``band``/``band_label``/``verdict`` keys are removed
    at every depth (an embedded audio-track result included).
    """
    label = layer_label or layer
    if "reference_band" in payload:
        reference_band = str(payload.get("reference_band") or UNAVAILABLE_BAND)
        note = str(payload.get("reference_note") or "")
    else:
        reference_band, note = _legacy_reference(payload, label)
    if reference_band not in REFERENCE_BANDS:
        reference_band = UNAVAILABLE_BAND
    body = strip_legacy({k: v for k, v in payload.items() if k not in {"reference_band", "reference_note", "score"}})
    out: dict[str, Any] = {
        "kind": LAYER_DIAGNOSTIC_KIND,
        "title": LAYER_DIAGNOSTIC_TITLE,
        "layer": layer,
        # G1: the Korean name of the layer for text renderings (``layer`` is the id).
        "layer_label": label,
        "measured": False,
        "notice": LAYER_DIAGNOSTIC_NOTICE,
    }
    if subject is not None:
        out["subject"] = subject
    out["reference_band"] = reference_band
    out["reference_note"] = note
    if "score" in payload:
        out["raw_score"] = payload.get("score")
    out["diagnostic"] = body
    return out


def format_layer_diagnostic(diag: Mapping[str, Any]) -> str:
    """Plain-text rendering of a layer diagnostic (``--format table``)."""
    from .result_text import display_name, escape_controls

    lines = [f"[{diag.get('title', LAYER_DIAGNOSTIC_TITLE)}] {diag.get('layer_label') or diag.get('layer', '')}"]
    if diag.get("subject"):
        # R10-1: an echoed file name is shown through display_name.
        lines.append(f"대상: {display_name(diag['subject'])}")
    lines.append(str(diag.get("notice", LAYER_DIAGNOSTIC_NOTICE)))
    if diag.get("raw_score") is not None:
        lines.append(f"참고 원점수(미측정): {diag['raw_score']}/100")
    if diag.get("reference_note"):
        lines.append(f"참고: {diag['reference_note']}")
    raw_body = diag.get("diagnostic")
    body: Mapping[str, Any] = raw_body if isinstance(raw_body, Mapping) else {}
    measurements = [
        f"{key}={escape_controls(value)}"
        for key, value in body.items()
        if key not in {"signals", "limitations"} and not isinstance(value, (Mapping, list, tuple))
    ]
    if measurements:
        # G1: raw field ids are shown as identifiers (key=value), not as a
        # sentence; the meaning is in the Korean notes and signals below.
        lines.append("측정값(필드=값):")
        lines.extend(f"  {entry}" for entry in measurements)
    raw_signals = body.get("signals")
    signals: list[Any] = raw_signals if isinstance(raw_signals, list) else []
    if signals:
        lines.append("참고 신호:")
        for signal in signals:
            if isinstance(signal, Mapping):
                lines.append(f"  - [{signal.get('weight', '')}] {escape_controls(signal.get('title', ''))}: {escape_controls(signal.get('detail', ''))}")
    raw_limitations = body.get("limitations")
    limitations: list[Any] = raw_limitations if isinstance(raw_limitations, list) else []
    if limitations:
        lines.append("한계:")
        lines.extend(f"  - {escape_controls(item)}" for item in limitations)
    return "\n".join(lines)


__all__ = [
    "ANALYSIS_RESULT_KIND",
    "LAYER_DIAGNOSTIC_KIND",
    "LAYER_DIAGNOSTIC_NOTICE",
    "LAYER_DIAGNOSTIC_TITLE",
    "LEGACY_RESULT_KEYS",
    "REFERENCE_BAND",
    "REFERENCE_BANDS",
    "UNAVAILABLE_BAND",
    "format_layer_diagnostic",
    "raw_score_note",
    "reference_band_for",
    "strip_legacy",
    "to_layer_diagnostic",
]

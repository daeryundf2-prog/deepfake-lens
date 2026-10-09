"""Scan result deserialization — JSON payloads back into typed records.

Inverse of the ``to_json`` methods on the result dataclasses; used by the
scan cache, the report loaders, and the review-store readers. Kept apart
from ``core.py`` so the pipeline file stays focused on analysis.
"""

from __future__ import annotations

import dataclasses
import re
from enum import Enum
from pathlib import Path
from typing import Any

from .model_adapter import ExternalModelAnalysis
from .pixel import PIXEL_REFERENCE_CONFIDENCE, PixelAnalysis, PixelExpertResult
from .result_types import (
    RISK_LABELS,
    ClassificationResult,
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceSignal,
    EvidenceStrength,
    Grade,
    RiskBand,
    ScanItem,
    SourceConfidence,
    SourceGuess,
    Verdict,
)


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _num(value: object, default: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _opt_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _opt_str(value: object) -> str | None:
    return str(value) if value is not None and value != "" else None


def _ci(value: object) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        lo, hi = _opt_float(value[0]), _opt_float(value[1])
        if lo is not None and hi is not None:
            return (lo, hi)
    return None


def _signal_from_json(item: dict[str, Any]) -> EvidenceSignal:
    return EvidenceSignal(str(item.get("title", "")), str(item.get("detail", "")), int(_num(item.get("weight"))))


def _evidence_item_from_json(data: dict[str, Any]) -> EvidenceItem:
    raw_score = data.get("raw_score")
    return EvidenceItem(
        title=str(data.get("title", "")),
        detail=str(data.get("detail", "")),
        kind=EvidenceKind(str(data.get("kind"))),
        direction=EvidenceDirection(str(data.get("direction"))),
        strength=EvidenceStrength(str(data.get("strength"))),
        layer=str(data.get("layer", "")),
        probability=_opt_float(data.get("probability")),
        probability_ci=_ci(data.get("probability_ci")),
        calibration_id=_opt_str(data.get("calibration_id")),
        measured_on=_opt_str(data.get("measured_on")),
        raw_score=int(_num(raw_score)) if raw_score is not None else None,
    )


def _coverage_entry_from_json(data: dict[str, Any]) -> CoverageEntry:
    return CoverageEntry(
        check=str(data.get("check", "")),
        status=CoverageStatus(str(data.get("status"))),
        reason=str(data.get("reason", "")),
    )


def _scan_item_from_json(data: dict[str, object]) -> ScanItem:
    result_data = data.get("result")
    result = _classification_result_from_json(result_data) if isinstance(result_data, dict) else None
    return ScanItem(
        str(data.get("path", "")),
        str(data.get("name", "")),
        str(data.get("kind", "unknown")),
        str(data.get("status", "failed")),
        int(_num(data.get("size_bytes"))),
        result,
        str(data.get("error")) if data.get("error") is not None else None,
        str(data.get("duplicate_of")) if data.get("duplicate_of") is not None else None,
        str(data.get("sha256")) if isinstance(data.get("sha256"), str) and data.get("sha256") else None,
        # P7: member identity fields (absent on every other row).
        str(data["container"]) if isinstance(data.get("container"), str) else None,
        str(data["member"]) if isinstance(data.get("member"), str) else None,
        # R12-3: the member's position in its container (absent on other rows).
        _member_index(data.get("member_index")),
    )


def _member_index(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _classification_result_from_json(data: dict[str, object]) -> ClassificationResult:
    source_data = _dict(data.get("source_guess"))
    source_guess = SourceGuess(
        str(source_data.get("label", "출처 단서 없음")),
        SourceConfidence(str(source_data.get("confidence", SourceConfidence.UNKNOWN.value))),
        [str(item) for item in _items(source_data.get("reasons"))],
    )
    pixel_raw = data.get("pixel_analysis")
    pixel_data = pixel_raw if isinstance(pixel_raw, dict) else None
    model_raw = data.get("model_analysis")
    model_data = model_raw if isinstance(model_raw, dict) else None
    score = int(_num(data.get("score")))
    return ClassificationResult(
        score=score,
        band=RiskBand(str(data.get("band", RiskBand.UNKNOWN.value))),
        band_label=str(data.get("band_label", RISK_LABELS[RiskBand.UNKNOWN])),
        verdict=str(data.get("verdict", "")),
        signals=[_signal_from_json(item) for item in _items(data.get("signals")) if isinstance(item, dict)],
        limitations=[str(item) for item in _items(data.get("limitations"))],
        source_guess=source_guess,
        next_checks=[str(item) for item in _items(data.get("next_checks"))],
        pixel_analysis=_pixel_analysis_from_json(pixel_data) if pixel_data else None,
        model_analysis=_model_analysis_from_json(model_data) if model_data else None,
        ai_score=int(_num(data.get("ai_score")) or score),
        source_attribution_label=str(data.get("source_attribution_label", source_guess.label)),
        av_audio=_dict(data.get("av_audio")) or None,
        document_metadata=_dict(data.get("document_metadata")) or None,
        # Contract v2 fields. A v1 record has none of them: it loads as
        # undetermined/evidence-grade with empty evidence and coverage — a
        # stored v1 band is kept verbatim for read-compat but is not a verdict.
        verdict_code=Verdict(str(data.get("verdict_code", Verdict.UNDETERMINED.value))),
        grade=Grade(str(data.get("grade", Grade.EVIDENCE.value))),
        evidence=[_evidence_item_from_json(item) for item in _items(data.get("evidence")) if isinstance(item, dict)],
        coverage=[_coverage_entry_from_json(item) for item in _items(data.get("coverage")) if isinstance(item, dict)],
        probability=_opt_float(data.get("probability")),
        probability_ci=_ci(data.get("probability_ci")),
        score_is_calibrated=bool(data.get("score_is_calibrated", False)),
        reference_signals=[_signal_from_json(item) for item in _items(data.get("reference_signals")) if isinstance(item, dict)],
    )


def _pixel_analysis_from_json(data: dict[str, object]) -> PixelAnalysis:
    experts = [
        PixelExpertResult(
            name=str(item.get("name", "")),
            family=str(item.get("family", "")),
            score=int(_num(item.get("score"))),
            weight=_num(item.get("weight")),
            available=bool(item.get("available", False)),
            detail=str(item.get("detail", "")),
            reference=str(item.get("reference", "")),
            implementation=str(item.get("implementation", "local")),
        )
        for item in _items(data.get("experts"))
        if isinstance(item, dict)
    ]
    available = bool(data.get("available", False))
    # D12: v2 records carry raw_score/reference_confidence; older records
    # carry score/confidence — read both, and never re-surface a legacy
    # low/medium/high confidence for a pixel run (it becomes "참고").
    raw = data.get("raw_score", data.get("score"))
    reference_confidence = data.get("reference_confidence")
    if not reference_confidence:
        reference_confidence = PIXEL_REFERENCE_CONFIDENCE if available else data.get("confidence", "unknown")
    return PixelAnalysis(
        mode=str(data.get("mode", "off")),
        available=available,
        raw_score=int(_num(raw)),
        reference_confidence=str(reference_confidence),
        model=str(data.get("model", "")),
        experts=experts,
        signals=[str(item) for item in _items(data.get("signals"))],
        limitations=[str(item) for item in _items(data.get("limitations"))],
        fusion=str(data.get("fusion", "weighted_mean")),
        evidence_chain=[str(item) for item in _items(data.get("evidence_chain"))],
        implemented_references=[str(item) for item in _items(data.get("implemented_references"))],
        heatmap_path=str(data.get("heatmap_path")) if data.get("heatmap_path") else None,
        analysis_tier=str(data.get("analysis_tier", "ensemble")),
    )


def _model_analysis_from_json(data: dict[str, object]) -> ExternalModelAnalysis:
    return ExternalModelAnalysis(
        available=bool(data.get("available", False)),
        score=int(_num(data.get("score"))),
        confidence=str(data.get("confidence", "unknown")),
        model=str(data.get("model", "")),
        detail=str(data.get("detail", "")),
        limitations=[str(item) for item in _items(data.get("limitations"))],
        models=[dict(item) for item in _items(data.get("models")) if isinstance(item, dict)],
        probability=_opt_float(data.get("probability")),
        probability_ci=_ci(data.get("probability_ci")),
        calibration_id=_opt_str(data.get("calibration_id")),
        measured_on=_opt_str(data.get("measured_on")),
        display_name=str(data.get("display_name") or ""),
    )


# -- --redact-paths (S3) -------------------------------------------------------

# Keys whose value names a model profile file; redacted output keeps only
# the file name (``aide-runtime.json``), wherever the profile lives.
PROFILE_PATH_KEYS = frozenset({"profile", "profile_path", "model_path", "checkpoint_path"})
# Values the report itself must still open (the heatmap image it embeds);
# they point at the tool's output root, not at the installation.
UNREDACTED_KEYS = frozenset({"heatmap_path"})


def install_roots() -> tuple[str, ...]:
    """Directories that reveal where the tool is installed (S3).

    The package's parent (a source checkout or ``site-packages``), the
    effective models dir (``$DEEPFAKE_LENS_MODELS_DIR`` or the packaged
    one) and the interpreter's purelib/platlib — longest first, so the most
    specific prefix is stripped.
    """
    import sysconfig

    from .vendor_weights import default_models_dir

    package = Path(__file__).resolve().parent
    candidates = {str(package.parent), str(package), str(default_models_dir())}
    for key in ("purelib", "platlib"):
        value = sysconfig.get_paths().get(key)
        if value:
            candidates.add(str(Path(value).resolve()))
            candidates.add(str(value))
    return tuple(sorted((c for c in candidates if c and c not in {"/", "."}), key=len, reverse=True))


def _install_path_pattern(roots: tuple[str, ...]) -> re.Pattern[str]:
    # <root>/<dir>/.../ up to the last separator — the file name stays.
    alternation = "|".join(re.escape(root.rstrip("/\\")) for root in roots)
    return re.compile(rf"(?:{alternation})[/\\](?:[^\s'\"/\\]+[/\\])*")


def redact_install_paths(items: list[ScanItem]) -> list[ScanItem]:
    """Rows for a ``--redact-paths`` report: no value names the install path (S3).

    ``model_analysis.models[].profile`` (and any other profile-path key)
    becomes the bare profile file name; any other string carrying one of
    :func:`install_roots` keeps only the file name after it. Everything else
    is unchanged; the input rows are not modified. Non-redacted output keeps
    the full paths.
    """
    pattern = _install_path_pattern(install_roots())
    return [_redact(item, pattern) for item in items]


def _redact(value: Any, pattern: re.Pattern[str], key: str | None = None) -> Any:
    if key in UNREDACTED_KEYS or isinstance(value, Enum):  # str-valued enums stay enums
        return value
    if isinstance(value, str):
        if key in PROFILE_PATH_KEYS and ("/" in value or "\\" in value):
            return Path(value.replace("\\", "/")).name
        return pattern.sub("", value)
    if isinstance(value, Enum) or value is None or isinstance(value, (bool, int, float)):
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        changes = {
            f.name: _redact(getattr(value, f.name), pattern, f.name)
            for f in dataclasses.fields(value) if f.init
        }
        return dataclasses.replace(value, **changes)
    if isinstance(value, dict):
        return {k: _redact(v, pattern, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, pattern, key) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact(v, pattern, key) for v in value)
    return value

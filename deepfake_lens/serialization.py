"""Scan result deserialization — JSON payloads back into typed records.

Inverse of the ``to_json`` methods on the result dataclasses; used by the
scan cache, the report loaders, and the review-store readers. Kept apart
from ``core.py`` so the pipeline file stays focused on analysis.
"""

from __future__ import annotations

from .model_adapter import ExternalModelAnalysis
from .pixel import PixelAnalysis, PixelExpertResult
from .result_types import (
    RISK_LABELS,
    ClassificationResult,
    EvidenceSignal,
    RiskBand,
    ScanItem,
    SourceConfidence,
    SourceGuess,
)


def _scan_item_from_json(data: dict[str, object]) -> ScanItem:
    result_data = data.get("result")
    result = _classification_result_from_json(result_data) if isinstance(result_data, dict) else None
    return ScanItem(
        str(data.get("path", "")),
        str(data.get("name", "")),
        str(data.get("kind", "unknown")),
        str(data.get("status", "failed")),
        int(data.get("size_bytes", 0) or 0),
        result,
        str(data.get("error")) if data.get("error") is not None else None,
        str(data.get("duplicate_of")) if data.get("duplicate_of") is not None else None,
    )


def _classification_result_from_json(data: dict[str, object]) -> ClassificationResult:
    source_data = data.get("source_guess") if isinstance(data.get("source_guess"), dict) else {}
    source_guess = SourceGuess(
        str(source_data.get("label", "출처 단서 없음")),
        SourceConfidence(str(source_data.get("confidence", SourceConfidence.UNKNOWN.value))),
        [str(item) for item in source_data.get("reasons", [])] if isinstance(source_data.get("reasons"), list) else [],
    )
    pixel_data = data.get("pixel_analysis") if isinstance(data.get("pixel_analysis"), dict) else None
    model_data = data.get("model_analysis") if isinstance(data.get("model_analysis"), dict) else None
    score = int(data.get("score", 0) or 0)
    return ClassificationResult(
        score=score,
        band=RiskBand(str(data.get("band", RiskBand.UNKNOWN.value))),
        band_label=str(data.get("band_label", RISK_LABELS[RiskBand.UNKNOWN])),
        verdict=str(data.get("verdict", "")),
        signals=[EvidenceSignal(str(item.get("title", "")), str(item.get("detail", "")), int(item.get("weight", 0) or 0)) for item in data.get("signals", []) if isinstance(item, dict)],
        limitations=[str(item) for item in data.get("limitations", [])] if isinstance(data.get("limitations"), list) else [],
        source_guess=source_guess,
        next_checks=[str(item) for item in data.get("next_checks", [])] if isinstance(data.get("next_checks"), list) else [],
        pixel_analysis=_pixel_analysis_from_json(pixel_data) if pixel_data else None,
        model_analysis=_model_analysis_from_json(model_data) if model_data else None,
        ai_score=int(data.get("ai_score", score) or score),
        source_attribution_label=str(data.get("source_attribution_label", source_guess.label)),
        av_audio=data.get("av_audio") if isinstance(data.get("av_audio"), dict) else None,
        document_metadata=data.get("document_metadata") if isinstance(data.get("document_metadata"), dict) else None,
    )


def _pixel_analysis_from_json(data: dict[str, object]) -> PixelAnalysis:
    experts = [
        PixelExpertResult(
            name=str(item.get("name", "")),
            family=str(item.get("family", "")),
            score=int(item.get("score", 0) or 0),
            weight=float(item.get("weight", 0.0) or 0.0),
            available=bool(item.get("available", False)),
            detail=str(item.get("detail", "")),
            reference=str(item.get("reference", "")),
            implementation=str(item.get("implementation", "local")),
        )
        for item in data.get("experts", [])
        if isinstance(item, dict)
    ]
    return PixelAnalysis(
        mode=str(data.get("mode", "off")),
        available=bool(data.get("available", False)),
        score=int(data.get("score", 0) or 0),
        confidence=str(data.get("confidence", "unknown")),
        model=str(data.get("model", "")),
        experts=experts,
        signals=[str(item) for item in data.get("signals", [])] if isinstance(data.get("signals"), list) else [],
        limitations=[str(item) for item in data.get("limitations", [])] if isinstance(data.get("limitations"), list) else [],
        fusion=str(data.get("fusion", "weighted_mean")),
        evidence_chain=[str(item) for item in data.get("evidence_chain", [])] if isinstance(data.get("evidence_chain"), list) else [],
        implemented_references=[str(item) for item in data.get("implemented_references", [])] if isinstance(data.get("implemented_references"), list) else [],
        heatmap_path=str(data.get("heatmap_path")) if data.get("heatmap_path") else None,
        analysis_tier=str(data.get("analysis_tier", "ensemble")),
    )


def _model_analysis_from_json(data: dict[str, object]) -> ExternalModelAnalysis:
    return ExternalModelAnalysis(
        available=bool(data.get("available", False)),
        score=int(data.get("score", 0) or 0),
        confidence=str(data.get("confidence", "unknown")),
        model=str(data.get("model", "")),
        detail=str(data.get("detail", "")),
        limitations=[str(item) for item in data.get("limitations", [])] if isinstance(data.get("limitations"), list) else [],
        models=[dict(item) for item in data.get("models", []) if isinstance(item, dict)] if isinstance(data.get("models"), list) else [],
    )

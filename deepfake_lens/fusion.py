from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .calibration import calibrate_threshold
from .core import ClassificationResult, EvidenceSignal, RISK_LABELS, RiskBand, ScanItem, SourceConfidence, analyze_file
from .datasets import discover_dataset, is_positive_label


@dataclass(frozen=True)
class FusionProfile:
    version: str
    weights: dict[str, float]
    threshold: int
    unknown_below: int = 8

    def to_json(self) -> dict[str, object]:
        return asdict(self)


DEFAULT_FUSION_PROFILE = FusionProfile(
    version="fusion-profile-v1",
    weights={"metadata": 0.35, "pixel": 0.25, "external_model": 0.3, "source": 0.1},
    threshold=67,
)


def load_fusion_profile(path: Path | str | None) -> FusionProfile | None:
    if path is None:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    weights = payload.get("weights") if isinstance(payload.get("weights"), dict) else DEFAULT_FUSION_PROFILE.weights
    return FusionProfile(
        version=str(payload.get("version", "fusion-profile-v1")),
        weights={str(key): float(value) for key, value in weights.items() if isinstance(value, (int, float))},
        threshold=int(payload.get("threshold", DEFAULT_FUSION_PROFILE.threshold) or DEFAULT_FUSION_PROFILE.threshold),
        unknown_below=int(payload.get("unknown_below", DEFAULT_FUSION_PROFILE.unknown_below) or DEFAULT_FUSION_PROFILE.unknown_below),
    )


def write_fusion_profile(path: Path | str, profile: FusionProfile) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def calibrate_fusion_profile(
    root: Path | str,
    *,
    pixel_mode: str = "deep",
    model_path: Path | None = None,
    target_false_positive_rate: float = 0.05,
    max_files: int | None = None,
) -> dict[str, object]:
    root_path = Path(root)
    summary, records = discover_dataset(root_path, max_files=max_files)
    rows = []
    scores: list[tuple[int, bool]] = []
    for record in records:
        if record.label == "unknown":
            continue
        item = analyze_file(Path(record.path), root=root_path, pixel_mode=pixel_mode, model_path=model_path)
        if not item.result:
            continue
        components = component_scores(item.result)
        score = fused_score(components, DEFAULT_FUSION_PROFILE)
        positive = is_positive_label(record.label)
        scores.append((score, positive))
        rows.append({"path": item.path, "label": record.label, "score": score, "components": components})
    calibration = calibrate_threshold(scores, target_false_positive_rate=target_false_positive_rate)
    profile = replace(DEFAULT_FUSION_PROFILE, threshold=calibration.threshold)
    return {
        "version": "fusion-calibration-v1",
        "dataset": summary.to_json(),
        "profile": profile.to_json(),
        "metrics": calibration.metrics,
        "rows": rows,
    }


def component_scores(result: ClassificationResult) -> dict[str, int]:
    metadata_score = sum(signal.weight for signal in result.signals if not _is_pixel_signal(signal) and not _is_model_signal(signal))
    pixel_score = result.pixel_analysis.score if result.pixel_analysis and result.pixel_analysis.available else 0
    model_score = result.model_analysis.score if result.model_analysis and result.model_analysis.available else 0
    source_score = {
        SourceConfidence.HIGH: 100,
        SourceConfidence.MEDIUM: 65,
        SourceConfidence.LOW: 35,
        SourceConfidence.UNKNOWN: 0,
    }[result.source_guess.confidence]
    return {
        "metadata": max(0, min(100, metadata_score)),
        "pixel": max(0, min(100, pixel_score)),
        "external_model": max(0, min(100, model_score)),
        "source": source_score,
    }


def fused_score(components: dict[str, int], profile: FusionProfile) -> int:
    total_weight = sum(max(0.0, value) for value in profile.weights.values())
    if total_weight <= 0:
        return 0
    score = sum(float(components.get(name, 0)) * max(0.0, weight) for name, weight in profile.weights.items()) / total_weight
    return max(0, min(100, int(round(score))))


def apply_fusion_to_result(result: ClassificationResult, profile: FusionProfile) -> ClassificationResult:
    components = component_scores(result)
    score = fused_score(components, profile)
    if score < profile.unknown_below and result.source_guess.confidence == SourceConfidence.UNKNOWN:
        band = RiskBand.UNKNOWN
    elif score >= profile.threshold:
        band = RiskBand.HIGH
    elif score >= max(35, profile.threshold // 2):
        band = RiskBand.MEDIUM
    else:
        band = RiskBand.LOW
    signal = EvidenceSignal("융합 점수", f"metadata={components['metadata']}, pixel={components['pixel']}, external={components['external_model']}, source={components['source']}", score)
    return replace(
        result,
        score=score,
        ai_score=score,
        band=band,
        band_label=RISK_LABELS[band],
        verdict=_verdict(band),
        signals=[signal, *result.signals],
        limitations=[*result.limitations, "융합 점수는 로컬 보정 프로필 기반 우선순위 점수입니다."],
    )


def apply_fusion_to_items(items: list[ScanItem], profile: FusionProfile | None) -> list[ScanItem]:
    if profile is None:
        return items
    fused = []
    for item in items:
        if item.result:
            fused.append(replace(item, result=apply_fusion_to_result(item.result, profile)))
        else:
            fused.append(item)
    return fused


def _is_pixel_signal(signal: EvidenceSignal) -> bool:
    return signal.title.startswith("픽셀")


def _is_model_signal(signal: EvidenceSignal) -> bool:
    return signal.title.startswith("외부 모델")


def _verdict(band: RiskBand) -> str:
    return {
        RiskBand.UNKNOWN: "융합 점수에서 판단할 단서가 부족합니다.",
        RiskBand.HIGH: "융합 점수에서 의심 신호가 강합니다.",
        RiskBand.MEDIUM: "융합 점수에서 추가 확인이 필요합니다.",
        RiskBand.LOW: "융합 점수에서 뚜렷한 의심 신호는 적습니다.",
    }[band]

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .calibration import calibrate_threshold
from .core import ClassificationResult, EvidenceSignal, ScanItem, SourceConfidence, analyze_file
from .datasets import discover_dataset, is_positive_label
from .evaluation_metrics import ci_summary


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
    # G26: the fused number is built from raw member scores (component_scores
    # reads model_analysis/pixel_analysis raw values, never result.score) and
    # the fitted cutoff is in-sample; report CIs and say both.
    ci = ci_summary(
        [float(score) for score, _ in scores],
        [1 if positive else 0 for _, positive in scores],
        threshold=float(calibration.threshold),
    )
    return {
        "version": "fusion-calibration-v1",
        "dataset": summary.to_json(),
        "profile": profile.to_json(),
        "metrics": calibration.metrics,
        "metrics_ci": ci,
        "score_basis": "raw, uncalibrated",
        "in_sample": True,
        "note": "융합 임계값은 같은 표본에서 맞추고 같은 표본에서 평가한 in-sample 값(참고)이며 원점수(raw, uncalibrated) 기반입니다.",
        "rows": rows,
    }


def component_scores(result: ClassificationResult) -> dict[str, int]:
    # "생성 도구 메타데이터" is derived from the same source guess that the
    # "source" component scores, so it must not also count as metadata weight;
    # the fusion signal itself must be excluded so re-applying fusion is
    # idempotent.
    metadata_score = sum(
        signal.weight
        for signal in result.signals
        if not _is_pixel_signal(signal)
        and not _is_model_signal(signal)
        and not _is_source_signal(signal)
        and not _is_fusion_signal(signal)
    )
    pixel_score = result.pixel_analysis.raw_score if result.pixel_analysis and result.pixel_analysis.available else 0
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


def component_scores_from_json(result: dict[str, object]) -> dict[str, int]:
    """Rebuild fusion component scores from a serialized ClassificationResult
    dict (e.g. a prior ``scan --json-out`` item's ``result``), using the same
    signal-title exclusions as :func:`component_scores`."""
    metadata_score = 0.0
    signals = result.get("signals", [])
    for signal in signals if isinstance(signals, list) else []:
        if not isinstance(signal, dict):
            continue
        title = str(signal.get("title", ""))
        weight = signal.get("weight", 0)
        if not isinstance(weight, (int, float)):
            continue
        if title.startswith("픽셀") or title.startswith("외부 모델") or title in {"생성 도구 메타데이터", "융합 점수"}:
            continue
        metadata_score += weight
    pixel = result.get("pixel_analysis") if isinstance(result.get("pixel_analysis"), dict) else {}
    model = result.get("model_analysis") if isinstance(result.get("model_analysis"), dict) else {}
    source_guess = result.get("source_guess") if isinstance(result.get("source_guess"), dict) else {}
    pixel_score = float(pixel.get("raw_score", pixel.get("score", 0)) or 0) if pixel.get("available") else 0.0
    model_score = float(model.get("score", 0) or 0) if model.get("available") else 0.0
    source_score = {"high": 100, "medium": 65, "low": 35}.get(str(source_guess.get("confidence", "")), 0)
    return {
        "metadata": max(0, min(100, int(metadata_score))),
        "pixel": max(0, min(100, int(pixel_score))),
        "external_model": max(0, min(100, int(model_score))),
        "source": source_score,
    }


def fused_score(components: dict[str, int], profile: FusionProfile) -> int:
    total_weight = sum(max(0.0, value) for value in profile.weights.values())
    if total_weight <= 0:
        return 0
    score = sum(float(components.get(name, 0)) * max(0.0, weight) for name, weight in profile.weights.items()) / total_weight
    return max(0, min(100, int(round(score))))


FUSION_SIGNAL_TITLE = "융합 점수"
FUSION_LIMITATION = "융합 점수는 손으로 정한 가중치의 참고 점수이며 결론에 참여하지 않습니다."


def apply_fusion_to_result(result: ClassificationResult, profile: FusionProfile) -> ClassificationResult:
    """Attach the fused component score as a *reference* signal (G5).

    The weights are hand-set constants, not a measured mapping, so the
    fused number must not move the verdict, band, or score. Re-applying
    the same profile replaces the previous fusion signal (idempotent).
    """
    base_reference = [signal for signal in result.reference_signals if not _is_fusion_signal(signal)]
    base_signals = [signal for signal in result.signals if not _is_fusion_signal(signal)]
    base_limitations = [limitation for limitation in result.limitations if limitation != FUSION_LIMITATION]
    components = component_scores(replace(result, signals=base_signals))
    score = fused_score(components, profile)
    signal = EvidenceSignal(
        FUSION_SIGNAL_TITLE,
        f"metadata={components['metadata']}, pixel={components['pixel']}, external={components['external_model']}, source={components['source']} (참고, 미측정 가중치)",
        score,
    )
    return replace(
        result,
        reference_signals=[signal, *base_reference],
        signals=base_signals,
        limitations=[*base_limitations, FUSION_LIMITATION],
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


def _is_source_signal(signal: EvidenceSignal) -> bool:
    return signal.title == "생성 도구 메타데이터"


def _is_fusion_signal(signal: EvidenceSignal) -> bool:
    return signal.title == FUSION_SIGNAL_TITLE

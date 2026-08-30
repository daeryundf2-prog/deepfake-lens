from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_THRESHOLD = 67


@dataclass(frozen=True)
class CalibrationProfile:
    version: str
    threshold: int
    target_false_positive_rate: float
    metrics: dict[str, float | int]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def load_calibration(path: Path | str | None) -> CalibrationProfile | None:
    if path is None:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    threshold = payload.get("threshold")
    if not isinstance(threshold, (int, float)):
        return None
    return CalibrationProfile(
        version=str(payload.get("version", "calibration-v1")),
        threshold=max(0, min(100, int(round(float(threshold))))),
        target_false_positive_rate=float(payload.get("target_false_positive_rate", 0.05) or 0.05),
        metrics=payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {},
    )


def write_calibration(path: Path | str, profile: CalibrationProfile) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(profile.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def calibrate_threshold(scores: list[tuple[int, bool]], *, target_false_positive_rate: float = 0.05) -> CalibrationProfile:
    if not scores:
        return CalibrationProfile("calibration-v1", DEFAULT_THRESHOLD, target_false_positive_rate, {"samples": 0})
    best_threshold = DEFAULT_THRESHOLD
    best_recall = -1.0
    best_metrics: dict[str, float | int] = {}
    for threshold in range(0, 101):
        metrics = binary_metrics(scores, threshold)
        fpr = float(metrics["false_positive_rate"])
        recall = float(metrics["recall"])
        if fpr <= target_false_positive_rate and recall > best_recall:
            best_threshold = threshold
            best_recall = recall
            best_metrics = metrics
    if not best_metrics:
        # No threshold satisfies the target FPR. Save the most conservative
        # in-domain threshold (100) and record the shortfall explicitly; a
        # former fallback of 101 was out of the score domain and silently
        # disabled detection while looking like a normal profile.
        best_threshold = 100
        best_metrics = binary_metrics(scores, best_threshold)
        best_metrics["target_fpr_met"] = 0
    else:
        best_metrics["target_fpr_met"] = 1
    return CalibrationProfile("calibration-v1", best_threshold, target_false_positive_rate, best_metrics)


def binary_metrics(scores: list[tuple[int, bool]], threshold: int) -> dict[str, float | int]:
    tp = fp = tn = fn = 0
    for score, positive in scores:
        predicted = score >= threshold
        if predicted and positive:
            tp += 1
        elif predicted and not positive:
            fp += 1
        elif not predicted and positive:
            fn += 1
        else:
            tn += 1
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    accuracy = (tp + tn) / max(1, len(scores))
    false_positive_rate = fp / max(1, fp + tn)
    return {
        "threshold": threshold,
        "samples": len(scores),
        "true_positive": tp,
        "false_positive": fp,
        "true_negative": tn,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "accuracy": accuracy,
        "false_positive_rate": false_positive_rate,
    }


def eer(scores: list[tuple[int, bool]]) -> float | None:
    """Equal error rate: the operating point where FPR and miss rate cross.

    Swept over integer thresholds; None when either class is absent.
    """
    if not scores:
        return None
    if not any(positive for _, positive in scores):
        return None
    if all(positive for _, positive in scores):
        return None
    best_diff = None
    best_value = None
    for threshold in range(0, 101):
        metrics = binary_metrics(scores, threshold)
        fpr = float(metrics["false_positive_rate"])
        fnr = 1.0 - float(metrics["recall"])
        diff = abs(fpr - fnr)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_value = (fpr + fnr) / 2
    return best_value


def auroc(scores: list[tuple[int, bool]]) -> float | None:
    positives = [score for score, positive in scores if positive]
    negatives = [score for score, positive in scores if not positive]
    if not positives or not negatives:
        return None
    wins = 0.0
    total = len(positives) * len(negatives)
    for positive_score in positives:
        for negative_score in negatives:
            if positive_score > negative_score:
                wins += 1.0
            elif positive_score == negative_score:
                wins += 0.5
    return wins / total

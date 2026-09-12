from __future__ import annotations

import json
from bisect import bisect_left
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from .datasets import is_negative_label, is_positive_label


DEFAULT_THRESHOLD = 67
CALIBRATOR_VERSION = "score-calibrator-v1"
MIN_CALIBRATION_SAMPLES = 20
MIN_CLASS_SAMPLES = 5


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


@dataclass(frozen=True)
class ScoreCalibrator:
    """Monotone mapping from a raw 0-100 prioritization score to a
    dataset-dependent calibrated confidence.

    Fit by pool-adjacent-violators isotonic regression on the empirical
    positive rate of a labeled calibration set. The calibrated value is a
    screening confidence tied to that dataset — it is not a universal
    probability of "AI-generated" and not a truth label. When the fit data
    was insufficient the mapping is empty and ``transform`` reports
    ``(None, "uncalibrated")`` rather than a fabricated value.
    """

    version: str
    method: str  # "isotonic-pava" or "insufficient-data"
    mapping: tuple[tuple[float, float], ...]  # (score knot, calibrated confidence), ascending
    fpr_table: tuple[tuple[float, float], ...]  # (score threshold, empirical FPR at >= threshold)
    samples: int
    positives: int
    negatives: int
    dataset_fingerprint: str = ""
    notes: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return bool(self.mapping)

    def to_json(self) -> dict[str, object]:
        return {
            "version": self.version,
            "method": self.method,
            "mapping": [[knot, value] for knot, value in self.mapping],
            "fpr_table": [[threshold, fpr] for threshold, fpr in self.fpr_table],
            "samples": self.samples,
            "positives": self.positives,
            "negatives": self.negatives,
            "dataset_fingerprint": self.dataset_fingerprint,
            "notes": list(self.notes),
        }

    def transform(self, score: float) -> tuple[float | None, str]:
        """Map a raw score to ``(calibrated_confidence, fpr_band)``.

        ``fpr_band`` is the empirical false-positive rate band observed on
        the calibration negatives at this score level: "low" (<=5%),
        "moderate" (<=20%), "high" (>20%), or "unknown"/"uncalibrated" when
        no fit exists.
        """
        if not self.mapping:
            return None, "uncalibrated"
        x = min(100.0, max(0.0, float(score)))
        return round(_interpolate(self.mapping, x), 4), self._fpr_band(x)

    def _fpr_band(self, score: float) -> str:
        if not self.fpr_table:
            return "unknown"
        fpr = 0.0
        for threshold, value in self.fpr_table:
            if threshold >= score:
                fpr = value
                break
        if fpr <= 0.05:
            return "low"
        if fpr <= 0.2:
            return "moderate"
        return "high"


def calibrate_scores(
    labeled_results: Iterable[object],
    *,
    dataset_fingerprint: str = "",
    min_samples: int = MIN_CALIBRATION_SAMPLES,
    min_per_class: int = MIN_CLASS_SAMPLES,
) -> ScoreCalibrator:
    """Fit an isotonic score->confidence mapping from labeled results.

    Accepts ``(score, positive)`` pairs or dicts with ``score`` plus either
    ``positive`` (bool) or ``label``/``expected_label`` (dataset label
    string). Unparseable rows are skipped. Returns an "insufficient-data"
    calibrator — never a fabricated mapping — when the usable data is below
    ``min_samples`` or either class has fewer than ``min_per_class`` rows.
    """
    pairs = [pair for pair in (_labeled_pair(item) for item in labeled_results) if pair is not None]
    positives = sum(1 for _, positive in pairs if positive)
    negatives = len(pairs) - positives
    notes = [
        "calibrated values are dataset-dependent screening confidences, not truth probabilities",
        "scores remain prioritization evidence, not final truth labels",
    ]
    if len(pairs) < min_samples or positives < min_per_class or negatives < min_per_class:
        notes.append(
            f"insufficient labeled data ({len(pairs)} usable samples, {positives} positive, "
            f"{negatives} negative; need >= {min_samples} samples and >= {min_per_class} per class)"
        )
        return ScoreCalibrator(
            CALIBRATOR_VERSION, "insufficient-data", (), (), len(pairs), positives, negatives, dataset_fingerprint, tuple(notes)
        )
    mapping = _isotonic_knots(pairs)
    negative_scores = sorted(score for score, positive in pairs if not positive)
    fpr_table = tuple(
        (score, (negatives - bisect_left(negative_scores, score)) / negatives)
        for score in sorted({score for score, _ in pairs})
    )
    return ScoreCalibrator(
        CALIBRATOR_VERSION, "isotonic-pava", mapping, fpr_table, len(pairs), positives, negatives, dataset_fingerprint, tuple(notes)
    )


def load_score_calibrator(path: Path | str | None) -> ScoreCalibrator | None:
    if path is None:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != CALIBRATOR_VERSION:
        return None
    mapping = _float_pairs(payload.get("mapping"))
    fpr_table = _float_pairs(payload.get("fpr_table"))
    return ScoreCalibrator(
        version=CALIBRATOR_VERSION,
        method=str(payload.get("method", "insufficient-data")),
        mapping=mapping,
        fpr_table=fpr_table,
        samples=int(payload.get("samples", 0) or 0),
        positives=int(payload.get("positives", 0) or 0),
        negatives=int(payload.get("negatives", 0) or 0),
        dataset_fingerprint=str(payload.get("dataset_fingerprint", "")),
        notes=tuple(str(note) for note in payload.get("notes", []) if isinstance(note, str)),
    )


def write_score_calibrator(path: Path | str, calibrator: ScoreCalibrator) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(calibrator.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _labeled_pair(item: object) -> tuple[float, bool] | None:
    if isinstance(item, dict):
        score = item.get("score")
        if not isinstance(score, (int, float)):
            return None
        if "positive" in item:
            return float(score), bool(item["positive"])
        label = str(item.get("label", item.get("expected_label", "")))
        if is_positive_label(label):
            return float(score), True
        if is_negative_label(label):
            return float(score), False
        return None
    if isinstance(item, (tuple, list)) and len(item) >= 2:
        try:
            return float(item[0]), bool(item[1])
        except (TypeError, ValueError):
            return None
    return None


def _isotonic_knots(pairs: list[tuple[float, bool]]) -> tuple[tuple[float, float], ...]:
    """Pool-adjacent-violators isotonic fit on the empirical positive rate.

    Scores are grouped first so tied scores share one fitted value; blocks
    are merged while an earlier block's mean exceeds a later block's. Each
    returned knot is ``(block_max_score, fitted_positive_rate)`` and the
    fitted values are non-decreasing.
    """
    buckets: dict[float, list[int]] = {}
    for score, positive in pairs:
        buckets.setdefault(float(score), []).append(1 if positive else 0)
    # blocks: [positive_sum, count, min_score, max_score]
    blocks: list[list[float]] = []
    for score in sorted(buckets):
        labels = buckets[score]
        blocks.append([float(sum(labels)), float(len(labels)), score, score])
        while len(blocks) >= 2 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            merged = blocks.pop()
            blocks[-1][0] += merged[0]
            blocks[-1][1] += merged[1]
            blocks[-1][3] = merged[3]
    return tuple((block[3], block[0] / block[1]) for block in blocks)


def _interpolate(mapping: tuple[tuple[float, float], ...], score: float) -> float:
    """Piecewise-linear interpolation over isotonic knots, clamped at the ends."""
    if score <= mapping[0][0]:
        return mapping[0][1]
    previous_knot, previous_value = mapping[0]
    for knot, value in mapping[1:]:
        if score <= knot:
            ratio = (score - previous_knot) / (knot - previous_knot)
            return previous_value + ratio * (value - previous_value)
        previous_knot, previous_value = knot, value
    return mapping[-1][1]


def _float_pairs(raw: object) -> tuple[tuple[float, float], ...]:
    pairs = []
    if isinstance(raw, list):
        for row in raw:
            if isinstance(row, (list, tuple)) and len(row) >= 2 and all(isinstance(v, (int, float)) for v in row[:2]):
                pairs.append((float(row[0]), float(row[1])))
    return tuple(pairs)

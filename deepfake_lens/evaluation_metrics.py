from __future__ import annotations

import math
from itertools import groupby

from .calibration import auroc as _calibration_auroc


def _validate(pairs: list[tuple[float, int]]) -> None:
    if any(label not in (0, 1) for _, label in pairs):
        raise ValueError("labels must be binary (0 or 1)")
    if any(not math.isfinite(score) for score, _ in pairs):
        raise ValueError("scores must be finite")


def undefined_reason(pairs: list[tuple[float, int]]) -> str | None:
    _validate(pairs)
    if not pairs:
        return "empty_input"
    if not any(label == 1 for _, label in pairs):
        return "no_positive_samples"
    if not any(label == 0 for _, label in pairs):
        return "no_negative_samples"
    return None


def auroc(pairs: list[tuple[float, int]]) -> float | None:
    _validate(pairs)
    return _calibration_auroc([(score, label == 1) for score, label in pairs])


def sweep(pairs: list[tuple[float, int]]):
    _validate(pairs)
    if not pairs:
        return
    ranked = sorted(pairs, key=lambda pair: pair[0], reverse=True)
    positives = sum(label for _, label in pairs)
    negatives = len(pairs) - positives
    tp = fp = 0
    yield math.nextafter(ranked[0][0], math.inf), 0.0 if negatives else None, 1.0 if positives else None
    for threshold, group in groupby(ranked, key=lambda pair: pair[0]):
        for _, label in group:
            tp += label
            fp += 1 - label
        yield threshold, fp / negatives if negatives else None, (positives - tp) / positives if positives else None


def eer(pairs: list[tuple[float, int]]) -> float | None:
    if undefined_reason(pairs) is not None:
        return None
    previous_fpr, previous_fnr = 0.0, 1.0
    for _, fpr, fnr in sweep(pairs):
        if fpr == fnr:
            return fpr
        if fpr > fnr:
            previous_diff = previous_fpr - previous_fnr
            fraction = -previous_diff / (fpr - fnr - previous_diff)
            return previous_fpr + fraction * (fpr - previous_fpr)
        previous_fpr, previous_fnr = fpr, fnr
    raise ValueError("ROC curve did not cross the equal-error line")


def threshold_at_fpr(pairs: list[tuple[float, int]], target_fpr: float) -> float | None:
    if not math.isfinite(target_fpr) or not 0.0 <= target_fpr <= 1.0:
        raise ValueError("target FPR must be finite and between 0 and 1")
    _validate(pairs)
    if not any(label == 0 for _, label in pairs):
        return None
    best = None
    for threshold, fpr, _ in sweep(pairs):
        if fpr > target_fpr:
            break
        best = threshold
    if best is None or not math.isfinite(best):
        raise ValueError("no finite threshold can satisfy the target FPR")
    return best

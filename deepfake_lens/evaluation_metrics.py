from __future__ import annotations

import bisect
import math
import random
from itertools import groupby
from typing import Any

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


# --- Bootstrap confidence intervals (phase 0, G26/WP-I) -------------------
#
# Every AUROC / recall / FPR this project reports must carry a confidence
# interval and its class counts; a point estimate on 40 samples per class is
# not evidence. The bootstrap is *stratified* (positives and negatives are
# resampled separately, so every replicate keeps n_pos/n_neg fixed and no
# replicate is degenerate) and uses the percentile method.
#
# Resampling indices always come from ``random.Random(seed)`` so the numpy
# path and the pure-Python fallback produce identical intervals for the same
# seed; numpy only vectorises the per-replicate metric.

BOOTSTRAP_METRICS = frozenset({"auroc", "recall_at_fpr", "fpr_at_threshold"})
# n_boot=2000 is the usual floor for stable 95% percentile intervals
# (Efron & Tibshirani 1993, ch. 13); alpha=0.05 → 95% CI.
DEFAULT_N_BOOT = 2000
DEFAULT_ALPHA = 0.05
# Operating point for recall_at_fpr: the measurement gate requires recall at
# FPR 1% for text members (phase-0 spec WP-I, ``recall_at_fpr_0_01``).
DEFAULT_TARGET_FPR = 0.01
# Fixed decision threshold for fpr_at_threshold on the 0-100 raw scale —
# the ``>= 50`` operating point experiments/eval_all.py has always reported.
DEFAULT_SCORE_THRESHOLD = 50.0
CI_METHOD = "stratified-percentile-bootstrap"

Sequence = list[float] | tuple[float, ...]
LabelSequence = list[int] | tuple[int, ...]


def _auroc_split(pos: list[float], neg: list[float]) -> float:
    """Mann-Whitney AUROC (ties count 0.5) via sorted negatives, O(n log n)."""
    ordered = sorted(neg)
    wins = 0.0
    for score in pos:
        below = bisect.bisect_left(ordered, score)
        at_or_below = bisect.bisect_right(ordered, score)
        wins += below + 0.5 * (at_or_below - below)
    return wins / (len(pos) * len(neg))


def _allowed_false_positives(target_fpr: float, n_neg: int) -> int:
    # 1e-12 guards float error in e.g. 0.01 * 300 = 2.9999999999999996.
    return int(math.floor(target_fpr * n_neg + 1e-12))


def _recall_at_fpr_split(pos: list[float], neg: list[float], target_fpr: float) -> float:
    """Recall at the strictest feasible cutoff with FPR <= ``target_fpr``.

    A sample is called positive when its score is strictly above the cutoff
    ``c``; ``c`` is the (k+1)-th largest negative score with
    ``k = floor(target_fpr * n_neg)`` allowed false positives, so at most k
    negatives exceed it. When k >= n_neg every sample may be called positive.
    """
    allowed = _allowed_false_positives(target_fpr, len(neg))
    if allowed >= len(neg):
        return 1.0
    cutoff = sorted(neg, reverse=True)[allowed]
    return sum(1 for score in pos if score > cutoff) / len(pos)


def _fpr_at_threshold_split(neg: list[float], threshold: float) -> float:
    """Share of negatives called positive at ``score >= threshold``."""
    return sum(1 for score in neg if score >= threshold) / len(neg)


def _check_metric(metric: str) -> None:
    if metric not in BOOTSTRAP_METRICS:
        raise ValueError(f"unknown metric {metric!r}; expected one of {sorted(BOOTSTRAP_METRICS)}")


def _metric_split(metric: str, pos: list[float], neg: list[float], *, target_fpr: float, threshold: float) -> float:
    if metric == "auroc":
        return _auroc_split(pos, neg)
    if metric == "recall_at_fpr":
        return _recall_at_fpr_split(pos, neg, target_fpr)
    return _fpr_at_threshold_split(neg, threshold)


def _split_by_label(scores: Sequence, labels: LabelSequence) -> tuple[list[float], list[float]]:
    if len(scores) != len(labels):
        raise ValueError("scores and labels must have the same length")
    pairs = [(float(score), int(label)) for score, label in zip(scores, labels)]
    _validate(pairs)
    pos = [score for score, label in pairs if label == 1]
    neg = [score for score, label in pairs if label == 0]
    return pos, neg


def _defined(metric: str, pos: list[float], neg: list[float]) -> bool:
    return bool(neg) and (metric == "fpr_at_threshold" or bool(pos))


def metric_value(
    scores: Sequence,
    labels: LabelSequence,
    metric: str,
    *,
    target_fpr: float = DEFAULT_TARGET_FPR,
    threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> float | None:
    """Point estimate of ``metric``; None when a needed class is empty."""
    _check_metric(metric)
    pos, neg = _split_by_label(scores, labels)
    if not _defined(metric, pos, neg):
        return None
    return _metric_split(metric, pos, neg, target_fpr=target_fpr, threshold=threshold)


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile (numpy's default ``linear`` method)."""
    position = (len(sorted_values) - 1) * q
    low = int(math.floor(position))
    high = min(low + 1, len(sorted_values) - 1)
    fraction = position - low
    return sorted_values[low] + fraction * (sorted_values[high] - sorted_values[low])


def _bootstrap_indices(rng: random.Random, size: int, n_boot: int) -> list[list[int]]:
    population = range(size)
    return [rng.choices(population, k=size) for _ in range(n_boot)]


def _replicates_numpy(
    metric: str,
    pos: list[float],
    neg: list[float],
    pos_idx: list[list[int]],
    neg_idx: list[list[int]],
    *,
    target_fpr: float,
    threshold: float,
) -> list[float]:
    import numpy as np

    pos_arr: Any = np.asarray(pos, dtype=np.float64)
    neg_arr: Any = np.asarray(neg, dtype=np.float64)
    allowed = _allowed_false_positives(target_fpr, len(neg))
    out: list[float] = []
    for b in range(len(neg_idx)):
        negatives = neg_arr[neg_idx[b]]
        if metric == "fpr_at_threshold":
            out.append(float(np.count_nonzero(negatives >= threshold)) / len(neg))
            continue
        positives = pos_arr[pos_idx[b]]
        if metric == "auroc":
            ordered = np.sort(negatives)
            below = np.searchsorted(ordered, positives, side="left")
            at_or_below = np.searchsorted(ordered, positives, side="right")
            wins = float(below.sum()) + 0.5 * float((at_or_below - below).sum())
            out.append(wins / (len(pos) * len(neg)))
        elif allowed >= len(neg):
            out.append(1.0)
        else:
            cutoff = float(np.sort(negatives)[::-1][allowed])
            out.append(float(np.count_nonzero(positives > cutoff)) / len(pos))
    return out


def _replicates_python(
    metric: str,
    pos: list[float],
    neg: list[float],
    pos_idx: list[list[int]],
    neg_idx: list[list[int]],
    *,
    target_fpr: float,
    threshold: float,
) -> list[float]:
    return [
        _metric_split(
            metric,
            [pos[i] for i in pos_idx[b]],
            [neg[i] for i in neg_idx[b]],
            target_fpr=target_fpr,
            threshold=threshold,
        )
        for b in range(len(neg_idx))
    ]


def bootstrap_ci(
    scores: Sequence,
    labels: LabelSequence,
    metric: str,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = 0,
    alpha: float = DEFAULT_ALPHA,
    *,
    target_fpr: float = DEFAULT_TARGET_FPR,
    threshold: float = DEFAULT_SCORE_THRESHOLD,
    use_numpy: bool = True,
) -> tuple[float, float] | None:
    """Stratified percentile-bootstrap ``(lo, hi)`` interval for ``metric``.

    ``metric`` is one of ``auroc``, ``recall_at_fpr`` (recall at the cutoff
    meeting ``target_fpr``, re-derived per replicate so the cutoff's own
    uncertainty is included) or ``fpr_at_threshold`` (negatives with
    ``score >= threshold``). Returns None when the metric is undefined (a
    needed class is empty). numpy is used when importable and
    ``use_numpy`` is true; results are identical either way for one seed.
    """
    _check_metric(metric)
    if n_boot < 1:
        raise ValueError("n_boot must be >= 1")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must be between 0 and 1")
    pos, neg = _split_by_label(scores, labels)
    if not _defined(metric, pos, neg):
        return None
    rng = random.Random(seed)
    # Negatives first, then positives: fpr_at_threshold (no positives
    # needed) consumes the same stream prefix as the other metrics.
    neg_idx = _bootstrap_indices(rng, len(neg), n_boot)
    pos_idx = _bootstrap_indices(rng, len(pos), n_boot) if pos else [[] for _ in range(n_boot)]
    replicates: list[float] | None = None
    if use_numpy:
        try:
            replicates = _replicates_numpy(metric, pos, neg, pos_idx, neg_idx, target_fpr=target_fpr, threshold=threshold)
        except ImportError:
            replicates = None
    if replicates is None:
        replicates = _replicates_python(metric, pos, neg, pos_idx, neg_idx, target_fpr=target_fpr, threshold=threshold)
    replicates.sort()
    return (_percentile(replicates, alpha / 2.0), _percentile(replicates, 1.0 - alpha / 2.0))


def metric_with_ci(
    scores: Sequence,
    labels: LabelSequence,
    metric: str,
    *,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = 0,
    alpha: float = DEFAULT_ALPHA,
    target_fpr: float = DEFAULT_TARGET_FPR,
    threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> dict[str, object]:
    """Point estimate + bootstrap CI + class counts, ready to serialise."""
    pos, neg = _split_by_label(scores, labels)
    value = metric_value(scores, labels, metric, target_fpr=target_fpr, threshold=threshold)
    ci = bootstrap_ci(scores, labels, metric, n_boot, seed, alpha, target_fpr=target_fpr, threshold=threshold)
    return {
        "value": value,
        "ci": [ci[0], ci[1]] if ci is not None else None,
        "n_pos": len(pos),
        "n_neg": len(neg),
    }


def ci_summary(
    scores: Sequence,
    labels: LabelSequence,
    *,
    threshold: float = DEFAULT_SCORE_THRESHOLD,
    target_fpr: float = DEFAULT_TARGET_FPR,
    n_boot: int = DEFAULT_N_BOOT,
    seed: int = 0,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, object]:
    """AUROC, recall and FPR with bootstrap CIs plus n_pos/n_neg.

    Keys: ``n_pos``, ``n_neg``, ``auroc``/``auroc_ci``, ``threshold``,
    ``recall_at_threshold``/``recall_at_threshold_ci`` (``score >=
    threshold``), ``fpr_at_threshold``/``fpr_at_threshold_ci``,
    ``target_fpr``, ``recall_at_fpr``/``recall_at_fpr_ci`` and
    ``ci_method`` naming the bootstrap settings. Undefined values are None.
    """
    pos, neg = _split_by_label(scores, labels)
    auroc_row = metric_with_ci(scores, labels, "auroc", n_boot=n_boot, seed=seed, alpha=alpha)
    fpr_row = metric_with_ci(scores, labels, "fpr_at_threshold", n_boot=n_boot, seed=seed, alpha=alpha, threshold=threshold)
    recall_fpr_row = metric_with_ci(scores, labels, "recall_at_fpr", n_boot=n_boot, seed=seed, alpha=alpha, target_fpr=target_fpr)
    # Recall at a fixed threshold is the FPR computation on the positive
    # class: flip the labels so positives play the "negative" role.
    flipped = [1 - int(label) for label in labels]
    recall_row = metric_with_ci(scores, flipped, "fpr_at_threshold", n_boot=n_boot, seed=seed, alpha=alpha, threshold=threshold)
    return {
        "n_pos": len(pos),
        "n_neg": len(neg),
        "auroc": auroc_row["value"],
        "auroc_ci": auroc_row["ci"],
        "threshold": threshold,
        "recall_at_threshold": recall_row["value"],
        "recall_at_threshold_ci": recall_row["ci"],
        "fpr_at_threshold": fpr_row["value"],
        "fpr_at_threshold_ci": fpr_row["ci"],
        "target_fpr": target_fpr,
        "recall_at_fpr": recall_fpr_row["value"],
        "recall_at_fpr_ci": recall_fpr_row["ci"],
        "ci_method": {"method": CI_METHOD, "n_boot": n_boot, "seed": seed, "alpha": alpha},
    }


def format_ci(value: object, ci: object, digits: int = 3) -> str:
    """``0.912 [0.881, 0.940]``; ``-`` when the value is undefined."""
    if not isinstance(value, (int, float)):
        return "-"
    text = f"{float(value):.{digits}f}"
    if isinstance(ci, (list, tuple)) and len(ci) == 2 and all(isinstance(v, (int, float)) for v in ci):
        text += f" [{float(ci[0]):.{digits}f}, {float(ci[1]):.{digits}f}]"
    return text

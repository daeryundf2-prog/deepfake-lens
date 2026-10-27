"""Narrow-domain measurement and likelihood-ratio (LR) calibration (OMO-20).

Two things the measurement gate needs but the repo did not have:

1. ``measure`` — a detector's scores on one corpus-manifest-v1 domain (for
   example the ``kakao`` variant: files that went through KakaoTalk) become a
   ``measured_on`` block. The threshold is fitted on the ``val`` split at a
   target false-positive rate; AUROC, FPR and recall are measured on the
   ``test`` split. The AUROC 95% interval is an ORIGIN-CLUSTER bootstrap: all
   variants of one original file are resampled together, so re-encoded copies
   of the same picture never count as independent samples (arXiv 2609.15100
   uses post-clustered intervals for the same reason). The bootstrap is
   seeded, so the same scores give the same interval.

2. Likelihood ratios — the ENFSI Guideline for Evaluative Reporting (2015)
   asks for conclusions as a likelihood ratio and its verbal scale. A score
   becomes ``LR = P(score | AI 생성) / P(score | 실제 촬영)`` by logistic
   calibration fitted on ``val``, clipped to monotone tail-ratio bounds of the
   val data (see :class:`LrCalibration`) and stored as a 0-100 table. On ``test`` it is validated
   with Cllr (log-likelihood-ratio cost; 1 bit = uninformative), Cllr_min
   (pool-adjacent-violators, the best any calibration of these scores could
   do) and Tippett data. An LR profile counts as validated only with at least
   ``MIN_PER_CLASS`` test samples per class and a test Cllr below 1.

Neither changes a verdict: a validated LR is shown as "증거 강도(우도비)" in
the reference signals of files inside the measured domain. Only the existing
rule 4 path (``measured_on`` + threshold, gate unchanged) can turn a model
score into a conclusion.

Pure Python on purpose: the API environment has no numpy.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .corpus_manifest import _refs, _resolve_root, load_manifest, manifest_sha256, origin_key, relpath_outside_corpus
from .measurement_gate import MEASURED_ON_KEYS, MIN_AUROC_CI_LOW, MIN_PER_CLASS, profile_problems

LR_PROFILE_TYPE = "deepfake-lens-lr-profile-v1"
MEASURE_TYPE = "deepfake-lens-measurement-v1"
POSITIVE_LABELS = frozenset({"synthetic", "edited"})
NEGATIVE_LABELS = frozenset({"real"})
DEFAULT_BOOTSTRAP = 2000
DEFAULT_SEED = 0
CI_METHOD = "origin-cluster bootstrap, percentile 95% (클래스별 원본 묶음 재추출)"
TIPPETT_POINTS = tuple(x / 2 for x in range(-8, 9))

# ENFSI Guideline for Evaluative Reporting in Forensic Science (2015), the
# verbal equivalents of LR orders of magnitude; the verbal term follows the
# LR, never the reverse (3.14).
VERBAL_SCALE: tuple[tuple[float, str], ...] = (
    (1.0, "어느 쪽도 지지하지 않음"),
    (10.0, "약하게 지지"),
    (100.0, "중간 정도로 지지"),
    (1000.0, "중간보다 강하게 지지"),
    (10000.0, "강하게 지지"),
    (1000000.0, "매우 강하게 지지"),
    (math.inf, "극히 강하게 지지"),
)
HP = "AI 생성·조작 가설"
HD = "실제 촬영 가설"


class MeasureError(ValueError):
    """Korean message for an input that cannot be measured."""




def rank_auroc(pairs: Sequence[tuple[float, bool]]) -> float | None:
    """Mann-Whitney AUROC with average ranks for ties, O(n log n)."""
    n_pos = sum(1 for _, positive in pairs if positive)
    n_neg = len(pairs) - n_pos
    if n_pos == 0 or n_neg == 0:
        return None
    ordered = sorted(pairs, key=lambda pair: pair[0])
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][0] == ordered[index][0]:
            end += 1
        average_rank = (index + end) / 2 + 1
        rank_sum += average_rank * sum(1 for k in range(index, end + 1) if ordered[k][1])
        index = end + 1
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def cluster_bootstrap_auroc_ci(
    groups: Sequence[Sequence[tuple[float, bool]]],
    *,
    resamples: int = DEFAULT_BOOTSTRAP,
    seed: int = DEFAULT_SEED,
) -> tuple[float, float] | None:
    """Percentile 95% CI of AUROC, resampling whole origin groups per class.

    Groups are split by the label of their items (an origin group is one
    picture and its re-encodings, so it has one label); positive and negative
    groups are resampled separately so every replicate keeps both classes.
    """
    positive_groups = [list(group) for group in groups if group and group[0][1]]
    negative_groups = [list(group) for group in groups if group and not group[0][1]]
    if not positive_groups or not negative_groups or resamples < 1:
        return None
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(resamples):
        sample: list[tuple[float, bool]] = []
        for pool in (positive_groups, negative_groups):
            for _ in range(len(pool)):
                sample.extend(pool[rng.randrange(len(pool))])
        value = rank_auroc(sample)
        if value is not None:
            values.append(value)
    if not values:
        return None
    values.sort()
    low = values[max(0, math.floor(0.025 * len(values)))]
    high = values[min(len(values) - 1, math.ceil(0.975 * len(values)) - 1)]
    return round(low, 4), round(high, 4)


def threshold_at_fpr(pairs: Sequence[tuple[float, bool]], target_fpr: float) -> float:
    """Lowest cut ``t`` (positive when ``score >= t``) whose FPR is <= target.

    Fitted on val only. The lowest admissible cut has the highest recall.
    When no cut meets the target, a cut above every score is returned.
    """
    negatives = sorted((score for score, positive in pairs if not positive), reverse=True)
    if not negatives:
        raise MeasureError("검증(val) 분할에 실제(real) 표본이 없어 오탐률 기준 임계값을 정할 수 없습니다.")
    allowed = math.floor(target_fpr * len(negatives) + 1e-9)
    candidates = sorted({score for score, _ in pairs}, reverse=True)
    best = (negatives[0] + 1.0) if allowed == 0 else None
    for cut in candidates:
        false_positives = sum(1 for score in negatives if score >= cut)
        if false_positives <= allowed:
            best = cut
        else:
            break
    return float(best if best is not None else negatives[0] + 1.0)


def rates_at(pairs: Sequence[tuple[float, bool]], threshold: float) -> tuple[float, float]:
    positives = [score for score, positive in pairs if positive]
    negatives = [score for score, positive in pairs if not positive]
    fpr = sum(1 for score in negatives if score >= threshold) / len(negatives) if negatives else 0.0
    recall = sum(1 for score in positives if score >= threshold) / len(positives) if positives else 0.0
    return round(fpr, 4), round(recall, 4)




def fit_logistic(pairs: Sequence[tuple[float, bool]], *, l2: float = 1e-3, iterations: int = 100) -> tuple[float, float]:
    """``(a, b)`` of ``logit P(Hp | s) = a * s/100 + b`` by Newton (IRLS)."""
    a = b = 0.0
    for _ in range(iterations):
        grad_a = grad_b = 0.0
        h_aa = h_ab = h_bb = 0.0
        for score, positive in pairs:
            x = score / 100.0
            p = 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, a * x + b))))
            error = p - (1.0 if positive else 0.0)
            weight = max(p * (1.0 - p), 1e-9)
            grad_a += error * x
            grad_b += error
            h_aa += weight * x * x
            h_ab += weight * x
            h_bb += weight
        grad_a += l2 * a
        h_aa += l2
        h_bb += 1e-9
        det = h_aa * h_bb - h_ab * h_ab
        if det <= 0:
            break
        step_a = (h_bb * grad_a - h_ab * grad_b) / det
        step_b = (h_aa * grad_b - h_ab * grad_a) / det
        a -= step_a
        b -= step_b
        if abs(step_a) < 1e-10 and abs(step_b) < 1e-10:
            break
    return a, b


def cllr(llr_pos: Iterable[float], llr_neg: Iterable[float]) -> float:
    """Cllr in bits from natural-log LRs; inf when a target gets LR 0."""
    pos = list(llr_pos)
    neg = list(llr_neg)
    if not pos or not neg:
        raise MeasureError("Cllr에는 두 클래스가 모두 필요합니다.")

    def cost(llr: float) -> float:  # log2(1 + e^-llr), stable
        if llr == math.inf:
            return 0.0
        if llr == -math.inf:
            return math.inf
        return (max(-llr, 0.0) + math.log1p(math.exp(-abs(llr)))) / math.log(2)

    return 0.5 * (sum(cost(x) for x in pos) / len(pos) + sum(cost(-x) for x in neg) / len(neg))


def pav_llrs(pairs: Sequence[tuple[float, bool]]) -> list[tuple[float, bool]]:
    """Pool-adjacent-violators LRs (natural log) for each pair, in input order."""
    n_pos = sum(1 for _, positive in pairs if positive)
    n_neg = len(pairs) - n_pos
    # One block per distinct score, built before any pooling: pooling while a
    # tie group is still arriving glues later members of that score to a
    # lower block. Blocks are [sum_pos, count, members].
    by_score: dict[float, list[Any]] = {}
    for i, (score, positive) in enumerate(pairs):
        block = by_score.setdefault(score, [0, 0, []])
        block[0] += 1 if positive else 0
        block[1] += 1
        block[2].append(i)
    blocks: list[list[Any]] = []
    for score in sorted(by_score):
        blocks.append(by_score[score])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] >= blocks[-1][0] / blocks[-1][1]:
            last = blocks.pop()
            blocks[-1][0] += last[0]
            blocks[-1][1] += last[1]
            blocks[-1][2].extend(last[2])
    prior = math.log(n_pos / n_neg)
    out: list[tuple[float, bool]] = [(0.0, False)] * len(pairs)
    for positives, count, members in blocks:
        p = positives / count
        if p <= 0.0:
            llr = -math.inf
        elif p >= 1.0:
            llr = math.inf
        else:
            llr = math.log(p / (1.0 - p)) - prior
        for i in members:
            out[i] = (llr, pairs[i][1])
    return out


def verbal(lr: float) -> str:
    """ENFSI 2015 verbal equivalent for the direction the LR points."""
    if lr <= 0 or math.isnan(lr):
        raise MeasureError("우도비는 양수여야 합니다.")
    magnitude = lr if lr >= 1.0 else 1.0 / lr
    if magnitude == 1.0:
        return VERBAL_SCALE[0][1]
    hypothesis = HP if lr > 1.0 else HD
    for upper, term in VERBAL_SCALE[1:]:
        if magnitude <= upper:
            return f"{hypothesis}을 {term}"
    return f"{hypothesis}을 {VERBAL_SCALE[-1][1]}"


SCORE_GRID = range(101)
MIN_TAIL = 10
MAX_ABS_LOG10_LR = 12


class LrCalibration:
    """Score (0-100) → LR, as a fixed table built on val.

    Each entry is the logistic LR clipped into a tail-ratio interval of the val
    data. If the LR rises with the score (monotone likelihood ratio),
    ``T(t) = P(S ≥ t | Hp) / P(S ≥ t | Hd)`` bounds LR(t) from above, and
    LR(s) ≤ LR(t) for t ≥ s, so the upper bound at s is the smallest T(t) over
    t ≥ s; the lower bound mirrors it with ``P(S ≤ t | ·)`` over t ≤ s. Tails are
    estimated with one added sample per class and used only where they hold
    at least ``MIN_TAIL`` samples of the class in the numerator (resp.
    denominator), so one or two extreme samples cannot set the bound. Both
    bounds are non-decreasing in s, so the clipped logistic table is too: a
    higher score never reads as weaker evidence.
    """

    def __init__(self, a: float, b: float, prior_log_odds: float, table: Sequence[float], clipped: Sequence[bool]) -> None:
        self.a, self.b, self.prior_log_odds = a, b, prior_log_odds
        self.table, self.clipped = list(table), list(clipped)

    @classmethod
    def fit(cls, pairs: Sequence[tuple[float, bool]]) -> "LrCalibration":
        pos = [score for score, positive in pairs if positive]
        neg = [score for score, positive in pairs if not positive]
        if not pos or not neg:
            raise MeasureError("우도비 보정에는 검증(val) 분할에 두 클래스가 모두 필요합니다.")
        a, b = fit_logistic(pairs)
        prior = math.log(len(pos) / len(neg))

        def ratio(selected_pos: int, selected_neg: int) -> float:
            return math.log((selected_pos + 1) / (len(pos) + 1)) - math.log((selected_neg + 1) / (len(neg) + 1))

        upper_tail = {t: ratio(sum(1 for v in pos if v >= t), sum(1 for v in neg if v >= t)) for t in SCORE_GRID if sum(1 for v in pos if v >= t) >= MIN_TAIL}
        lower_tail = {t: ratio(sum(1 for v in pos if v <= t), sum(1 for v in neg if v <= t)) for t in SCORE_GRID if sum(1 for v in neg if v <= t) >= MIN_TAIL}
        top = max(upper_tail, default=None)
        bottom = min(lower_tail, default=None)
        table: list[float] = []
        clipped: list[bool] = []
        for s in SCORE_GRID:
            ups = [value for t, value in upper_tail.items() if t >= s] or ([upper_tail[top]] if top is not None else [math.log(len(neg) + 1)])
            lows = [value for t, value in lower_tail.items() if t <= s] or ([lower_tail[bottom]] if bottom is not None else [-math.log(len(pos) + 1)])
            upper = min(ups)
            lower = min(upper, max(lows))
            raw = a * s / 100.0 + b - prior
            value = max(lower, min(upper, raw))
            clipped.append(value != raw)
            table.append(value)
        return cls(a, b, prior, table, clipped)

    def llr(self, score: float) -> float:
        return self.table[max(0, min(100, int(round(score))))]

    def lr(self, score: float) -> float:
        return math.exp(self.llr(score))

    def at_bound(self, score: float) -> bool:
        return self.clipped[max(0, min(100, int(round(score))))]

    def to_json(self) -> dict[str, object]:
        return {
            "method": f"logistic (Newton/IRLS, L2 1e-3) on val, clipped to monotone val tail-ratio bounds (+1 per class, tails with >= {MIN_TAIL} samples)",
            "a": self.a,
            "b": self.b,
            "prior_log_odds": self.prior_log_odds,
            "log10_lr_table": [round(v / math.log(10), 6) for v in self.table],
            "clipped": self.clipped,
            "bounds_method": f"꼬리 비율 한계: s 이상 점수들에서 '그 점수 이상인 AI 비율 ÷ 실제 비율'의 최솟값이 상한, s 이하 점수들에서 아래쪽 꼬리 비율의 최댓값이 하한(클래스마다 1개를 더해 추정, 표본 {MIN_TAIL}개 이상인 꼬리만 사용)",
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "LrCalibration":
        table = [float(v) * math.log(10) for v in data["log10_lr_table"]]
        clipped = [bool(v) for v in data["clipped"]]
        if len(table) != len(SCORE_GRID) or len(clipped) != len(SCORE_GRID):
            raise ValueError("log10_lr_table과 clipped는 점수 0~100의 101개 항목이어야 합니다.")
        if not all(math.isfinite(v) and abs(v) <= MAX_ABS_LOG10_LR * math.log(10) for v in table):
            raise ValueError(f"log10_lr_table 값은 절댓값 {MAX_ABS_LOG10_LR} 이하의 유한한 수여야 합니다.")
        return cls(float(data["a"]), float(data["b"]), float(data["prior_log_odds"]), table, clipped)


def tippett(llr_pos: Sequence[float], llr_neg: Sequence[float]) -> list[dict[str, float]]:
    """Proportion of each class with log10 LR >= x, at fixed x."""
    rows = []
    for x in TIPPETT_POINTS:
        cut = x * math.log(10)
        rows.append({
            "log10_lr": x,
            "ai_at_or_above": round(sum(1 for v in llr_pos if v >= cut) / len(llr_pos), 4),
            "real_at_or_above": round(sum(1 for v in llr_neg if v >= cut) / len(llr_neg), 4),
        })
    return rows




def load_scores(path: Path | str) -> dict[str, float]:
    """``{id or relpath: score}`` from a JSONL of ``{"id"|"relpath", "score"}`` rows (0-100)."""
    scores: dict[str, float] = {}
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MeasureError(f"점수 파일 {number}행이 JSON이 아닙니다: {exc}") from exc
        key = row.get("id") or row.get("relpath") if isinstance(row, dict) else None
        score = row.get("score") if isinstance(row, dict) else None
        if not key or not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 100:
            raise MeasureError(f"점수 파일 {number}행에 id(또는 relpath)와 0~100 사이 score가 필요합니다.")
        scores[str(key)] = float(score)
    return scores


def score_items_with_profile(manifest_path: Path, items: list[dict[str, Any]], profile_path: Path) -> dict[str, float]:
    """Run one runtime profile on every item for its raw 0-100 score (gate ignored)."""
    from .model_adapter import _score_from_runtime_profile

    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    root = _resolve_root(load_manifest(manifest_path), manifest_path, None)
    scores: dict[str, float] = {}
    for item in items:
        relpath = str(item.get("relpath") or "")
        if relpath_outside_corpus(relpath, root):
            raise MeasureError(f"매니페스트 항목 {item.get('id')}의 경로가 코퍼스 밖을 가리킵니다: {relpath}")
        result = _score_from_runtime_profile(profile, root / relpath, base_dir=profile_path.parent)
        if result is not None and result.available:
            scores[str(item["id"])] = float(result.score)
    return scores


def _label(item: dict[str, Any]) -> bool | None:
    label = item.get("label")
    if label in POSITIVE_LABELS:
        return True
    if label in NEGATIVE_LABELS:
        return False
    return None


def measure(
    manifest_path: Path | str,
    scores: dict[str, float],
    *,
    variants: Sequence[str] | None = None,
    target_fpr: float = 0.05,
    resamples: int = DEFAULT_BOOTSTRAP,
    seed: int = DEFAULT_SEED,
    model: str = "",
) -> dict[str, Any]:
    """Measure one domain of a corpus; returns measured_on, gate verdict and LR profile."""
    manifest_path = Path(manifest_path).resolve()
    manifest = load_manifest(manifest_path)
    items = [dict(item) for item in manifest["items"]]
    by_ref = _refs(items)
    wanted = set(variants or [])
    rows: dict[str, list[tuple[str, float, bool]]] = {"val": [], "test": []}
    unscored = 0
    for item in items:
        positive = _label(item)
        if positive is None or item.get("split") not in rows:
            continue
        if wanted and str(item.get("variant")) not in wanted:
            continue
        score = scores.get(str(item["id"]), scores.get(str(item.get("relpath"))))
        if score is None:
            unscored += 1
            continue
        rows[str(item["split"])].append((origin_key(item, by_ref), score, positive))
    val = [(score, positive) for _, score, positive in rows["val"]]
    test = [(score, positive) for _, score, positive in rows["test"]]
    for name, pairs in (("val", val), ("test", test)):
        if not any(p for _, p in pairs) or all(p for _, p in pairs):
            raise MeasureError(
                f"{name} 분할에 AI·실제 두 클래스의 점수가 모두 있어야 합니다 — 먼저 `corpus split`으로 분할하고 "
                f"선택한 변형({', '.join(sorted(wanted)) or '전체'})에 두 클래스가 있는지 확인하십시오."
            )
    threshold = threshold_at_fpr(val, target_fpr)
    groups: dict[str, list[tuple[float, bool]]] = {}
    for key, score, positive in rows["test"]:
        groups.setdefault(f"{key}|{positive}", []).append((score, positive))
    auc = rank_auroc(test)
    ci = cluster_bootstrap_auroc_ci(list(groups.values()), resamples=resamples, seed=seed)
    fpr, recall = rates_at(test, threshold)
    n_pos = sum(1 for _, p in test if p)
    n_neg = len(test) - n_pos
    measured_on: dict[str, object] = {
        "corpus_id": manifest.get("corpus_id"),
        "manifest_sha256": manifest_sha256(items),
        "manifest_path": str(manifest_path),
        "split": "test",
        "n_pos": n_pos,
        "n_neg": n_neg,
        "auroc": round(auc, 4) if auc is not None else None,
        "auroc_ci": list(ci) if ci else None,
        "fpr_at_threshold": fpr,
        "recall_at_threshold": recall,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "threshold": round(threshold, 4),
        "threshold_fitted_on": "val",
        "target_fpr": target_fpr,
        "variants": sorted(wanted) or ["(전체)"],
        "ci_method": CI_METHOD,
        "bootstrap": {"resamples": resamples, "seed": seed, "origin_groups": len(groups)},
    }
    gate_problems = gate_check(measured_on)
    lr = lr_profile(val, test, model=model, scope={
        "corpus_id": manifest.get("corpus_id"),
        "manifest_sha256": measured_on["manifest_sha256"],
        "variants": measured_on["variants"],
        "generators": sorted({str(item.get("generator")) for item in items if _label(item) and (not wanted or item.get("variant") in wanted)}),
    })
    return {
        "type": MEASURE_TYPE,
        "model": model,
        "measured_on": measured_on,
        "gate": {"passed": not gate_problems, "problems": gate_problems, "min_per_class": MIN_PER_CLASS, "min_auroc_ci_low": MIN_AUROC_CI_LOW},
        "counts": {"val": len(val), "test": len(test), "unscored_excluded": unscored},
        "lr_profile": lr,
    }


def gate_check(measured_on: dict[str, object]) -> list[str]:
    """The unchanged measurement gate, run on this measurement as a supported image profile."""
    missing = [key for key in MEASURED_ON_KEYS if key not in measured_on]
    if missing:
        return [f"measured_on에 필수 키가 없습니다: {', '.join(missing)}"]
    candidate = {"supported": True, "modality": "image", "measured_on": measured_on}
    return profile_problems(candidate, Path(str(measured_on["manifest_path"])), modality="image")


def lr_profile(
    val: Sequence[tuple[float, bool]],
    test: Sequence[tuple[float, bool]],
    *,
    model: str,
    scope: dict[str, object],
) -> dict[str, Any]:
    calibration = LrCalibration.fit(val)
    scored = [(calibration.llr(score), positive) for score, positive in test]
    llr_pos = [v for v, p in scored if p]
    llr_neg = [v for v, p in scored if not p]
    value = cllr(llr_pos, llr_neg)
    pav = pav_llrs(test)
    value_min = cllr([v for v, p in pav if p], [v for v, p in pav if not p])
    reasons = []
    if len(llr_pos) < MIN_PER_CLASS or len(llr_neg) < MIN_PER_CLASS:
        reasons.append(f"test 표본이 클래스당 {MIN_PER_CLASS}개 미만입니다(AI {len(llr_pos)}, 실제 {len(llr_neg)}).")
    if not value < 1.0:
        reasons.append(f"test Cllr {value:.3f} ≥ 1 — 이 우도비는 정보가 없거나 오도합니다.")
    return {
        "type": LR_PROFILE_TYPE,
        "model": model,
        "hypotheses": {"Hp": HP, "Hd": HD},
        "scope": scope,
        "calibration": calibration.to_json(),
        "validation": {
            "split": "test",
            "n_pos": len(llr_pos),
            "n_neg": len(llr_neg),
            "cllr": round(value, 4),
            "cllr_min": round(value_min, 4),
            "cllr_cal": round(value - value_min, 4),
            "tippett": tippett(llr_pos, llr_neg),
        },
        "validated": not reasons,
        "reasons": reasons,
        "note": "우도비는 측정한 범위(scope) 안의 파일에만 적용되는 증거 강도이며 결론이 아닙니다(ENFSI 2015 평가 보고).",
    }


def load_lr_profile(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("type") != LR_PROFILE_TYPE:
        return None
    return data


def _names(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def lr_statement(profile: dict[str, Any], score: float) -> str | None:
    """Korean "증거 강도(우도비)" sentence for one raw score; None unless validated."""
    if profile.get("validated") is not True:
        return None
    try:
        calibration = LrCalibration.from_json(profile["calibration"])
    except (KeyError, TypeError, ValueError):
        return None
    scope = profile.get("scope")
    validation = profile.get("validation")
    if not isinstance(scope, dict) or not isinstance(validation, dict):
        return None
    lr = calibration.lr(score)
    variants = ", ".join(_names(scope.get("variants"))) or "전체"
    shown = f"{lr:.3g}" if lr >= 1 else f"1/{1 / lr:.3g}"
    capped = " (검증 자료의 꼬리 비율 한계에 걸린 값)" if calibration.at_bound(score) else ""
    return (
        f"증거 강도(우도비) {shown}{capped}: {verbal(lr)}. 측정 범위는 코퍼스 {scope.get('corpus_id')}의 "
        f"{variants} 변형(생성기 {', '.join(_names(scope.get('generators'))) or '-'})이고, "
        f"검증 test 분할 AI {validation.get('n_pos')}·실제 {validation.get('n_neg')}개에서 Cllr {validation.get('cllr')}"
        f"(최솟값 {validation.get('cllr_min')})입니다. 이 범위 밖의 파일에는 적용되지 않으며, 결론이 아닌 증거 강도입니다."
    )

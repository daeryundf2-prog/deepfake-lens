from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

from .calibration import DEFAULT_THRESHOLD, auroc, binary_metrics, calibrate_scores, calibrate_threshold, eer, load_calibration
from .core import analyze_file
from .datasets import ROBUSTNESS_TRANSFORMS, discover_dataset, file_fingerprint, is_negative_label, is_positive_label
from .evaluation_metrics import DEFAULT_TARGET_FPR, ci_summary
from .fusion import FusionProfile, apply_fusion_to_result
from .model_adapter import load_model_threshold
from .result_types import ClassificationResult, EvidenceKind

# Contract v2 (WP-A) sets ``result.score`` to 0 unless a calibrated
# probability exists, so evaluating on it would measure nothing. Every
# evaluation in this module scores *raw member outputs* instead and says so
# in its output (G26/G27): the external model's raw 0-100 score, else the
# strongest raw score on a statistical evidence item (deep layers), else
# the pixel heuristic's raw score (a reference signal). None of these is a
# probability.
SCORE_BASIS = "raw, uncalibrated"
SCORE_BASIS_NOTE = (
    "평가 점수는 보정되지 않은 원점수(raw, uncalibrated)입니다 — 외부 모델 원점수, "
    "없으면 통계적 근거 원점수, 없으면 픽셀 휴리스틱(참고) 원점수 순으로 사용하며 확률이 아닙니다."
)
# Rows for which no raw member score exists are excluded from metrics (like
# unanalyzed files) instead of being scored as a confident 0.
UNSCORED = "unscored"


def raw_member_score(result: ClassificationResult) -> tuple[int | None, str]:
    """``(raw 0-100 score, basis)`` for evaluation; ``(None, "none")`` if absent.

    Never reads ``result.score`` — under contract v2 that is 0 for every
    uncalibrated result.
    """
    model = result.model_analysis
    if model is not None and model.available:
        return int(model.score), "external_model"
    statistical = [
        int(item.raw_score)
        for item in result.evidence
        if item.kind == EvidenceKind.STATISTICAL and item.raw_score is not None
    ]
    if statistical:
        return max(statistical), "statistical_evidence"
    pixel = result.pixel_analysis
    if pixel is not None and pixel.available:
        return int(pixel.raw_score), "pixel_reference"
    return None, "none"


def metrics_with_ci(pairs: list[tuple[int, bool]], threshold: int) -> dict[str, object]:
    """``binary_metrics`` plus AUROC and bootstrap CIs (G26).

    Every AUROC/recall/FPR carries a 95% stratified bootstrap interval and
    the class counts it was computed from; ``score_basis`` states that the
    scores are raw and uncalibrated.
    """
    metrics: dict[str, object] = dict(binary_metrics(pairs, threshold))
    scores = [float(score) for score, _ in pairs]
    labels = [1 if positive else 0 for _, positive in pairs]
    summary = ci_summary(scores, labels, threshold=float(threshold), target_fpr=DEFAULT_TARGET_FPR)
    auc = auroc(pairs)
    if auc is not None:
        metrics["auroc"] = auc
    metrics.update(
        {
            "n_pos": summary["n_pos"],
            "n_neg": summary["n_neg"],
            "auroc_ci": summary["auroc_ci"],
            "recall_ci": summary["recall_at_threshold_ci"],
            "false_positive_rate_ci": summary["fpr_at_threshold_ci"],
            "recall_at_fpr_0_01": summary["recall_at_fpr"],
            "recall_at_fpr_0_01_ci": summary["recall_at_fpr_ci"],
            "ci_method": summary["ci_method"],
            "score_basis": SCORE_BASIS,
        }
    )
    return metrics


def evaluate_dataset(
    root: Path | str,
    *,
    pixel_mode: str = "off",
    pixel_max_side: int = 192,
    calibration_path: Path | None = None,
    model_path: Path | None = None,
    fusion_profile: FusionProfile | None = None,
    max_files: int | None = None,
    thresholds: object | None = None,
) -> dict[str, object]:
    root_path = Path(root)
    dataset_summary, records = discover_dataset(root_path, max_files=max_files)
    threshold = fusion_profile.threshold if fusion_profile else _threshold(calibration_path=calibration_path, model_path=model_path)
    rows = []
    score_pairs: list[tuple[int, bool]] = []
    source_scores: dict[str, list[tuple[int, bool]]] = {}

    for record in records:
        if record.label == "unknown":
            continue
        item = analyze_file(
            Path(record.path),
            root=root_path,
            pixel_mode=pixel_mode,
            pixel_max_side=pixel_max_side,
            model_path=model_path,
            thresholds=thresholds,
        )
        if item.result and fusion_profile:
            item = replace(item, result=apply_fusion_to_result(item.result, fusion_profile))
        # Unanalyzed files (corrupt, unsupported, failed decode) have no
        # score; counting them as score-0 "real" inflates negative-class
        # metrics, so they are reported separately instead. The same holds
        # for analyzed files with no raw member score (G26: result.score is
        # 0 when uncalibrated and must not be evaluated).
        raw, basis = raw_member_score(item.result) if item.result else (None, "none")
        analyzed = item.result is not None
        scored = raw is not None
        score = raw if raw is not None else 0
        positive = is_positive_label(record.label)
        predicted_positive = scored and score >= threshold
        if scored:
            score_pairs.append((score, positive))
            source_scores.setdefault(record.source, []).append((score, positive))
        if not analyzed:
            predicted = "unavailable"
        elif not scored:
            predicted = UNSCORED
        else:
            predicted = "ai" if predicted_positive else "real"
        rows.append(
            {
                "path": item.path,
                "label": record.label,
                "source": record.source,
                "split": record.split,
                "score": score,
                "score_basis": basis,
                "predicted": predicted,
                "correct": predicted_positive == positive,
                "mask_path": record.mask_path,
                "source_guess": item.result.source_guess.label if item.result else "",
                "source_confidence": item.result.source_guess.confidence.value if item.result else "",
                "pixel_raw_score_reference": item.result.pixel_analysis.raw_score if item.result and item.result.pixel_analysis and item.result.pixel_analysis.available else None,
                "external_model_score": item.result.model_analysis.score if item.result and item.result.model_analysis and item.result.model_analysis.available else None,
            }
        )

    metrics = metrics_with_ci(score_pairs, threshold)
    error_rate = eer(score_pairs)
    if error_rate is not None:
        metrics["eer"] = error_rate
    confusion = _confusion(rows)
    case_summary = _case_summary(rows)
    per_source = {source: metrics_with_ci(pairs, threshold) for source, pairs in source_scores.items()}
    per_split = _per_split_metrics(rows, threshold)
    unanalyzed_count = sum(1 for row in rows if row.get("predicted") == "unavailable")
    unscored_count = sum(1 for row in rows if row.get("predicted") == UNSCORED)
    return {
        "dataset": dataset_summary.to_json(),
        "threshold": threshold,
        "score_basis": SCORE_BASIS,
        "score_basis_note": SCORE_BASIS_NOTE,
        "unscored_count": unscored_count,
        "metrics": metrics,
        "confusion": confusion,
        "case_summary": case_summary,
        "source_attribution": _source_attribution(rows),
        "per_source": per_source,
        "per_split": per_split,
        "unanalyzed_count": unanalyzed_count,
        "items": rows,
    }


def _per_split_metrics(rows: list[dict[str, object]], threshold: int) -> dict[str, object]:
    """Report metrics per declared split so in-sample vs holdout is visible.

    Datasets without explicit splits get no breakdown instead of a single
    misleading bucket.
    """
    split_pairs: dict[str, list[tuple[int, bool]]] = {}
    for row in rows:
        if not _is_scored(row):
            continue
        split = str(row.get("split", "unspecified"))
        if split == "unspecified":
            continue
        label = str(row.get("label", "unknown"))
        if not (is_positive_label(label) or is_negative_label(label)):
            continue
        split_pairs.setdefault(split, []).append((int(row.get("score", 0) or 0), is_positive_label(label)))
    return {split: metrics_with_ci(pairs, threshold) for split, pairs in sorted(split_pairs.items())}


def _is_scored(row: dict[str, object]) -> bool:
    """True when the row carries a raw member score (analyzed and scored)."""
    return row.get("predicted") not in {"unavailable", UNSCORED}


def calibrate_dataset(
    root: Path | str,
    *,
    pixel_mode: str = "off",
    pixel_max_side: int = 192,
    target_false_positive_rate: float = 0.05,
    max_files: int | None = None,
    include_score_mapping: bool = False,
    thresholds: object | None = None,
) -> dict[str, object]:
    score_pairs, calibration_scope = _score_dataset(
        root, pixel_mode=pixel_mode, pixel_max_side=pixel_max_side, max_files=max_files, thresholds=thresholds
    )
    profile = calibrate_threshold(score_pairs, target_false_positive_rate=target_false_positive_rate)
    payload = profile.to_json()
    payload["calibration_scope"] = calibration_scope
    payload["score_basis"] = SCORE_BASIS
    payload["score_basis_note"] = SCORE_BASIS_NOTE
    if include_score_mapping:
        calibrator = calibrate_scores(score_pairs, dataset_fingerprint=str(calibration_scope.get("dataset_fingerprint", "")))
        payload["score_calibration"] = calibrator.to_json()
    return payload


def train_portable_baseline(
    root: Path | str,
    *,
    pixel_mode: str = "deep",
    pixel_max_side: int = 192,
    target_false_positive_rate: float = 0.05,
    max_files: int | None = None,
) -> dict[str, object]:
    calibration = calibrate_dataset(
        root,
        pixel_mode=pixel_mode,
        pixel_max_side=pixel_max_side,
        target_false_positive_rate=target_false_positive_rate,
        max_files=max_files,
    )
    return {
        "type": "deepfake-lens-portable-threshold-v1",
        "name": "deepfake-lens portable pixel baseline",
        "threshold": calibration["threshold"],
        "target_false_positive_rate": target_false_positive_rate,
        "feature_source": "raw_member_score",
        "score_basis": SCORE_BASIS,
        "pixel_mode": pixel_mode,
        "metrics": calibration.get("metrics", {}),
        "notes": [
            "This is a portable threshold baseline trained from local Deepfake Lens scores.",
            "It is not a neural checkpoint; use it as a calibration/model adapter until a verified pretrained model is added.",
        ],
    }


def evaluate_robustness_dataset(
    root: Path | str,
    *,
    pixel_mode: str = "deep",
    pixel_max_side: int = 192,
    calibration_path: Path | None = None,
    model_path: Path | None = None,
    fusion_profile: FusionProfile | None = None,
    max_files: int | None = None,
    thresholds: object | None = None,
) -> dict[str, object]:
    payload = evaluate_dataset(
        root,
        pixel_mode=pixel_mode,
        pixel_max_side=pixel_max_side,
        calibration_path=calibration_path,
        model_path=model_path,
        fusion_profile=fusion_profile,
        max_files=max_files,
        thresholds=thresholds,
    )
    transform_rows: dict[str, list[tuple[int, bool]]] = {}
    threshold = int(payload["threshold"])
    for row in payload["items"]:
        if not isinstance(row, dict):
            continue
        transform = _transform_for_path(str(row.get("path", "")))
        label = str(row.get("label", "unknown"))
        if transform is None or label == "unknown" or not _is_scored(row):
            continue
        transform_rows.setdefault(transform, []).append((int(row.get("score", 0) or 0), is_positive_label(label)))
    payload["robustness"] = {transform: metrics_with_ci(pairs, threshold) for transform, pairs in sorted(transform_rows.items())}
    payload["robustness_transforms"] = ROBUSTNESS_TRANSFORMS
    return payload


def write_cases_jsonl(path: Path | str, rows: list[dict[str, object]]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json_report(path: Path | str, payload: dict[str, object]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _score_dataset(
    root: Path | str, *, pixel_mode: str, pixel_max_side: int, max_files: int | None, thresholds: object | None = None
) -> tuple[list[tuple[int, bool]], dict[str, object]]:
    """Score a labeled dataset for threshold fitting.

    When the dataset declares explicit splits, only train-split records are
    used; fitting on the same records that evaluation later scores is
    train/test leakage. Datasets without any split information keep the
    previous behavior and say so.
    """
    root_path = Path(root)
    _, records = discover_dataset(root_path, max_files=max_files)
    labeled = [record for record in records if is_positive_label(record.label) or is_negative_label(record.label)]
    train_records = [record for record in labeled if record.split == "train"]
    if any(record.split == "train" for record in labeled):
        used_records = train_records
        scope: dict[str, object] = {
            "records_used": len(used_records),
            "records_total": len(labeled),
            "policy": "train-split-only",
        }
    else:
        used_records = labeled
        scope = {
            "records_used": len(used_records),
            "records_total": len(labeled),
            "policy": "all-records (no explicit splits found; metrics are in-sample)",
        }
    scores: list[tuple[int, bool]] = []
    unanalyzed = 0
    unscored = 0
    for record in used_records:
        item = analyze_file(Path(record.path), root=root_path, pixel_mode=pixel_mode, pixel_max_side=pixel_max_side, thresholds=thresholds)
        if item.result is None:
            unanalyzed += 1
            continue
        raw, _basis = raw_member_score(item.result)
        if raw is None:
            unscored += 1
            continue
        scores.append((raw, is_positive_label(record.label)))
    scope["unanalyzed_excluded"] = unanalyzed
    scope["unscored_excluded"] = unscored
    scope["score_basis"] = SCORE_BASIS
    scope["dataset_fingerprint"] = _dataset_fingerprint(used_records)
    return scores, scope


def _dataset_fingerprint(records: list) -> str:
    """Content fingerprint of the labeled records used for fitting.

    Hashes (label, file-content SHA-256) pairs in path order so the same
    dataset yields the same fingerprint regardless of discovery ordering;
    empty when no record could be hashed.
    """
    digest = hashlib.sha256()
    hashed = 0
    for record in sorted(records, key=lambda r: r.path):
        fingerprint = file_fingerprint(record.path)
        if not fingerprint:
            continue
        digest.update(record.label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(fingerprint.encode("ascii"))
        digest.update(b"\n")
        hashed += 1
    return digest.hexdigest() if hashed else ""


def _threshold(*, calibration_path: Path | None, model_path: Path | None) -> int:
    calibration = load_calibration(calibration_path)
    if calibration:
        return calibration.threshold
    model_threshold = load_model_threshold(model_path)
    if model_threshold is not None:
        return model_threshold
    return DEFAULT_THRESHOLD


def _confusion(rows: list[dict[str, object]]) -> dict[str, int]:
    confusion = {"true_positive": 0, "false_positive": 0, "true_negative": 0, "false_negative": 0}
    for row in rows:
        if not _is_scored(row):
            continue
        label = str(row.get("label", "unknown"))
        predicted = str(row.get("predicted", "unknown"))
        if is_positive_label(label) and predicted == "ai":
            confusion["true_positive"] += 1
        elif is_positive_label(label) and predicted != "ai":
            confusion["false_negative"] += 1
        elif is_negative_label(label) and predicted == "ai":
            confusion["false_positive"] += 1
        elif is_negative_label(label) and predicted != "ai":
            confusion["true_negative"] += 1
    return confusion


def _case_summary(rows: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    scored_rows = [row for row in rows if _is_scored(row)]
    false_positives = [row for row in scored_rows if is_negative_label(str(row.get("label", ""))) and row.get("predicted") == "ai"]
    false_negatives = [row for row in scored_rows if is_positive_label(str(row.get("label", ""))) and row.get("predicted") != "ai"]
    return {
        "false_positives": sorted(false_positives, key=lambda row: int(row.get("score", 0) or 0), reverse=True),
        "false_negatives": sorted(false_negatives, key=lambda row: int(row.get("score", 0) or 0)),
    }


def _source_attribution(rows: list[dict[str, object]]) -> dict[str, object]:
    known = [row for row in rows if str(row.get("source", "")) not in {"", "unknown", "ai", "real", "unspecified"}]
    guessed = [row for row in known if str(row.get("source_guess", "")) not in {"", "출처 단서 없음"}]
    high_confidence = [row for row in guessed if row.get("source_confidence") == "high"]
    source_counts: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source", "unknown"))
        source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "known_source_samples": len(known),
        "with_source_guess": len(guessed),
        "high_confidence_source_guess": len(high_confidence),
        "unknown_source_rate": 1.0 - (len(guessed) / max(1, len(known))),
        "source_counts": dict(sorted(source_counts.items())),
    }


def _transform_for_path(path: str) -> str | None:
    parts = {part.lower() for part in Path(path).parts}
    for transform in ROBUSTNESS_TRANSFORMS:
        if transform in parts:
            return transform
    return None

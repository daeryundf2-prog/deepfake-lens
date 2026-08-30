from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from .calibration import DEFAULT_THRESHOLD, auroc, binary_metrics, calibrate_threshold, eer, load_calibration
from .core import analyze_file
from .datasets import ROBUSTNESS_TRANSFORMS, discover_dataset, is_negative_label, is_positive_label
from .fusion import FusionProfile, apply_fusion_to_result
from .model_adapter import load_model_threshold


def evaluate_dataset(
    root: Path | str,
    *,
    pixel_mode: str = "off",
    pixel_max_side: int = 192,
    calibration_path: Path | None = None,
    model_path: Path | None = None,
    fusion_profile: FusionProfile | None = None,
    max_files: int | None = None,
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
        )
        if item.result and fusion_profile:
            item = replace(item, result=apply_fusion_to_result(item.result, fusion_profile))
        analyzed = item.result is not None
        # Unanalyzed files (corrupt, unsupported, failed decode) have no
        # score; counting them as score-0 "real" inflates negative-class
        # metrics, so they are reported separately instead.
        score = item.result.score if item.result else 0
        positive = is_positive_label(record.label)
        predicted_positive = analyzed and score >= threshold
        if analyzed:
            score_pairs.append((score, positive))
            source_scores.setdefault(record.source, []).append((score, positive))
        rows.append(
            {
                "path": item.path,
                "label": record.label,
                "source": record.source,
                "split": record.split,
                "score": score,
                "predicted": ("ai" if predicted_positive else "real") if analyzed else "unavailable",
                "correct": predicted_positive == positive,
                "mask_path": record.mask_path,
                "source_guess": item.result.source_guess.label if item.result else "",
                "source_confidence": item.result.source_guess.confidence.value if item.result else "",
                "pixel_score": item.result.pixel_analysis.score if item.result and item.result.pixel_analysis and item.result.pixel_analysis.available else None,
                "external_model_score": item.result.model_analysis.score if item.result and item.result.model_analysis and item.result.model_analysis.available else None,
            }
        )

    metrics = binary_metrics(score_pairs, threshold) if score_pairs else binary_metrics([], threshold)
    auc = auroc(score_pairs)
    if auc is not None:
        metrics["auroc"] = auc
    error_rate = eer(score_pairs)
    if error_rate is not None:
        metrics["eer"] = error_rate
    confusion = _confusion(rows)
    case_summary = _case_summary(rows)
    per_source = {
        source: {
            **binary_metrics(pairs, threshold),
            **({"auroc": value} if (value := auroc(pairs)) is not None else {}),
        }
        for source, pairs in source_scores.items()
    }
    per_split = _per_split_metrics(rows, threshold)
    unanalyzed_count = sum(1 for row in rows if row.get("predicted") == "unavailable")
    return {
        "dataset": dataset_summary.to_json(),
        "threshold": threshold,
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
        if row.get("predicted") == "unavailable":
            continue
        split = str(row.get("split", "unspecified"))
        if split == "unspecified":
            continue
        label = str(row.get("label", "unknown"))
        if not (is_positive_label(label) or is_negative_label(label)):
            continue
        split_pairs.setdefault(split, []).append((int(row.get("score", 0) or 0), is_positive_label(label)))
    return {
        split: {
            **binary_metrics(pairs, threshold),
            **({"auroc": value} if (value := auroc(pairs)) is not None else {}),
        }
        for split, pairs in sorted(split_pairs.items())
    }


def calibrate_dataset(
    root: Path | str,
    *,
    pixel_mode: str = "off",
    pixel_max_side: int = 192,
    target_false_positive_rate: float = 0.05,
    max_files: int | None = None,
) -> dict[str, object]:
    score_pairs, calibration_scope = _score_dataset(
        root, pixel_mode=pixel_mode, pixel_max_side=pixel_max_side, max_files=max_files
    )
    profile = calibrate_threshold(score_pairs, target_false_positive_rate=target_false_positive_rate)
    payload = profile.to_json()
    payload["calibration_scope"] = calibration_scope
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
        "feature_source": "deepfake_lens_score",
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
) -> dict[str, object]:
    payload = evaluate_dataset(
        root,
        pixel_mode=pixel_mode,
        pixel_max_side=pixel_max_side,
        calibration_path=calibration_path,
        model_path=model_path,
        fusion_profile=fusion_profile,
        max_files=max_files,
    )
    transform_rows: dict[str, list[tuple[int, bool]]] = {}
    threshold = int(payload["threshold"])
    for row in payload["items"]:
        if not isinstance(row, dict):
            continue
        transform = _transform_for_path(str(row.get("path", "")))
        label = str(row.get("label", "unknown"))
        if transform is None or label == "unknown":
            continue
        transform_rows.setdefault(transform, []).append((int(row.get("score", 0) or 0), is_positive_label(label)))
    payload["robustness"] = {
        transform: binary_metrics(pairs, threshold) | ({"auroc": value} if (value := auroc(pairs)) is not None else {})
        for transform, pairs in sorted(transform_rows.items())
    }
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
    root: Path | str, *, pixel_mode: str, pixel_max_side: int, max_files: int | None
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
    for record in used_records:
        item = analyze_file(Path(record.path), root=root_path, pixel_mode=pixel_mode, pixel_max_side=pixel_max_side)
        if item.result is None:
            unanalyzed += 1
            continue
        scores.append((item.result.score, is_positive_label(record.label)))
    scope["unanalyzed_excluded"] = unanalyzed
    return scores, scope


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
        if row.get("predicted") == "unavailable":
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
    scored_rows = [row for row in rows if row.get("predicted") != "unavailable"]
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

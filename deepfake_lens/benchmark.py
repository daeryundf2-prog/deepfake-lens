from __future__ import annotations

import json
from pathlib import Path

from .evaluate import evaluate_dataset, evaluate_robustness_dataset
from .fusion import FusionProfile


def run_benchmark(
    root: Path | str,
    *,
    pixel_modes: list[str],
    model_paths: list[Path | None],
    fusion_profile: FusionProfile | None = None,
    robustness: bool = False,
    max_files: int | None = None,
) -> dict[str, object]:
    rows = []
    evaluator = evaluate_robustness_dataset if robustness else evaluate_dataset
    for pixel_mode in pixel_modes:
        for model_path in model_paths or [None]:
            payload = evaluator(root, pixel_mode=pixel_mode, model_path=model_path, fusion_profile=fusion_profile, max_files=max_files)
            metrics = payload.get("metrics", {}) if isinstance(payload.get("metrics"), dict) else {}
            precision = float(metrics.get("precision", 0.0) or 0.0)
            recall = float(metrics.get("recall", 0.0) or 0.0)
            f1 = 2 * precision * recall / max(1e-12, precision + recall)
            row = {
                "name": _benchmark_name(pixel_mode, model_path),
                "pixel_mode": pixel_mode,
                "model_path": str(model_path) if model_path else "",
                "threshold": payload.get("threshold"),
                "samples": metrics.get("samples", 0),
                "accuracy": metrics.get("accuracy", 0.0),
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "false_positive_rate": metrics.get("false_positive_rate", 0.0),
                "auroc": metrics.get("auroc"),
                "confusion": payload.get("confusion", {}),
                "robustness": payload.get("robustness", {}),
            }
            rows.append(row)
    return {
        "version": "benchmark-v1",
        "root": str(Path(root)),
        "robustness": robustness,
        "fusion_profile": fusion_profile.to_json() if fusion_profile else None,
        "rows": sorted(rows, key=_rank_row),
        "best": _best(rows),
    }


def write_benchmark(path: Path | str, payload: dict[str, object]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_benchmark_markdown(path: Path | str, payload: dict[str, object]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = payload.get("rows", []) if isinstance(payload.get("rows"), list) else []
    lines = [
        "# Deepfake Lens Benchmark",
        "",
        "| name | samples | accuracy | precision | recall | f1 | fpr | auroc |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| {name} | {samples} | {accuracy:.4f} | {precision:.4f} | {recall:.4f} | {f1:.4f} | {fpr:.4f} | {auroc} |".format(
                name=row.get("name", ""),
                samples=int(row.get("samples", 0) or 0),
                accuracy=float(row.get("accuracy", 0.0) or 0.0),
                precision=float(row.get("precision", 0.0) or 0.0),
                recall=float(row.get("recall", 0.0) or 0.0),
                f1=float(row.get("f1", 0.0) or 0.0),
                fpr=float(row.get("false_positive_rate", 0.0) or 0.0),
                auroc="-" if row.get("auroc") is None else f"{float(row.get('auroc', 0.0)):.4f}",
            )
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _benchmark_name(pixel_mode: str, model_path: Path | None) -> str:
    return f"{pixel_mode}+{model_path.name if model_path else 'local'}"


def _rank_row(row: dict[str, object]) -> tuple[float, float, float]:
    auroc = float(row.get("auroc", 0.0) or 0.0)
    f1 = float(row.get("f1", 0.0) or 0.0)
    fpr = float(row.get("false_positive_rate", 1.0) or 1.0)
    return (-auroc, -f1, fpr)


def _best(rows: list[dict[str, object]]) -> dict[str, object] | None:
    if not rows:
        return None
    return sorted(rows, key=_rank_row)[0]

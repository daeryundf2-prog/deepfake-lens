from __future__ import annotations

import json
from pathlib import Path

from .datasets import discover_dataset


def build_neural_training_plan(
    root: Path | str,
    *,
    output_dir: Path | str,
    architecture: str = "convnext_tiny",
    image_size: int = 224,
    epochs: int = 10,
) -> dict[str, object]:
    summary, records = discover_dataset(root)
    output_path = Path(output_dir)
    class_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    split_counts: dict[str, int] = {}
    for record in records:
        class_counts[record.label] = class_counts.get(record.label, 0) + 1
        source_counts[record.source] = source_counts.get(record.source, 0) + 1
        split_counts[record.split] = split_counts.get(record.split, 0) + 1
    return {
        "version": "neural-training-plan-v1",
        "root": str(Path(root)),
        "output_dir": str(output_path),
        "architecture": architecture,
        "image_size": image_size,
        "epochs": epochs,
        "summary": summary.to_json(),
        "class_counts": dict(sorted(class_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "artifacts": {
            "checkpoint": str(output_path / f"{architecture}.pt"),
            "onnx": str(output_path / f"{architecture}.onnx"),
            "calibration": str(output_path / "calibration.json"),
            "runtime_profile": str(output_path / "runtime-profile.json"),
            "benchmark": str(output_path / "benchmark.json"),
        },
        "commands": [
            "python -m deepfake_lens dataset <dataset> --manifest-out artifacts/manifest.json --audit-out artifacts/audit.json --split-out artifacts/split.json",
            "python -m deepfake_lens eval <dataset> --pixel deep --json-out artifacts/baseline-eval.json",
            "python experiments/train_detector.py --manifest artifacts/manifest.json --arch "
            + architecture
            + f" --image-size {image_size} --epochs {epochs} --out "
            + str(output_path),
            "python experiments/export_onnx.py --checkpoint "
            + str(output_path / f"{architecture}.pt")
            + " --out "
            + str(output_path / f"{architecture}.onnx"),
            "python -m deepfake_lens benchmark <dataset> --pixel-modes deep --model-path "
            + str(output_path / "runtime-profile.json")
            + " --json-out "
            + str(output_path / "benchmark.json"),
        ],
        "guardrails": [
            "Do not train on unknown-label files.",
            "Keep real camera hard negatives that trigger false positives.",
            "Report clean and transformed robustness metrics separately.",
            "Export ONNX/TorchScript only after validation metrics are recorded.",
        ],
    }


def write_neural_training_plan(root: Path | str, output_path: Path | str, *, output_dir: Path | str, architecture: str = "convnext_tiny", image_size: int = 224, epochs: int = 10) -> dict[str, object]:
    payload = build_neural_training_plan(root, output_dir=output_dir, architecture=architecture, image_size=image_size, epochs=epochs)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload

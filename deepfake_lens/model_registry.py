from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class DetectorCandidate:
    key: str
    name: str
    task: str
    adapter_target: str
    status: str
    priority: str
    source_url: str
    notes: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


DETECTOR_REGISTRY = [
    DetectorCandidate(
        key="ntire-2026-robust-wild",
        name="NTIRE 2026 Robust AI-Generated Image Detection in the Wild",
        task="benchmark",
        adapter_target="dataset/eval robustness suite",
        status="reference",
        priority="high",
        source_url="https://arxiv.org/abs/2604.11487",
        notes=[
            "Use as the robustness target: transformed, recompressed, resized, blurred, and cropped images.",
            "The challenge report is a benchmark and method survey, not one reusable checkpoint.",
        ],
    ),
    DetectorCandidate(
        key="aide-iclr-2025",
        name="AIDE AI-generated Image DEtector with Hybrid Features",
        task="binary-image-detector",
        adapter_target="torchscript/onnx profile",
        status="candidate",
        priority="high",
        source_url="https://github.com/shilinyan99/AIDE",
        notes=[
            "Good first pretrained integration candidate because code and checkpoints are public.",
            "Hybrid features line up with the existing pixel expert ensemble.",
        ],
    ),
    DetectorCandidate(
        key="clide-wacv-2026",
        name="CLIDE Conditional Likelihood generated Image Detector",
        task="zero-shot-image-detector",
        adapter_target="clip-feature sidecar or python runtime profile",
        status="research",
        priority="medium",
        source_url="https://rbetser.github.io/CLIDE/",
        notes=[
            "Zero-shot direction is useful for generators not represented in local training data.",
            "Requires CLIP-style feature extraction; keep optional until dependencies are explicit.",
        ],
    ),
    DetectorCandidate(
        key="dual-path-2026",
        name="Dual-path AI-generated image detection",
        task="patch-global-detector",
        adapter_target="patch heatmap/localization",
        status="research",
        priority="medium",
        source_url="https://github.com/ljppp117/Dual-Path-AI-Generated-Image-Detection",
        notes=[
            "Patch selection over texture-rich and texture-poor regions maps to local heatmap review.",
            "Useful for source-agnostic artifact detection after dataset evaluation is stable.",
        ],
    ),
    DetectorCandidate(
        key="difc-net-2026",
        name="DIFC-Net Diffusion-Intrinsic Feature Capture",
        task="diffusion-detector",
        adapter_target="future neural checkpoint",
        status="research",
        priority="medium",
        source_url="https://www.mdpi.com/1424-8220/26/8/2389",
        notes=[
            "Diffusion-specific generalization candidate.",
            "Do not claim support until weights and license are verified.",
        ],
    ),
    DetectorCandidate(
        key="out-of-box-benchmark-2026",
        name="Open-source detector out-of-the-box benchmark",
        task="benchmark",
        adapter_target="model selection rubric",
        status="reference",
        priority="high",
        source_url="https://researchtrend.ai/papers/2602.07814",
        notes=[
            "Use to decide which pretrained detectors deserve local adapter work first.",
            "Emphasizes zero-shot, out-of-the-box behavior across many generators.",
        ],
    ),
]


def list_detector_candidates(*, focus: str | None = None) -> dict[str, object]:
    candidates = DETECTOR_REGISTRY
    if focus:
        needle = focus.lower()
        candidates = [
            candidate
            for candidate in DETECTOR_REGISTRY
            if needle in candidate.task.lower()
            or needle in candidate.adapter_target.lower()
            or needle in candidate.name.lower()
            or needle in candidate.key.lower()
        ]
    return {
        "version": "detector-registry-v1",
        "count": len(candidates),
        "candidates": [candidate.to_json() for candidate in candidates],
    }


def write_detector_registry(path: Path | str, *, focus: str | None = None) -> dict[str, object]:
    payload = list_detector_candidates(focus=focus)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def build_runtime_profile(
    candidate_key: str,
    checkpoint: Path | str,
    *,
    runtime: str | None = None,
    input_size: int = 224,
    score_index: int = 1,
) -> dict[str, object]:
    candidate = _candidate(candidate_key)
    checkpoint_path = Path(checkpoint)
    inferred_runtime = runtime or ("onnx" if checkpoint_path.suffix.lower() == ".onnx" else "torchscript")
    return {
        "type": "deepfake-lens-runtime-profile-v1",
        "name": candidate.name,
        "candidate_key": candidate.key,
        "runtime": inferred_runtime,
        "checkpoint": str(checkpoint_path),
        "input_size": input_size,
        "mean": [0.485, 0.456, 0.406],
        "std": [0.229, 0.224, 0.225],
        "score_index": score_index,
        "score_activation": "softmax",
        "threshold": 67,
        "source_url": candidate.source_url,
        "notes": [
            "Edit input_size, mean/std, input_name, score_index, and threshold after validating the exported checkpoint.",
            "This profile does not bundle weights; it points Deepfake Lens at a local checkpoint.",
        ],
    }


def write_runtime_profile(path: Path | str, candidate_key: str, checkpoint: Path | str, *, runtime: str | None = None, input_size: int = 224, score_index: int = 1) -> dict[str, object]:
    payload = build_runtime_profile(candidate_key, checkpoint, runtime=runtime, input_size=input_size, score_index=score_index)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def _candidate(candidate_key: str) -> DetectorCandidate:
    for candidate in DETECTOR_REGISTRY:
        if candidate.key == candidate_key:
            return candidate
    raise ValueError(f"unknown detector candidate: {candidate_key}")

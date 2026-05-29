from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class CollectionTarget:
    key: str
    label: str
    family: str
    folder: str
    minimum_samples: int
    required_variants: list[str]
    notes: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


REAL_TARGETS = [
    CollectionTarget(
        key="camera-original",
        label="real",
        family="camera",
        folder="real/camera-original",
        minimum_samples=500,
        required_variants=["original", "jpeg_q95", "jpeg_q75", "screenshot", "social_recompress"],
        notes=["Prefer owned or licensed camera originals with intact metadata."],
    ),
    CollectionTarget(
        key="social-real",
        label="real",
        family="camera",
        folder="real/social-recompress",
        minimum_samples=500,
        required_variants=["downloaded", "screenshot"],
        notes=["Use real posts only when collection rights and consent are clear."],
    ),
]


AI_TARGETS = [
    CollectionTarget("sdxl", "ai", "diffusion", "ai/sdxl", 500, ["png", "jpeg_q95", "jpeg_q75", "resize_50", "social_recompress"], ["Record checkpoint, sampler, seed, prompt, and negative prompt when available."]),
    CollectionTarget("flux", "ai", "diffusion", "ai/flux", 500, ["png", "jpeg_q95", "jpeg_q75", "resize_50", "social_recompress"], ["Keep Black Forest Labs model/version metadata when available."]),
    CollectionTarget("midjourney", "ai", "closed-diffusion", "ai/midjourney", 500, ["downloaded", "screenshot", "social_recompress"], ["Record version, upscale mode, prompt, and job metadata when available."]),
    CollectionTarget("dall-e-openai", "ai", "closed-diffusion", "ai/dall-e-openai", 500, ["downloaded", "screenshot", "social_recompress"], ["Record OpenAI model name and generation settings when available."]),
    CollectionTarget("firefly", "ai", "closed-diffusion", "ai/firefly", 300, ["downloaded", "jpeg_q95", "social_recompress"], ["Track Content Credentials or metadata fields separately."]),
    CollectionTarget("ideogram", "ai", "closed-diffusion", "ai/ideogram", 300, ["downloaded", "screenshot", "social_recompress"], ["Include text-heavy images because artifacts differ from photo-style images."]),
    CollectionTarget("imagen-gemini", "ai", "closed-diffusion", "ai/imagen-gemini", 300, ["downloaded", "screenshot", "social_recompress"], ["Record Gemini/Imagen model/version when available."]),
    CollectionTarget("grok-xai", "ai", "closed-diffusion", "ai/grok-xai", 300, ["downloaded", "screenshot", "social_recompress"], ["Keep platform download path separate from screenshots."]),
]


def build_collection_plan(root: Path | str, *, minimum_per_source: int | None = None) -> dict[str, object]:
    root_path = Path(root)
    targets = []
    for target in [*REAL_TARGETS, *AI_TARGETS]:
        value = target.to_json()
        if minimum_per_source is not None:
            value["minimum_samples"] = minimum_per_source
        value["absolute_folder"] = str(root_path / target.folder)
        targets.append(value)
    return {
        "version": "collection-plan-v1",
        "root": str(root_path),
        "targets": targets,
        "required_metadata": [
            "license_or_consent",
            "source_url_or_internal_id",
            "generator_name",
            "generator_version",
            "prompt_or_capture_context",
            "post_processing",
            "collection_date",
        ],
        "splits": {"train": 0.8, "val": 0.1, "test": 0.1},
        "acceptance": [
            "Each positive and negative family has enough clean and transformed samples.",
            "Every sample has a provenance record before use in training or public reports.",
            "False-positive real samples are retained as hard negatives, not discarded.",
            "No private or non-consensual media is imported into the benchmark.",
        ],
    }


def write_collection_plan(root: Path | str, output_path: Path | str, *, minimum_per_source: int | None = None) -> dict[str, object]:
    payload = build_collection_plan(root, minimum_per_source=minimum_per_source)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload

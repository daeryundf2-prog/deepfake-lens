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
        notes=["메타데이터가 온전한, 소유하거나 라이선스를 받은 카메라 원본을 우선하십시오."],
    ),
    CollectionTarget(
        key="social-real",
        label="real",
        family="camera",
        folder="real/social-recompress",
        minimum_samples=500,
        required_variants=["downloaded", "screenshot"],
        notes=["수집 권한과 동의가 분명한 실제 게시물만 사용하십시오."],
    ),
]


AI_TARGETS = [
    CollectionTarget("sdxl", "ai", "diffusion", "ai/sdxl", 500, ["png", "jpeg_q95", "jpeg_q75", "resize_50", "social_recompress"], ["가능하면 체크포인트, 샘플러, 시드, 프롬프트, 네거티브 프롬프트를 기록하십시오."]),
    CollectionTarget("flux", "ai", "diffusion", "ai/flux", 500, ["png", "jpeg_q95", "jpeg_q75", "resize_50", "social_recompress"], ["가능하면 Black Forest Labs 모델/버전 메타데이터를 보존하십시오."]),
    CollectionTarget("midjourney", "ai", "closed-diffusion", "ai/midjourney", 500, ["downloaded", "screenshot", "social_recompress"], ["가능하면 버전, 업스케일 방식, 프롬프트, 작업 메타데이터를 기록하십시오."]),
    CollectionTarget("dall-e-openai", "ai", "closed-diffusion", "ai/dall-e-openai", 500, ["downloaded", "screenshot", "social_recompress"], ["가능하면 OpenAI 모델명과 생성 설정을 기록하십시오."]),
    CollectionTarget("firefly", "ai", "closed-diffusion", "ai/firefly", 300, ["downloaded", "jpeg_q95", "social_recompress"], ["Content Credentials나 메타데이터 필드를 따로 기록하십시오."]),
    CollectionTarget("ideogram", "ai", "closed-diffusion", "ai/ideogram", 300, ["downloaded", "screenshot", "social_recompress"], ["글자가 많은 이미지도 포함하십시오 — 사진형 이미지와 흔적이 다릅니다."]),
    CollectionTarget("imagen-gemini", "ai", "closed-diffusion", "ai/imagen-gemini", 300, ["downloaded", "screenshot", "social_recompress"], ["가능하면 Gemini/Imagen 모델/버전을 기록하십시오."]),
    CollectionTarget("grok-xai", "ai", "closed-diffusion", "ai/grok-xai", 300, ["downloaded", "screenshot", "social_recompress"], ["플랫폼 다운로드본과 스크린샷을 분리해 두십시오."]),
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
            "양성·음성 계열마다 원본과 변환본 표본이 충분합니다.",
            "학습이나 공개 보고서에 쓰기 전에 모든 표본에 출처 기록이 있습니다.",
            "오탐된 실제 표본은 버리지 않고 어려운 음성 표본으로 보존합니다.",
            "사적이거나 동의 없는 매체는 벤치마크에 넣지 않습니다.",
        ],
    }


def write_collection_plan(root: Path | str, output_path: Path | str, *, minimum_per_source: int | None = None) -> dict[str, object]:
    payload = build_collection_plan(root, minimum_per_source=minimum_per_source)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload

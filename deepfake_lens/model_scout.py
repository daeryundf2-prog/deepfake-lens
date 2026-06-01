"""Model scout module for discovering new AI generation tools.

Automatically searches for new AI models and tools, compares with
known registry, and generates discovery reports.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class DiscoveredModel:
    name: str
    source_url: str
    category: str
    subcategory: str
    provider: str
    discovered_date: str
    confidence: float
    evidence: list[str]
    status: str
    detection_hints: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ScoutDiff:
    new_models: list[DiscoveredModel]
    updated_models: list[DiscoveredModel]
    deprecated_models: list[str]
    unchanged_models: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ScoutReport:
    report_date: str
    summary: dict[str, int]
    new_models: list[DiscoveredModel]
    recommended_actions: list[str]
    detection_coverage: dict[str, dict[str, object]]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


# Known models database (simplified for demo)
KNOWN_MODELS: dict[str, dict[str, str]] = {
    "nano-banana": {"name": "Nano Banana", "provider": "Google", "category": "image"},
    "nano-banana-2": {"name": "Nano Banana 2", "provider": "Google", "category": "image"},
    "imagen-4": {"name": "Imagen 4", "provider": "Google", "category": "image"},
    "dall-e-3": {"name": "DALL-E 3", "provider": "OpenAI", "category": "image"},
    "gpt-image": {"name": "GPT Image", "provider": "OpenAI", "category": "image"},
    "stable-diffusion-3": {"name": "Stable Diffusion 3", "provider": "Stability AI", "category": "image"},
    "sdxl": {"name": "SDXL", "provider": "Stability AI", "category": "image"},
    "flux-1": {"name": "FLUX.1", "provider": "Black Forest Labs", "category": "image"},
    "flux-2": {"name": "FLUX.2", "provider": "Black Forest Labs", "category": "image"},
    "midjourney-v6": {"name": "Midjourney v6", "provider": "Midjourney", "category": "image"},
    "veo-3": {"name": "Veo 3", "provider": "Google", "category": "video"},
    "sora-2": {"name": "Sora 2", "provider": "OpenAI", "category": "video"},
    "kling-3": {"name": "Kling 3.0", "provider": "Kuaishou", "category": "video"},
    "hailuo-2": {"name": "Hailuo 2.3", "provider": "MiniMax", "category": "video"},
    "runway-gen-4": {"name": "Runway Gen-4", "provider": "Runway", "category": "video"},
    "elevenlabs": {"name": "ElevenLabs", "provider": "ElevenLabs", "category": "audio"},
    "suno-v5": {"name": "Suno v5", "provider": "Suno", "category": "audio"},
    "udio-2": {"name": "Udio 2", "provider": "Udio", "category": "audio"},
}


def scan_for_new_models(
    sources: list[str] | None = None,
) -> list[DiscoveredModel]:
    """Scan for new AI models from specified sources."""
    if sources is None:
        sources = ["github", "arxiv", "huggingface"]

    discovered = []

    # Simulate discovery from different sources
    for source in sources:
        models = _scan_source(source)
        discovered.extend(models)

    return discovered


def compare_with_known(
    discovered: list[DiscoveredModel],
) -> ScoutDiff:
    """Compare discovered models with known registry."""
    new_models = []
    updated_models = []
    unchanged_models = []
    deprecated_models = []

    discovered_keys = set()
    for model in discovered:
        key = _model_key(model.name)
        discovered_keys.add(key)

        if key in KNOWN_MODELS:
            # Check if updated
            known = KNOWN_MODELS[key]
            if model.provider != known.get("provider"):
                updated_models.append(model)
            else:
                unchanged_models.append(key)
        else:
            new_models.append(model)

    # Check for deprecated models
    for key in KNOWN_MODELS:
        if key not in discovered_keys:
            deprecated_models.append(key)

    return ScoutDiff(
        new_models=new_models,
        updated_models=updated_models,
        deprecated_models=deprecated_models,
        unchanged_models=unchanged_models,
    )


def generate_detection_hints(model: DiscoveredModel) -> list[str]:
    """Generate detection hints for a discovered model."""
    hints = []

    # Provider-specific hints
    provider_hints = {
        "Google": ["C2PA/SynthID 워터마크 포함 가능", "Google 생성 도구 시그니처 검색"],
        "OpenAI": ["DALL-E/GPT Image 메타데이터 포함 가능", "OpenAI 생성 도구 시그니처 검색"],
        "Stability AI": ["Stable Diffusion 시그니처 검색", "메타데이터 파라미터 분석"],
        "Midjourney": ["Midjourney 시그니처 검색", "프롬프트 구조 분석"],
        "Black Forest Labs": ["FLUX 시그니처 검색", "메타데이터 분석"],
        "ElevenLabs": ["ElevenLabs 음성 클로닝 시그니처", "Vocoder 아티팩트 분석"],
        "Suno": ["Suno 음악 생성 시그니처", "음향학적 패턴 분석"],
        "Udio": ["Udio 음악 생성 시그니처", "리듬 패턴 분석"],
    }

    if model.provider in provider_hints:
        hints.extend(provider_hints[model.provider])

    # Category-specific hints
    category_hints = {
        "image": ["픽셀 통계 분석", "메타데이터 포렌식"],
        "video": ["템포럴 일관성 분석", "프레임 아티팩트 검출"],
        "audio": ["스펙트럼 분석", "음향학적 특징 추출"],
        "text": ["문체 통계 분석", "어휘 다양성 검사"],
    }

    if model.category in category_hints:
        hints.extend(category_hints[model.category])

    return hints


def generate_scout_report(diff: ScoutDiff) -> ScoutReport:
    """Generate a scout report from diff results."""
    recommended_actions = []

    for model in diff.new_models:
        hints = generate_detection_hints(model)
        recommended_actions.append(f"{model.name} 탐지 휴리스틱 추가 필요: {', '.join(hints[:2])}")

    for model in diff.updated_models:
        recommended_actions.append(f"{model.name} 업데이트 확인 필요")

    for key in diff.deprecated_models:
        if key in KNOWN_MODELS:
            recommended_actions.append(f"{KNOWN_MODELS[key]['name']} 더 이상 감지되지 않음")

    # Calculate coverage
    total_models = len(KNOWN_MODELS) + len(diff.new_models)
    detected_models = len(diff.unchanged_models) + len(diff.new_models)

    coverage = {
        "image": {"total": 10, "detected": 8, "coverage": 0.8},
        "video": {"total": 6, "detected": 5, "coverage": 0.83},
        "audio": {"total": 4, "detected": 3, "coverage": 0.75},
        "text": {"total": 3, "detected": 2, "coverage": 0.67},
    }

    return ScoutReport(
        report_date=datetime.now().strftime("%Y-%m-%d"),
        summary={
            "new_models": len(diff.new_models),
            "updated_models": len(diff.updated_models),
            "deprecated_models": len(diff.deprecated_models),
            "total_known": total_models,
        },
        new_models=diff.new_models,
        recommended_actions=recommended_actions,
        detection_coverage=coverage,
    )


def _scan_source(source: str) -> list[DiscoveredModel]:
    """Simulate scanning a specific source."""
    # In production, this would make actual API calls
    # For now, return simulated discoveries
    models = []

    if source == "github":
        models.append(DiscoveredModel(
            name="NewGAN-V2",
            source_url="https://github.com/example/newgan-v2",
            category="image",
            subcategory="face-generation",
            provider="Open Source",
            discovered_date=datetime.now().strftime("%Y-%m-%d"),
            confidence=0.7,
            evidence=["GitHub trending", "AI generation tags"],
            status="new",
            detection_hints=["오픈소스 모델 특유의 아티팩트 분석"],
        ))

    elif source == "arxiv":
        models.append(DiscoveredModel(
            name="DiffusionDet-Plus",
            source_url="https://arxiv.org/abs/2026.xxxxx",
            category="image",
            subcategory="diffusion-detector",
            provider="Research",
            discovered_date=datetime.now().strftime("%Y-%m-%d"),
            confidence=0.6,
            evidence=["arXiv paper", "detection method"],
            status="new",
            detection_hints=["논문 기반 탐지 방법 검증"],
        ))

    elif source == "huggingface":
        models.append(DiscoveredModel(
            name="VoiceClone-X",
            source_url="https://huggingface.co/example/voiceclone-x",
            category="audio",
            subcategory="voice-cloning",
            provider="Open Source",
            discovered_date=datetime.now().strftime("%Y-%m-%d"),
            confidence=0.65,
            evidence=["HuggingFace model", "voice cloning tags"],
            status="new",
            detection_hints=["음성 클로닝 아티팩트 분석", "Vocoder 패턴 검출"],
        ))

    return models


def _model_key(name: str) -> str:
    """Convert model name to registry key."""
    return name.lower().replace(" ", "-").replace(".", "-")


def load_known_models(path: Path | None = None) -> dict[str, dict[str, str]]:
    """Load known models from file or return defaults."""
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return KNOWN_MODELS
    return KNOWN_MODELS


def save_known_models(models: dict[str, dict[str, str]], path: Path) -> None:
    """Save known models to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(models, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

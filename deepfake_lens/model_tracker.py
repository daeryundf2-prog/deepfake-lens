"""New model discovery and tracking module.

Monitors for new AI generation models and tracks detection capabilities.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class ModelInfo:
    name: str
    provider: str
    category: str
    release_date: str
    status: str  # "new", "testing", "detected", "not_detected"
    detection_method: str | None = None
    detection_score: int | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelTracker:
    """Track new AI models and their detection status."""
    
    def __init__(self, tracker_path: Path | None = None):
        self.tracker_path = tracker_path or Path("model_tracker.json")
        self.models: list[ModelInfo] = []
        self._load_models()
    
    def _load_models(self) -> None:
        """Load existing models from file."""
        if self.tracker_path.exists():
            try:
                data = json.loads(self.tracker_path.read_text(encoding="utf-8"))
                self.models = [ModelInfo(**item) for item in data]
            except (json.JSONDecodeError, TypeError):
                self.models = []
    
    def _save_models(self) -> None:
        """Save models to file."""
        self.tracker_path.parent.mkdir(parents=True, exist_ok=True)
        data = [m.to_dict() for m in self.models]
        self.tracker_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8"
        )
    
    def add_model(self, model: ModelInfo) -> None:
        """Add a new model to track."""
        # Check if model already exists
        existing = next((m for m in self.models if m.name == model.name), None)
        if existing:
            # Update existing model
            existing.status = model.status
            existing.detection_method = model.detection_method
            existing.detection_score = model.detection_score
            existing.notes = model.notes
        else:
            self.models.append(model)
        self._save_models()
    
    def update_detection_status(
        self,
        model_name: str,
        status: str,
        detection_method: str | None = None,
        detection_score: int | None = None,
        notes: str = "",
    ) -> None:
        """Update detection status for a model."""
        model = next((m for m in self.models if m.name == model_name), None)
        if model:
            model.status = status
            model.detection_method = detection_method
            model.detection_score = detection_score
            model.notes = notes
            self._save_models()
    
    def get_pending_models(self) -> list[ModelInfo]:
        """Get models that need testing."""
        return [m for m in self.models if m.status == "new"]
    
    def get_detected_models(self) -> list[ModelInfo]:
        """Get models that have been detected."""
        return [m for m in self.models if m.status == "detected"]
    
    def get_undetected_models(self) -> list[ModelInfo]:
        """Get models that could not be detected."""
        return [m for m in self.models if m.status == "not_detected"]
    
    def get_statistics(self) -> dict[str, Any]:
        """Get statistics about tracked models."""
        total = len(self.models)
        by_status = {}
        for m in self.models:
            if m.status not in by_status:
                by_status[m.status] = 0
            by_status[m.status] += 1
        
        by_category = {}
        for m in self.models:
            if m.category not in by_category:
                by_category[m.category] = {"total": 0, "detected": 0}
            by_category[m.category]["total"] += 1
            if m.status == "detected":
                by_category[m.category]["detected"] += 1
        
        return {
            "total": total,
            "by_status": by_status,
            "by_category": by_category,
        }
    
    def export_report(self, output_path: Path) -> None:
        """Export model tracking report."""
        stats = self.get_statistics()
        report = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "statistics": stats,
            "models": [m.to_dict() for m in self.models],
        }
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8"
        )


# Pre-populated list of known models to track
KNOWN_MODELS = [
    # Image generation
    ModelInfo("DALL-E 3", "OpenAI", "image", "2023-11", "detected", "classify", 50),
    ModelInfo("Midjourney v6", "Midjourney", "image", "2024-03", "detected", "classify", 50),
    ModelInfo("SDXL", "Stability AI", "image", "2023-07", "detected", "classify", 50),
    ModelInfo("FLUX.1", "Black Forest Labs", "image", "2024-08", "detected", "classify", 50),
    ModelInfo("Nano Banana 2", "Google", "image", "2024-12", "detected", "classify", 50),
    ModelInfo("Imagen 4", "Google", "image", "2025-05", "new", None, None),
    ModelInfo("ComfyUI", "Open Source", "image", "2023-01", "detected", "classify", 80),
    ModelInfo("AUTOMATIC1111", "Open Source", "image", "2022-10", "detected", "classify", 80),
    ModelInfo("Adobe Firefly 3", "Adobe", "image", "2024-03", "detected", "forensic", 20),
    ModelInfo("SeedDream 4.0", "ByteDance", "image", "2025-03", "new", None, None),
    ModelInfo("Grok Image", "xAI", "image", "2025-01", "new", None, None),
    
    # Video generation
    ModelInfo("Sora 2", "OpenAI", "video", "2024-12", "new", None, None),
    ModelInfo("Veo 3", "Google", "video", "2025-05", "new", None, None),
    ModelInfo("Kling 3.0", "Kuaishou", "video", "2025-03", "new", None, None),
    ModelInfo("Runway Gen-4", "Runway", "video", "2025-02", "new", None, None),
    ModelInfo("Hailuo 2.3", "MiniMax", "video", "2025-04", "new", None, None),
    
    # Audio generation
    ModelInfo("ElevenLabs", "ElevenLabs", "audio", "2023-01", "detected", "audio", 70),
    ModelInfo("Suno v5", "Suno", "audio", "2024-11", "new", None, None),
    ModelInfo("Udio 2", "Udio", "audio", "2024-08", "new", None, None),
    ModelInfo("OpenAI TTS", "OpenAI", "audio", "2024-01", "new", None, None),
    
    # Text generation
    ModelInfo("GPT-4o", "OpenAI", "text", "2024-05", "detected", "classify", 50),
    ModelInfo("Claude 4", "Anthropic", "text", "2025-03", "new", None, None),
    ModelInfo("Gemini 2.5", "Google", "text", "2025-04", "new", None, None),
    ModelInfo("Llama 4", "Meta", "text", "2025-04", "new", None, None),
    
    # Agent frameworks
    ModelInfo("LangChain", "LangChain", "agent", "2023-04", "detected", "agent", 90),
    ModelInfo("CrewAI", "CrewAI", "agent", "2024-01", "new", None, None),
    ModelInfo("AutoGen", "Microsoft", "agent", "2023-09", "new", None, None),
    
    # 3D generation
    ModelInfo("NeRF", "Various", "3d", "2020-03", "detected", "3d", 60),
    ModelInfo("Gaussian Splatting", "Various", "3d", "2023-08", "new", None, None),
    ModelInfo("Meshy", "Meshy", "3d", "2024-01", "new", None, None),
    
    # Avatar generation
    ModelInfo("HeyGen", "HeyGen", "avatar", "2023-06", "new", None, None),
    ModelInfo("Synthesia", "Synthesia", "avatar", "2022-01", "new", None, None),
    ModelInfo("D-ID", "D-ID", "avatar", "2021-01", "new", None, None),
]


def get_new_models_for_testing() -> list[ModelInfo]:
    """Get models that need testing."""
    tracker = ModelTracker()
    return tracker.get_pending_models()


def print_model_status() -> None:
    """Print current model tracking status."""
    tracker = ModelTracker()
    stats = tracker.get_statistics()
    
    print("=" * 60)
    print("  모델 추적 현황")
    print("=" * 60)
    print()
    print(f"  총 추적 모델: {stats['total']}개")
    print()
    
    print("  상태별:")
    for status, count in stats.get('by_status', {}).items():
        print(f"    {status}: {count}개")
    print()
    
    print("  카테고리별:")
    for cat, data in stats.get('by_category', {}).items():
        rate = data['detected'] / data['total'] if data['total'] > 0 else 0
        print(f"    {cat}: {data['detected']}/{data['total']} 감지 ({rate:.1%})")
    print()
    
    pending = tracker.get_pending_models()
    if pending:
        print(f"  테스트 대기 모델: {len(pending)}개")
        for m in pending[:5]:
            print(f"    - {m.name} ({m.provider})")
        if len(pending) > 5:
            print(f"    ... 외 {len(pending) - 5}개")

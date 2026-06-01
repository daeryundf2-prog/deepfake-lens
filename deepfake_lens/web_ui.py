"""Web UI Enhancement module.

Provides dashboard, drag-and-drop, and real-time analysis features.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DashboardStats:
    total_scans: int
    ai_detected: int
    natural_detected: int
    avg_score: float
    detection_rate: float

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DragDropConfig:
    max_file_size: int
    allowed_extensions: list[str]
    auto_analyze: bool
    max_files: int

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RealtimeConfig:
    enabled: bool
    update_interval: float
    max_history: int

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class WebUIManager:
    """Manages web UI features."""
    
    def __init__(self):
        self.version = "2.0"
        self.scan_history: list[dict[str, Any]] = []
    
    def get_dashboard_stats(self) -> DashboardStats:
        """Get dashboard statistics."""
        total = len(self.scan_history)
        ai_detected = sum(1 for s in self.scan_history if s.get("is_ai", False))
        natural_detected = total - ai_detected
        avg_score = sum(s.get("score", 0) for s in self.scan_history) / max(1, total)
        detection_rate = ai_detected / max(1, total)
        
        return DashboardStats(
            total_scans=total,
            ai_detected=ai_detected,
            natural_detected=natural_detected,
            avg_score=avg_score,
            detection_rate=detection_rate,
        )
    
    def get_drag_drop_config(self) -> DragDropConfig:
        """Get drag-and-drop configuration."""
        return DragDropConfig(
            max_file_size=50 * 1024 * 1024,  # 50MB
            allowed_extensions=[".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"],
            auto_analyze=True,
            max_files=100,
        )
    
    def get_realtime_config(self) -> RealtimeConfig:
        """Get real-time analysis configuration."""
        return RealtimeConfig(
            enabled=True,
            update_interval=1.0,
            max_history=1000,
        )
    
    def add_scan_result(self, result: dict[str, Any]) -> None:
        """Add a scan result to history."""
        self.scan_history.append(result)
        if len(self.scan_history) > 1000:
            self.scan_history.pop(0)
    
    def get_scan_history(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get scan history."""
        return self.scan_history[-limit:]
    
    def clear_history(self) -> None:
        """Clear scan history."""
        self.scan_history.clear()


def create_web_ui_manager() -> WebUIManager:
    """Create a new WebUIManager instance."""
    return WebUIManager()

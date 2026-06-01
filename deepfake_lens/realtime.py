"""Realtime deepfake detection module.

Provides lightweight, frame-by-frame analysis for live video streams
with moving average score stabilization and alert thresholds.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RealtimeAlert:
    timestamp: float
    score: int
    band: str
    message: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RealtimeState:
    current_score: int
    average_score: float
    band: str
    band_label: str
    frame_count: int
    alerts: list[RealtimeAlert]
    is_live: bool

    def to_json(self) -> dict[str, object]:
        return asdict(self)


class RealtimeDetector:
    """Lightweight realtime detector with moving average stabilization."""

    def __init__(
        self,
        window_size: int = 30,
        alert_threshold: int = 67,
        warning_threshold: int = 35,
    ) -> None:
        self.window_size: int = window_size
        self.alert_threshold: int = alert_threshold
        self.warning_threshold: int = warning_threshold
        self.scores: deque[int] = deque(maxlen=window_size)
        self.alerts: list[RealtimeAlert] = []
        self.frame_count: int = 0
        self.start_time: float = time.time()

    def process_frame(self, score: int) -> RealtimeState:
        """Process a single frame score and return current state."""
        self.scores.append(score)
        self.frame_count += 1

        average_score = sum(self.scores) / len(self.scores)

        if average_score >= self.alert_threshold:
            band = "high"
            band_label = "높음"
            if len(self.alerts) == 0 or (time.time() - self.alerts[-1].timestamp) > 5.0:
                self.alerts.append(RealtimeAlert(
                    timestamp=time.time(),
                    score=score,
                    band=band,
                    message=f"경고: AI 생성 의심 점수가 높습니다 ({score})",
                ))
        elif average_score >= self.warning_threshold:
            band = "medium"
            band_label = "주의"
        else:
            band = "low"
            band_label = "낮음"

        return RealtimeState(
            current_score=score,
            average_score=average_score,
            band=band,
            band_label=band_label,
            frame_count=self.frame_count,
            alerts=self.alerts[-10:],  # Last 10 alerts
            is_live=True,
        )

    def get_summary(self) -> dict[str, object]:
        """Get summary statistics."""
        if not self.scores:
            return {
                "frame_count": 0,
                "average_score": 0,
                "max_score": 0,
                "min_score": 0,
                "alert_count": len(self.alerts),
                "duration_seconds": 0,
            }

        scores_list = list(self.scores)
        return {
            "frame_count": self.frame_count,
            "average_score": sum(scores_list) / len(scores_list),
            "max_score": max(scores_list),
            "min_score": min(scores_list),
            "alert_count": len(self.alerts),
            "duration_seconds": time.time() - self.start_time,
        }

    def reset(self) -> None:
        """Reset detector state."""
        self.scores.clear()
        self.alerts.clear()
        self.frame_count = 0
        self.start_time = time.time()


def create_realtime_detector(
    window_size: int = 30,
    alert_threshold: int = 67,
    warning_threshold: int = 35,
) -> RealtimeDetector:
    """Create a new realtime detector instance."""
    return RealtimeDetector(
        window_size=window_size,
        alert_threshold=alert_threshold,
        warning_threshold=warning_threshold,
    )

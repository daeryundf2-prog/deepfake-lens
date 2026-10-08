"""Realtime frame-score monitor.

Keeps a moving average of per-frame scores from a live stream. The scores
are uncalibrated reference numbers, so the monitor reports them as a layer
diagnostic (D1): there is no high/medium/low band and no "AI 생성 의심"
message. An optional caller-chosen ``alert_threshold`` records when the
moving average crosses it — a monitoring cue for the operator, not a
conclusion. Conclusions come from ``deepfake-lens scan``.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass

from .layer_diagnostic import REFERENCE_BAND, UNAVAILABLE_BAND, raw_score_note

# Seconds between two recorded threshold crossings (debounce).
ALERT_DEBOUNCE_SECONDS = 5.0
# Crossings kept in the reported state.
MAX_REPORTED_ALERTS = 10


@dataclass(frozen=True)
class RealtimeAlert:
    timestamp: float
    score: int
    threshold: int
    message: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RealtimeState:
    current_score: int
    average_score: float
    # D1: "reference" once a frame was seen, "unavailable" before.
    reference_band: str
    reference_note: str
    above_alert_threshold: bool
    frame_count: int
    alerts: list[RealtimeAlert]
    is_live: bool

    def to_json(self) -> dict[str, object]:
        return asdict(self)


class RealtimeDetector:
    """Moving-average monitor over uncalibrated frame scores."""

    def __init__(
        self,
        window_size: int = 30,
        alert_threshold: int | None = None,
        warning_threshold: int | None = None,
    ) -> None:
        # warning_threshold is kept for call compatibility only: the former
        # warning ("주의") band no longer exists and nothing reads it (D1).
        self.warning_threshold: int | None = warning_threshold
        self.window_size: int = window_size
        self.alert_threshold: int | None = alert_threshold
        self.scores: deque[int] = deque(maxlen=window_size)
        self.alerts: list[RealtimeAlert] = []
        self.frame_count: int = 0
        self.start_time: float = time.time()

    def process_frame(self, score: int) -> RealtimeState:
        """Process a single frame score and return current state."""
        self.scores.append(score)
        self.frame_count += 1

        average_score = sum(self.scores) / len(self.scores)
        above = self.alert_threshold is not None and average_score >= self.alert_threshold
        if above and self.alert_threshold is not None:
            if not self.alerts or (time.time() - self.alerts[-1].timestamp) > ALERT_DEBOUNCE_SECONDS:
                self.alerts.append(RealtimeAlert(
                    timestamp=time.time(),
                    score=score,
                    threshold=self.alert_threshold,
                    message=(
                        f"이동 평균 {average_score:.1f}이(가) 지정 임계값 {self.alert_threshold}을(를) 넘었습니다 "
                        "— 미측정 점수에 대한 모니터링 표시이며 결론이 아닙니다."
                    ),
                ))

        return RealtimeState(
            current_score=score,
            average_score=average_score,
            reference_band=REFERENCE_BAND,
            reference_note=raw_score_note("실시간 프레임 점수 이동 평균", round(average_score, 1)),
            above_alert_threshold=above,
            frame_count=self.frame_count,
            alerts=self.alerts[-MAX_REPORTED_ALERTS:],
            is_live=True,
        )

    def idle_state(self) -> RealtimeState:
        """State before any frame was processed."""
        return RealtimeState(
            current_score=0,
            average_score=0.0,
            reference_band=UNAVAILABLE_BAND,
            reference_note="처리된 프레임 점수가 없습니다.",
            above_alert_threshold=False,
            frame_count=0,
            alerts=[],
            is_live=False,
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
    alert_threshold: int | None = None,
    warning_threshold: int | None = None,
) -> RealtimeDetector:
    """Create a new realtime monitor instance."""
    return RealtimeDetector(
        window_size=window_size,
        alert_threshold=alert_threshold,
        warning_threshold=warning_threshold,
    )

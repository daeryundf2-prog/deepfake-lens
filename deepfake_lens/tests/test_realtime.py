"""Tests for the realtime detection module."""

from __future__ import annotations

import unittest

from deepfake_lens.realtime import (
    RealtimeAlert,
    RealtimeDetector,
    RealtimeState,
    create_realtime_detector,
)


class RealtimeDetectorTest(unittest.TestCase):
    """Test cases for realtime detector functions."""

    def test_create_detector(self) -> None:
        """create_realtime_detector should return a RealtimeDetector."""
        detector = create_realtime_detector()
        self.assertIsInstance(detector, RealtimeDetector)

    def test_create_detector_custom_params(self) -> None:
        """create_realtime_detector should accept custom parameters."""
        detector = create_realtime_detector(
            window_size=10,
            alert_threshold=80,
            warning_threshold=40,
        )
        self.assertEqual(detector.window_size, 10)
        self.assertEqual(detector.alert_threshold, 80)
        self.assertEqual(detector.warning_threshold, 40)

    def test_process_frame_returns_state(self) -> None:
        """process_frame should return a RealtimeState."""
        detector = create_realtime_detector()
        state = detector.process_frame(50)
        self.assertIsInstance(state, RealtimeState)
        self.assertEqual(state.current_score, 50)
        self.assertEqual(state.frame_count, 1)

    def test_process_multiple_frames(self) -> None:
        """Processing multiple frames should update state."""
        detector = create_realtime_detector()
        detector.process_frame(20)
        detector.process_frame(30)
        state = detector.process_frame(40)
        self.assertEqual(state.frame_count, 3)
        self.assertAlmostEqual(state.average_score, 30.0)

    def test_alert_triggered(self) -> None:
        """High scores should trigger alerts."""
        detector = create_realtime_detector(alert_threshold=67)
        for _ in range(5):
            state = detector.process_frame(80)
        self.assertGreater(len(state.alerts), 0)

    def test_alert_cooldown(self) -> None:
        """Alerts should have cooldown period."""
        detector = create_realtime_detector(alert_threshold=67)
        # Process many high scores quickly
        for _ in range(10):
            detector.process_frame(80)
        # Should not have 10 alerts due to cooldown
        self.assertLess(len(detector.alerts), 10)

    def test_moving_average(self) -> None:
        """Moving average should stabilize scores."""
        detector = create_realtime_detector(window_size=5)
        detector.process_frame(20)
        detector.process_frame(20)
        detector.process_frame(20)
        detector.process_frame(80)
        state = detector.process_frame(80)
        # Average should be between 20 and 80
        self.assertGreater(state.average_score, 20)
        self.assertLess(state.average_score, 80)

    def test_get_summary(self) -> None:
        """get_summary should return statistics."""
        detector = create_realtime_detector()
        detector.process_frame(30)
        detector.process_frame(50)
        detector.process_frame(70)
        summary = detector.get_summary()
        self.assertEqual(summary["frame_count"], 3)
        self.assertEqual(summary["max_score"], 70)
        self.assertEqual(summary["min_score"], 30)
        self.assertAlmostEqual(summary["average_score"], 50.0)

    def test_reset(self) -> None:
        """reset should clear all state."""
        detector = create_realtime_detector()
        detector.process_frame(50)
        detector.process_frame(60)
        detector.reset()
        self.assertEqual(detector.frame_count, 0)
        self.assertEqual(len(detector.scores), 0)
        self.assertEqual(len(detector.alerts), 0)

    def test_state_to_json(self) -> None:
        """RealtimeState to_json should return a dictionary."""
        detector = create_realtime_detector()
        state = detector.process_frame(50)
        data = state.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("current_score", data)
        self.assertIn("average_score", data)
        self.assertIn("band", data)

    def test_alert_to_json(self) -> None:
        """RealtimeAlert to_json should return a dictionary."""
        alert = RealtimeAlert(
            timestamp=1234567890.0,
            score=80,
            band="high",
            message="Test alert",
        )
        data = alert.to_json()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["score"], 80)
        self.assertEqual(data["band"], "high")


if __name__ == "__main__":
    unittest.main()

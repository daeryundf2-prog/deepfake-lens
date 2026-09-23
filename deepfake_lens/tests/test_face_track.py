"""Tests for the face-track temporal consistency module."""

from __future__ import annotations

import unittest
from pathlib import Path

from deepfake_lens.face_track import (
    FaceTrackAnalysis,
    _box_smoothness,
    _consecutive_cosine,
    _landmark_jitter,
    _score,
    analyze_face_track,
)


class FaceTrackAnalysisTest(unittest.TestCase):
    def test_missing_file_is_unavailable(self) -> None:
        result = analyze_face_track(Path("/nonexistent/video.mp4"))
        self.assertFalse(result.available)
        self.assertEqual(result.score, 0)

    def test_unavailable_is_not_clean(self) -> None:
        """Unavailable must not masquerade as a low (clean) measurement."""
        result = analyze_face_track(Path("/nonexistent/video.mp4"))
        self.assertFalse(result.available)
        self.assertTrue(result.limitations)

    def test_to_json_shape(self) -> None:
        result = analyze_face_track(Path("/nonexistent/v.mp4")).to_json()
        for key in ("available", "score", "verdict", "embedding_drift_mean",
                    "landmark_jitter_mean", "box_area_delta_mean", "limitations"):
            self.assertIn(key, result)


class MetricMathTest(unittest.TestCase):
    def test_consecutive_cosine_identical(self) -> None:
        import numpy as np

        embs = np.tile(np.arange(10, dtype=float), (4, 1))
        d = _consecutive_cosine(embs)
        self.assertAlmostEqual(d["mean"], 0.0, places=5)
        self.assertAlmostEqual(d["max"], 0.0, places=5)

    def test_consecutive_cosine_orthogonal(self) -> None:
        import numpy as np

        embs = np.eye(4, 10, dtype=float)
        d = _consecutive_cosine(embs)
        self.assertAlmostEqual(d["mean"], 1.0, places=5)

    def test_landmark_jitter_zero_when_static(self) -> None:
        lms = [[(10, 10), (20, 20), (30, 30)]] * 4
        boxes = [(0, 0, 100, 100)] * 4
        self.assertAlmostEqual(_landmark_jitter(lms, boxes), 0.0)

    def test_landmark_jitter_normalized_by_box(self) -> None:
        # 5px shift on every landmark in a 100px box → ~0.07 diag.
        seq = [[(10, 10), (20, 20)], [(15, 15), (25, 25)]] * 2
        boxes = [(0, 0, 100, 100)] * 4
        j = _landmark_jitter(seq, boxes)
        self.assertGreater(j, 0.04)
        self.assertLess(j, 0.10)

    def test_box_smoothness_stable(self) -> None:
        boxes = [(0, 0, 100, 100)] * 5
        self.assertAlmostEqual(_box_smoothness(boxes), 0.0)

    def test_box_smoothness_detects_jump(self) -> None:
        boxes = [(0, 0, 100, 100), (0, 0, 100, 100), (0, 0, 200, 200)]
        self.assertGreater(_box_smoothness(boxes), 0.5)

    def test_score_bounds(self) -> None:
        self.assertEqual(_score(None, None, None), 0)
        high = _score({"mean": 0.4, "max": 0.7}, 0.2, 0.5)
        self.assertGreaterEqual(high, 60)
        self.assertLessEqual(high, 100)


if __name__ == "__main__":
    unittest.main()

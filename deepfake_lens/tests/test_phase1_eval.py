"""Regression tests for split-aware calibration/evaluation (Phase 1)."""

from __future__ import annotations

import unittest
from pathlib import Path

from deepfake_lens.calibration import calibrate_threshold
from deepfake_lens.evaluate import (
    _case_summary,
    _confusion,
    _per_split_metrics,
    calibrate_dataset,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO_ROOT / "fixtures" / "deepfake-lens-sample"


class CalibrateThresholdFallbackTest(unittest.TestCase):
    """When no threshold meets the target FPR the fallback must stay inside
    the 0-100 score domain and record the shortfall."""

    def test_impossible_fpr_falls_back_to_100_with_marker(self) -> None:
        # A negative sample at the maximum score: no threshold can exclude it.
        scores = [(100, False)]
        profile = calibrate_threshold(scores, target_false_positive_rate=0.05)
        self.assertEqual(profile.threshold, 100)
        self.assertEqual(profile.metrics["target_fpr_met"], 0)

    def test_feasible_target_marks_success(self) -> None:
        scores = [(10, False), (90, True)] * 5
        profile = calibrate_threshold(scores, target_false_positive_rate=0.05)
        self.assertLessEqual(profile.threshold, 100)
        self.assertEqual(profile.metrics["target_fpr_met"], 1)
        self.assertGreaterEqual(float(profile.metrics["recall"]), 0.5)


class SplitAwareCalibrationTest(unittest.TestCase):
    """Threshold fitting must use train-split records when splits exist."""

    def test_fixture_calibration_uses_train_split_only(self) -> None:
        # fixtures/deepfake-lens-sample has explicit train/ and test/ folders.
        payload = calibrate_dataset(FIXTURES, pixel_mode="off")
        scope = payload["calibration_scope"]
        self.assertEqual(scope["policy"], "train-split-only")
        self.assertEqual(scope["records_used"], 2)
        self.assertEqual(scope["records_total"], 4)


class UnanalyzedExclusionTest(unittest.TestCase):
    """Failed/unsupported files must not be scored as confident 'real'."""

    def test_confusion_ignores_unavailable_rows(self) -> None:
        rows = [
            {"label": "real", "predicted": "unavailable", "score": 0},
            {"label": "ai", "predicted": "unavailable", "score": 0},
            {"label": "real", "predicted": "real", "score": 0},
            {"label": "ai", "predicted": "ai", "score": 80},
        ]
        confusion = _confusion(rows)
        self.assertEqual(confusion["true_negative"], 1)
        self.assertEqual(confusion["true_positive"], 1)
        self.assertEqual(confusion["false_positive"], 0)
        self.assertEqual(confusion["false_negative"], 0)

    def test_case_summary_ignores_unavailable_rows(self) -> None:
        rows = [
            {"label": "ai", "predicted": "unavailable", "score": 0, "path": "bad.png"},
            {"label": "ai", "predicted": "real", "score": 3, "path": "missed.png"},
        ]
        summary = _case_summary(rows)
        self.assertEqual([row["path"] for row in summary["false_negatives"]], ["missed.png"])


class PerSplitMetricsTest(unittest.TestCase):
    """per_split appears only for declared splits and excludes unavailable."""

    def test_no_declared_splits_gives_empty_breakdown(self) -> None:
        rows = [{"label": "real", "predicted": "real", "score": 0, "split": "unspecified"}]
        self.assertEqual(_per_split_metrics(rows, threshold=67), {})

    def test_declared_split_reported(self) -> None:
        rows = [
            {"label": "ai", "predicted": "ai", "score": 90, "split": "test"},
            {"label": "real", "predicted": "real", "score": 5, "split": "test"},
            {"label": "real", "predicted": "unavailable", "score": 0, "split": "test"},
        ]
        breakdown = _per_split_metrics(rows, threshold=67)
        self.assertIn("test", breakdown)
        self.assertEqual(breakdown["test"]["samples"], 2)


if __name__ == "__main__":
    unittest.main()

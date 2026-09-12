"""Tests for the isotonic score-calibration layer (R-8c)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deepfake_lens.calibration import (
    MIN_CALIBRATION_SAMPLES,
    ScoreCalibrator,
    _isotonic_knots,
    calibrate_scores,
    load_score_calibrator,
    write_score_calibrator,
)
from deepfake_lens.evaluate import calibrate_dataset

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO_ROOT / "fixtures" / "deepfake-lens-sample"


def _separated_pairs() -> list[tuple[int, bool]]:
    negatives = [(score, False) for score in (5, 10, 12, 15, 18, 20, 25, 30, 35, 40, 45, 50)]
    positives = [(score, True) for score in (55, 58, 60, 65, 68, 70, 75, 78, 80, 85, 90, 95)]
    return negatives + positives


class IsotonicKnotsTest(unittest.TestCase):
    """PAVA correctness on known inputs."""

    def test_violating_block_is_pooled(self) -> None:
        # labels F,F,T,F,T,T over scores 10..60: the 1.0@30 vs 0.0@40
        # violation must pool into a single 0.5 block covering 30-40.
        knots = _isotonic_knots([(10, False), (20, False), (30, True), (40, False), (50, True), (60, True)])
        self.assertEqual(knots, ((10.0, 0.0), (20.0, 0.0), (40.0, 0.5), (50.0, 1.0), (60.0, 1.0)))

    def test_tied_scores_share_one_fitted_value(self) -> None:
        knots = _isotonic_knots([(50, False), (50, True), (50, True)])
        self.assertEqual(knots, ((50.0, 2 / 3),))

    def test_perfectly_separated_gives_two_plateaus(self) -> None:
        knots = _isotonic_knots(_separated_pairs())
        values = [value for _, value in knots]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values[-1], 1.0)


class CalibrateScoresTest(unittest.TestCase):
    def test_mapping_is_monotone_and_transform_matches(self) -> None:
        calibrator = calibrate_scores(_separated_pairs())
        self.assertEqual(calibrator.method, "isotonic-pava")
        self.assertTrue(calibrator.ready)
        values = [value for _, value in calibrator.mapping]
        self.assertEqual(values, sorted(values))

        swept = [calibrator.transform(score)[0] for score in range(0, 101)]
        self.assertEqual(swept, sorted(swept))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in swept))

    def test_transform_reports_empirical_fpr_band(self) -> None:
        calibrator = calibrate_scores(_separated_pairs())
        value, band = calibrator.transform(52)
        self.assertAlmostEqual(value, 0.4)  # linear between (50, 0.0) and (55, 1.0)
        self.assertEqual(band, "low")  # no negatives score >= 52
        self.assertEqual(calibrator.transform(45)[1], "moderate")  # 2/12 negatives >= 45
        self.assertEqual(calibrator.transform(30)[1], "high")  # 5/12 negatives >= 30

    def test_dict_rows_and_labels_are_accepted(self) -> None:
        rows = [{"score": score, "label": label} for score, label in
                [(10, "real"), (20, "real"), (30, "real"), (15, "camera"), (25, "authentic"),
                 (70, "ai"), (80, "fake"), (90, "synthetic"), (75, "deepfake"), (85, "generated")]]
        rows += [{"score": 5, "positive": False}, {"score": 95, "positive": True}] * 6
        rows.append({"score": "not-a-number", "label": "ai"})  # skipped
        calibrator = calibrate_scores(rows)
        self.assertEqual(calibrator.method, "isotonic-pava")
        self.assertEqual(calibrator.samples, 22)
        self.assertEqual(calibrator.positives, 11)
        self.assertEqual(calibrator.negatives, 11)

    def test_insufficient_data_is_honest_not_fabricated(self) -> None:
        calibrator = calibrate_scores([(10, False), (90, True), (20, False)])
        self.assertEqual(calibrator.method, "insufficient-data")
        self.assertFalse(calibrator.ready)
        self.assertEqual(calibrator.transform(50), (None, "uncalibrated"))
        self.assertTrue(any("insufficient" in note for note in calibrator.notes))

    def test_single_class_is_insufficient(self) -> None:
        calibrator = calibrate_scores([(score, True) for score in range(60, 60 + MIN_CALIBRATION_SAMPLES)])
        self.assertEqual(calibrator.method, "insufficient-data")
        self.assertEqual(calibrator.transform(80), (None, "uncalibrated"))


class ScoreCalibratorRoundTripTest(unittest.TestCase):
    def test_save_load_preserves_transform(self) -> None:
        calibrator = calibrate_scores(_separated_pairs(), dataset_fingerprint="abc123")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calibration-profile.json"
            write_score_calibrator(path, calibrator)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["method"], "isotonic-pava")
            self.assertEqual(payload["dataset_fingerprint"], "abc123")
            self.assertIn("mapping", payload)
            loaded = load_score_calibrator(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.mapping, calibrator.mapping)
        self.assertEqual(loaded.fpr_table, calibrator.fpr_table)
        for score in (0, 25, 52, 75, 100):
            self.assertEqual(loaded.transform(score), calibrator.transform(score))

    def test_load_missing_or_wrong_version_returns_none(self) -> None:
        self.assertIsNone(load_score_calibrator(None))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "other.json"
            path.write_text(json.dumps({"version": "other"}), encoding="utf-8")
            self.assertIsNone(load_score_calibrator(path))
            self.assertIsNone(load_score_calibrator(Path(tmp) / "missing.json"))


class DatasetIntegrationTest(unittest.TestCase):
    """calibrate --mapping-out path: honest insufficient-data on the tiny fixture."""

    def test_fixture_dataset_yields_insufficient_data_mapping(self) -> None:
        payload = calibrate_dataset(FIXTURES, pixel_mode="off", include_score_mapping=True)
        self.assertIn("score_calibration", payload)
        mapping = payload["score_calibration"]
        self.assertEqual(mapping["method"], "insufficient-data")
        self.assertEqual(mapping["mapping"], [])
        self.assertTrue(mapping["notes"])
        self.assertIn("dataset_fingerprint", payload["calibration_scope"])

    def test_dataset_fingerprint_is_stable(self) -> None:
        first = calibrate_dataset(FIXTURES, pixel_mode="off")["calibration_scope"]["dataset_fingerprint"]
        second = calibrate_dataset(FIXTURES, pixel_mode="off")["calibration_scope"]["dataset_fingerprint"]
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)


if __name__ == "__main__":
    unittest.main()

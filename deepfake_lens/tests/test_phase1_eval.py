"""Regression tests for split-aware calibration/evaluation (Phase 1)."""

from __future__ import annotations

import ast
import io
import json
import math
import unittest
from contextlib import redirect_stdout
from itertools import permutations, product
from pathlib import Path
from unittest.mock import patch

from experiments import eval_text_detect
from scripts import eval_aide

from deepfake_lens.calibration import auroc, calibrate_threshold
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


class EvaluationScriptMetricsTest(unittest.TestCase):
    def test_known_auroc_and_interpolated_eer(self) -> None:
        cases = [
            ([(0.0, 0), (1.0, 1)], 1.0, 0.0),
            ([(1.0, 0), (0.0, 1)], 0.0, 1.0),
            ([(0.5, 0), (0.5, 1)], 0.5, 0.5),
            ([(1.0, 0), (1.0, 1)], 0.5, 0.5),
            ([(0.0, 0), (0.0, 1), (0.0, 1)], 0.5, 0.5),
            ([(0.0, 0), (0.5, 0), (0.5, 1), (1.0, 1)], 0.875, 0.25),
            ([(0.0, 0), (0.5, 0), (0.5, 1), (0.5, 1), (1.0, 1)], 5 / 6, 2 / 7),
        ]
        for pairs, expected_auc, expected_eer in cases:
            for ordered in set(permutations(pairs)):
                with self.subTest(pairs=ordered):
                    scores, labels = map(list, zip(*ordered))
                    self.assertAlmostEqual(eval_aide._auroc(list(ordered)), expected_auc)
                    self.assertAlmostEqual(eval_text_detect._auroc(labels, scores), expected_auc)
                    self.assertAlmostEqual(eval_aide._eer(list(ordered)), expected_eer)
                    self.assertAlmostEqual(eval_text_detect._eer(labels, scores), expected_eer)

    def test_exhaustive_small_auroc_matches_pairwise_and_roc_area(self) -> None:
        for ordered in product(product((0.0, 0.5, 1.0), (0, 1)), repeat=4):
            pairs = list(ordered)
            if len({label for _, label in pairs}) < 2:
                continue
            points = list(eval_aide._sweep(pairs))
            area = sum((right[1] - left[1]) * (2 - left[2] - right[2]) / 2
                       for left, right in zip(points, points[1:]))
            self.assertAlmostEqual(eval_aide._auroc(pairs), area)
            self.assertAlmostEqual(auroc(pairs), area)
            self.assertAlmostEqual(eval_aide._eer(pairs), eval_aide._eer(list(reversed(pairs))))

    def test_sweep_groups_ties_and_includes_endpoints(self) -> None:
        pairs = [(0.5, 0), (0.5, 1), (1.0, 1)]
        self.assertEqual(list(eval_aide._sweep(pairs)), [
            (math.nextafter(1.0, math.inf), 0.0, 1.0),
            (1.0, 0.0, 0.5),
            (0.5, 1.0, 0.0),
        ])
        self.assertEqual(list(eval_aide._sweep([])), [])

    def test_target_fpr_never_overshoots_and_maximizes_recall(self) -> None:
        for ordered in product(product((0.0, 0.5, 1.0), (0, 1)), repeat=4):
            pairs = list(ordered)
            negatives = sum(label == 0 for _, label in pairs)
            for target in (0.0, 0.24, 0.25, 0.49, 0.5, 0.75, 1.0):
                threshold = eval_aide._threshold_at_fpr(pairs, target)
                if not negatives:
                    self.assertIsNone(threshold)
                    continue
                fp = sum(score >= threshold and label == 0 for score, label in pairs)
                self.assertLessEqual(fp / negatives, target)
                candidates = [t for t, fpr, _ in eval_aide._sweep(pairs) if fpr <= target]
                self.assertEqual(threshold, min(candidates))
                tp = sum(score >= threshold and label == 1 for score, label in pairs)
                self.assertEqual(tp, max(sum(score >= t and label == 1 for score, label in pairs)
                                         for t in candidates))

    def test_threshold_boundary_and_json_precision(self) -> None:
        cases = [
            ([(1.0, 0), (1.0, 1)], 0.0, math.nextafter(1.0, math.inf)),
            ([(0.0, 0)], 0.0, math.nextafter(0.0, math.inf)),
            ([(0.123456, 0), (0.123457, 1)], 0.0, 0.123457),
            ([(0.0, 0), (0.5, 0), (1.0, 1)], 0.49, 1.0),
            ([(0.0, 0), (0.5, 0), (1.0, 1)], 0.5, 0.5),
            ([(0.0, 0), (0.5, 0), (1.0, 1)], 1.0, 0.0),
        ]
        for pairs, target, expected in cases:
            with self.subTest(pairs=pairs, target=target):
                report = json.loads(json.dumps(eval_aide._metrics_report(pairs, target), allow_nan=False))
                threshold = report["threshold_at_target_fpr"]
                self.assertEqual(threshold, expected)
                labels = [label for _, label in pairs]
                scores = [score for score, _ in pairs]
                confusion = eval_text_detect._confusion(labels, scores, threshold)
                self.assertEqual(report["confusion_at_threshold"], {k: confusion[k] for k in ("tp", "fp", "fn", "tn")})
                self.assertLessEqual(confusion["fpr"], target)
        self.assertEqual(eval_text_detect._confusion([0, 1], [0.5, 0.5], 0.5)["fp"], 1)

    def test_undefined_metrics_are_null_with_reasons(self) -> None:
        for pairs, reason in [([], "empty_input"), ([(0.5, 0)], "no_positive_samples"),
                              ([(0.5, 1)], "no_negative_samples")]:
            with self.subTest(reason=reason):
                rows = [{"label": "ai" if label else "human", "score": score} for score, label in pairs]
                with redirect_stdout(io.StringIO()):
                    text_report = eval_text_detect._report("test", rows, 50.0)
                aide_report = eval_aide._metrics_report(pairs, 0.0)
                for report in (text_report, aide_report):
                    decoded = json.loads(json.dumps(report, allow_nan=False))
                    for key in ("auroc", "eer"):
                        self.assertIsNone(decoded[key])
                        self.assertEqual(decoded[key + "_reason"], reason)
                if reason == "no_positive_samples":
                    self.assertEqual(aide_report["confusion_at_threshold"]["fp"], 0)
                else:
                    self.assertIsNone(aide_report["threshold_at_target_fpr"])
                    self.assertIsNone(aide_report["confusion_at_threshold"])
                    self.assertEqual(aide_report["threshold_at_target_fpr_reason"], reason)

    def test_invalid_targets_rejected_even_without_samples(self) -> None:
        for target in (-0.01, 1.01, math.nan, math.inf, -math.inf):
            for pairs in ([], [(0.5, 0)], [(0.5, 1)]):
                with self.subTest(target=target, pairs=pairs):
                    with self.assertRaises(ValueError):
                        eval_aide._metrics_report(pairs, target)

    def test_invalid_scores_labels_and_lengths_rejected(self) -> None:
        for pairs in ([(math.nan, 0), (1.0, 1)], [(math.inf, 0)], [(0.5, 2)]):
            with self.subTest(pairs=pairs):
                with self.assertRaises(ValueError):
                    eval_aide._metrics_report(pairs, 0.05)
                labels = [label for _, label in pairs]
                scores = [score for score, _ in pairs]
                for metric in (eval_text_detect._auroc, eval_text_detect._eer):
                    with self.assertRaises(ValueError):
                        metric(labels, scores)
        for metric in (eval_text_detect._auroc, eval_text_detect._eer):
            with self.assertRaises(ValueError):
                metric([0, 1], [0.5])

    def test_cli_rejects_invalid_target_before_model_loading(self) -> None:
        with patch.object(eval_aide, "_load_run_aide") as loader:
            with redirect_stdout(io.StringIO()), patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as caught:
                    eval_aide.main(["--checkpoint", "unused.pth", "--root", "unused", "--target-fpr", "nan"])
            self.assertEqual(caught.exception.code, 2)
            loader.assert_not_called()

    def test_script_has_no_duplicate_functions(self) -> None:
        for module in (eval_aide, eval_text_detect):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
            self.assertEqual(len(names), len(set(names)))


if __name__ == "__main__":
    unittest.main()

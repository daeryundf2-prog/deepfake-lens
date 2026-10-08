"""Bootstrap CIs, raw-score evaluation and in-sample thresholds (WP-I).

G26: every AUROC/recall/FPR carries a 95% CI and n_pos/n_neg, and
evaluation reads raw member scores (contract v2 ``score`` is 0 unless
calibrated). G28: thresholds fitted in-sample are flagged and displayed as
``in-sample(참고)``.
"""

from __future__ import annotations

import contextlib
import io
import json
import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from deepfake_lens.calibration import IN_SAMPLE_LABEL, ThresholdProfile, load_threshold_profile, threshold_display_label
from deepfake_lens.cli_render import _load_thresholds_arg, _print_table, _write_csv
from deepfake_lens.core import _thresholds_json
from deepfake_lens.evaluate import SCORE_BASIS, metrics_with_ci, raw_member_score
from deepfake_lens.evaluation_metrics import (
    bootstrap_ci,
    ci_summary,
    format_ci,
    metric_value,
    metric_with_ci,
)
from deepfake_lens.result_types import (
    BatchScanSummary,
    ClassificationResult,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceStrength,
    ExternalModelAnalysis,
    PixelAnalysis,
    RiskBand,
    SourceGuess,
)

PACKAGED_THRESHOLDS = Path(__file__).resolve().parents[1] / "models" / "thresholds.json"


def _separable(n: int = 120, seed: int = 1, gap: float = 30.0) -> tuple[list[float], list[int]]:
    rng = random.Random(seed)
    scores = [rng.gauss(40 + gap, 10) for _ in range(n)] + [rng.gauss(40, 10) for _ in range(n)]
    return scores, [1] * n + [0] * n


class BootstrapCiTest(unittest.TestCase):
    def test_interval_brackets_point_estimate(self) -> None:
        scores, labels = _separable()
        for metric in ("auroc", "recall_at_fpr", "fpr_at_threshold"):
            with self.subTest(metric):
                value = metric_value(scores, labels, metric)
                ci = bootstrap_ci(scores, labels, metric, n_boot=400)
                self.assertIsNotNone(ci)
                assert ci is not None and value is not None
                self.assertLessEqual(ci[0], ci[1])
                self.assertLessEqual(ci[0], value + 1e-9)
                self.assertGreaterEqual(ci[1], value - 1e-9)
                self.assertTrue(0.0 <= ci[0] <= 1.0 and 0.0 <= ci[1] <= 1.0)

    def test_numpy_and_python_paths_agree_and_are_seeded(self) -> None:
        scores, labels = _separable(60, seed=2)
        for metric in ("auroc", "recall_at_fpr", "fpr_at_threshold"):
            with self.subTest(metric):
                fast = bootstrap_ci(scores, labels, metric, n_boot=300, seed=9)
                slow = bootstrap_ci(scores, labels, metric, n_boot=300, seed=9, use_numpy=False)
                self.assertEqual(fast, slow)
                self.assertEqual(fast, bootstrap_ci(scores, labels, metric, n_boot=300, seed=9))
        self.assertNotEqual(
            bootstrap_ci(scores, labels, "auroc", n_boot=300, seed=1),
            bootstrap_ci(scores, labels, "auroc", n_boot=300, seed=2),
        )

    def test_auroc_matches_reference_and_width_shrinks_with_n(self) -> None:
        from deepfake_lens.evaluation_metrics import auroc

        scores, labels = _separable(80)
        self.assertAlmostEqual(metric_value(scores, labels, "auroc") or 0.0, auroc(list(zip(scores, labels))) or 0.0)
        small = bootstrap_ci(*_separable(25, seed=3, gap=10.0), "auroc", n_boot=500)
        large = bootstrap_ci(*_separable(400, seed=3, gap=10.0), "auroc", n_boot=500)
        assert small is not None and large is not None
        self.assertGreater(small[1] - small[0], large[1] - large[0])

    def test_recall_at_fpr_definition(self) -> None:
        negatives = [float(v) for v in range(100)]  # FPR 1% allows one negative above the cutoff
        positives = [98.5, 99.5, 120.0, 10.0]
        scores = positives + negatives
        labels = [1] * len(positives) + [0] * len(negatives)
        # Cutoff = 2nd largest negative (98): positives strictly above it.
        self.assertEqual(metric_value(scores, labels, "recall_at_fpr", target_fpr=0.01), 0.75)
        self.assertEqual(metric_value(scores, labels, "recall_at_fpr", target_fpr=1.0), 1.0)
        self.assertEqual(metric_value(scores, labels, "fpr_at_threshold", threshold=90), 0.1)

    def test_undefined_and_invalid_inputs(self) -> None:
        self.assertIsNone(bootstrap_ci([1.0, 2.0], [1, 1], "auroc"))
        self.assertIsNone(bootstrap_ci([], [], "fpr_at_threshold"))
        self.assertIsNotNone(bootstrap_ci([10.0, 90.0], [0, 0], "fpr_at_threshold", n_boot=10))
        self.assertIsNone(metric_value([1.0], [0], "auroc"))
        with self.assertRaises(ValueError):
            bootstrap_ci([1.0], [0], "accuracy")
        with self.assertRaises(ValueError):
            metric_value([1.0], [0], "accuracy")
        with self.assertRaises(ValueError):
            bootstrap_ci([1.0], [0, 1], "auroc")
        with self.assertRaises(ValueError):
            bootstrap_ci([1.0, 2.0], [0, 1], "auroc", n_boot=0)
        with self.assertRaises(ValueError):
            bootstrap_ci([1.0, 2.0], [0, 1], "auroc", alpha=1.5)
        with self.assertRaises(ValueError):
            bootstrap_ci([1.0, 2.0], [0, 2], "auroc")

    def test_summary_and_formatting(self) -> None:
        scores, labels = _separable(50)
        summary = ci_summary(scores, labels, threshold=55.0, n_boot=200)
        self.assertEqual((summary["n_pos"], summary["n_neg"]), (50, 50))
        for key in ("auroc_ci", "recall_at_threshold_ci", "fpr_at_threshold_ci", "recall_at_fpr_ci"):
            self.assertEqual(len(summary[key]), 2)  # type: ignore[arg-type]
        self.assertEqual(summary["ci_method"]["n_boot"], 200)  # type: ignore[index]
        row = metric_with_ci(scores, labels, "auroc", n_boot=50)
        self.assertEqual((row["n_pos"], row["n_neg"]), (50, 50))
        self.assertEqual(format_ci(0.9123, [0.88, 0.94]), "0.912 [0.880, 0.940]")
        self.assertEqual(format_ci(None, None), "-")
        self.assertEqual(format_ci(0.5, None, 2), "0.50")
        empty = ci_summary([], [])
        self.assertIsNone(empty["auroc"])
        self.assertIsNone(empty["recall_at_threshold_ci"])


def _result(**fields: object) -> ClassificationResult:
    base = ClassificationResult(
        score=0,
        band=RiskBand.UNKNOWN,
        band_label="판단 불가",
        verdict="판단 불가",
        signals=[],
        limitations=[],
        source_guess=SourceGuess.unknown(),
        next_checks=[],
    )
    return replace(base, **fields)  # type: ignore[arg-type]


def _statistical(raw: int) -> EvidenceItem:
    return EvidenceItem("심층 신호", "d", EvidenceKind.STATISTICAL, EvidenceDirection.SYNTHETIC, EvidenceStrength.WEAK, "face", raw_score=raw)


class RawScoreEvaluationTest(unittest.TestCase):
    def test_raw_member_score_never_reads_result_score(self) -> None:
        model = ExternalModelAnalysis(True, 83, "high", "m", "d")
        self.assertEqual(raw_member_score(_result(score=0, model_analysis=model)), (83, "external_model"))
        unavailable = ExternalModelAnalysis(False, 99, "unknown", "m", "d")
        result = _result(score=0, model_analysis=unavailable, evidence=[_statistical(40), _statistical(61)])
        self.assertEqual(raw_member_score(result), (61, "statistical_evidence"))
        pixel = PixelAnalysis(mode="deep", available=True, raw_score=37, reference_confidence="참고", model="ensemble")
        self.assertEqual(raw_member_score(_result(pixel_analysis=pixel)), (37, "pixel_reference"))
        self.assertEqual(raw_member_score(_result(pixel_analysis=replace(pixel, available=False))), (None, "none"))
        # A legacy/uncalibrated result.score is never used as an eval score.
        self.assertEqual(raw_member_score(_result(score=77)), (None, "none"))

    def test_metrics_with_ci_reports_counts_and_basis(self) -> None:
        pairs = [(80, True)] * 30 + [(20, False)] * 25 + [(60, False)] * 5
        metrics = metrics_with_ci(pairs, 50)
        self.assertEqual(metrics["score_basis"], SCORE_BASIS)
        self.assertEqual(SCORE_BASIS, "raw, uncalibrated")
        self.assertEqual((metrics["n_pos"], metrics["n_neg"]), (30, 30))
        self.assertAlmostEqual(float(metrics["false_positive_rate"]), 5 / 30)  # type: ignore[arg-type]
        for key in ("auroc_ci", "recall_ci", "false_positive_rate_ci", "recall_at_fpr_0_01_ci"):
            self.assertIsInstance(metrics[key], list, key)

    def test_evaluate_dataset_output_names_basis_and_cis(self) -> None:
        from deepfake_lens.evaluate import evaluate_dataset

        fixtures = Path(__file__).resolve().parents[2] / "fixtures" / "deepfake-lens-sample"
        payload = evaluate_dataset(fixtures, pixel_mode="deep")
        self.assertEqual(payload["score_basis"], "raw, uncalibrated")
        self.assertIn("원점수", str(payload["score_basis_note"]))
        metrics = payload["metrics"]
        assert isinstance(metrics, dict)
        for key in ("n_pos", "n_neg", "auroc_ci", "recall_ci", "false_positive_rate_ci", "score_basis"):
            self.assertIn(key, metrics)
        for row in payload["items"]:  # type: ignore[attr-defined]
            self.assertIn(row["score_basis"], {"external_model", "statistical_evidence", "pixel_reference", "none"})
            if row["predicted"] == "unscored":
                self.assertEqual(row["score_basis"], "none")

    def test_benchmark_markdown_has_ci_columns(self) -> None:
        from deepfake_lens.benchmark import write_benchmark_markdown

        payload = {"rows": [{"name": "deep+local", "samples": 4, "n_pos": 2, "n_neg": 2, "accuracy": 0.5, "precision": 0.5,
                             "recall": 0.5, "recall_ci": [0.0, 1.0], "f1": 0.5, "false_positive_rate": 0.5,
                             "false_positive_rate_ci": [0.0, 1.0], "auroc": 0.75, "auroc_ci": [0.25, 1.0]}]}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "b.md"
            write_benchmark_markdown(out, payload)
            text = out.read_text(encoding="utf-8")
        self.assertIn("raw, uncalibrated", text)
        self.assertIn("auroc [95% CI]", text)
        self.assertIn("0.7500 [0.2500, 1.0000]", text)


class InSampleThresholdTest(unittest.TestCase):
    def test_packaged_thresholds_are_flagged_in_sample(self) -> None:
        """G28: the shipped faceswap-seam cutoffs were fitted in-sample."""
        raw = json.loads(PACKAGED_THRESHOLDS.read_text(encoding="utf-8"))
        self.assertIs(raw["in_sample"], True)
        self.assertIn("in-sample", raw["note"])
        profile = load_threshold_profile(PACKAGED_THRESHOLDS)
        assert profile is not None
        self.assertTrue(profile.in_sample)
        self.assertEqual(threshold_display_label(profile), IN_SAMPLE_LABEL)
        payload = _thresholds_json(profile)
        self.assertIs(payload["in_sample"], True)
        self.assertEqual(payload["label"], "in-sample(참고)")
        self.assertTrue(payload["note"])

    def test_labels_for_other_states(self) -> None:
        self.assertEqual(threshold_display_label(None), "내장 기본값(미측정)")
        self.assertEqual(threshold_display_label({"source": "builtin_defaults"}), "내장 기본값(미측정)")
        measured = ThresholdProfile("layer-thresholds-v1", {}, samples=500)
        self.assertEqual(threshold_display_label(measured), "측정됨")
        self.assertEqual(threshold_display_label(ThresholdProfile("layer-thresholds-v1", {}, samples=3)), "잠정(미검증)")
        self.assertIs(_thresholds_json(measured)["in_sample"], False)
        self.assertEqual(load_threshold_profile(None), None)

    def test_cli_render_shows_in_sample(self) -> None:
        profile = load_threshold_profile(PACKAGED_THRESHOLDS)
        summary = BatchScanSummary(total=0, analyzed=0, high=0, medium=0, unknown=0, low=0, unsupported_or_failed=0, capped=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_table(summary, [], include_low=True, thresholds=profile)
        self.assertIn(IN_SAMPLE_LABEL, out.getvalue())
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "o.csv"
            _write_csv(csv_path, [], thresholds=profile)
            self.assertIn("in_sample=true", csv_path.read_text(encoding="utf-8"))
            import argparse

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                loaded = _load_thresholds_arg(argparse.Namespace(thresholds=PACKAGED_THRESHOLDS))
            self.assertTrue(loaded is not None and loaded.in_sample)
            self.assertIn(IN_SAMPLE_LABEL, err.getvalue())


if __name__ == "__main__":
    unittest.main()

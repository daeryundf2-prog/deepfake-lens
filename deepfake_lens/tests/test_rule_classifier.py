"""Tests for the rule-based classifier module (renamed from ml_classifier)."""

from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

from deepfake_lens.rule_classifier import (
    ClassificationResult,
    RuleClassifier,
    load_classifier,
    save_classifier,
    train_rule_classifier,
)


class RuleClassifierTest(unittest.TestCase):
    """Test cases for the rule-based feature classifier."""

    def test_predict_returns_result(self) -> None:
        result = RuleClassifier().predict({})
        self.assertIsInstance(result, ClassificationResult)
        self.assertEqual(result.prediction, "natural")

    def test_low_feature_rules_raise_ai_score(self) -> None:
        result = RuleClassifier().predict(
            {"hist_entropy": 4.0, "texture_variance": 100.0, "std": 10.0}
        )
        self.assertEqual(result.prediction, "ai")
        self.assertIn("low_entropy", result.features_used)
        self.assertIn("low_texture", result.features_used)
        self.assertIn("smooth_image", result.features_used)

    def test_natural_features_stay_natural(self) -> None:
        result = RuleClassifier().predict(
            {"hist_entropy": 7.5, "texture_variance": 3000.0, "std": 80.0}
        )
        self.assertEqual(result.prediction, "natural")
        self.assertEqual(result.probability_ai, 0.0)

    def test_trained_statistics_switch_to_zscore_rules(self) -> None:
        data = [{"hist_entropy": 7.0, "texture_variance": 2000.0}] * 10
        classifier = train_rule_classifier(data)
        self.assertTrue(classifier.feature_means)
        result = classifier.predict({"hist_entropy": 1.0, "texture_variance": 2000.0})
        # std is 0 in the training data, so no z-score fires
        self.assertEqual(result.probability_ai, 0.0)

    def test_zscore_path_fires_on_outlier(self) -> None:
        data = [{"hist_entropy": v} for v in (5.0, 5.5, 6.0, 6.5, 7.0)]
        classifier = train_rule_classifier(data)
        result = classifier.predict({"hist_entropy": 20.0})
        self.assertIn("hist_entropy_zscore", result.features_used)

    def test_save_load_roundtrip(self) -> None:
        data = [{"hist_entropy": 6.0, "std": 40.0}] * 5
        classifier = train_rule_classifier(data)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "clf.json"
            save_classifier(classifier, path)
            loaded = load_classifier(path)
        self.assertEqual(loaded.feature_means, classifier.feature_means)
        self.assertEqual(loaded.threshold, classifier.threshold)

    def test_load_missing_file_returns_fresh_classifier(self) -> None:
        loaded = load_classifier(Path("/nonexistent/clf.json"))
        self.assertIsInstance(loaded, RuleClassifier)
        self.assertFalse(loaded.feature_means)


class DeprecatedShimTest(unittest.TestCase):
    """The old ml_classifier import path must keep working."""

    def test_shim_reexports_same_objects(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from deepfake_lens import ml_classifier

        self.assertIs(ml_classifier.SimpleClassifier, RuleClassifier)
        self.assertIs(ml_classifier.RuleClassifier, RuleClassifier)
        self.assertIs(ml_classifier.train_simple_classifier, train_rule_classifier)
        self.assertIs(ml_classifier.ClassificationResult, ClassificationResult)

    def test_shim_emits_deprecation_warning(self) -> None:
        import importlib
        import sys

        sys.modules.pop("deepfake_lens.ml_classifier", None)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import deepfake_lens.ml_classifier  # noqa: F401

            importlib.reload(sys.modules["deepfake_lens.ml_classifier"])
        self.assertTrue(
            any(issubclass(w.category, DeprecationWarning) for w in caught)
        )


if __name__ == "__main__":
    unittest.main()

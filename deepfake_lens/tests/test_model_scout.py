"""Tests for the model scout module."""

from __future__ import annotations

import unittest

from deepfake_lens.model_scout import (
    DiscoveredModel,
    ScoutDiff,
    ScoutReport,
    scan_for_new_models,
    compare_with_known,
    generate_scout_report,
    generate_detection_hints,
)


class ModelScoutTest(unittest.TestCase):
    """Test cases for model scout functions."""

    def test_scan_for_new_models(self) -> None:
        """scan_for_new_models should return a list of DiscoveredModel."""
        models = scan_for_new_models(["github"])
        self.assertIsInstance(models, list)
        self.assertGreater(len(models), 0)

    def test_discovered_model_dataclass(self) -> None:
        """DiscoveredModel should be a valid dataclass."""
        model = DiscoveredModel(
            name="Test Model",
            source_url="https://example.com",
            category="image",
            subcategory="test",
            provider="TestProvider",
            discovered_date="2026-05-30",
            confidence=0.8,
            evidence=["test evidence"],
            status="new",
            detection_hints=["test hint"],
        )
        self.assertEqual(model.name, "Test Model")
        self.assertEqual(model.category, "image")

    def test_compare_with_known(self) -> None:
        """compare_with_known should return a ScoutDiff."""
        discovered = scan_for_new_models(["github"])
        diff = compare_with_known(discovered)
        self.assertIsInstance(diff, ScoutDiff)
        self.assertIsInstance(diff.new_models, list)
        self.assertIsInstance(diff.deprecated_models, list)

    def test_generate_scout_report(self) -> None:
        """generate_scout_report should return a ScoutReport."""
        discovered = scan_for_new_models(["github"])
        diff = compare_with_known(discovered)
        report = generate_scout_report(diff)
        self.assertIsInstance(report, ScoutReport)
        self.assertIn("new_models", report.summary)
        self.assertIn("total_known", report.summary)

    def test_generate_detection_hints(self) -> None:
        """generate_detection_hints should return hints."""
        model = DiscoveredModel(
            name="Test",
            source_url="https://example.com",
            category="image",
            subcategory="test",
            provider="Google",
            discovered_date="2026-05-30",
            confidence=0.8,
            evidence=[],
            status="new",
            detection_hints=[],
        )
        hints = generate_detection_hints(model)
        self.assertIsInstance(hints, list)
        self.assertGreater(len(hints), 0)

    def test_scout_report_to_json(self) -> None:
        """ScoutReport to_json should return a dictionary."""
        discovered = scan_for_new_models(["github"])
        diff = compare_with_known(discovered)
        report = generate_scout_report(diff)
        data = report.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("report_date", data)
        self.assertIn("summary", data)

    def test_scout_diff_to_json(self) -> None:
        """ScoutDiff to_json should return a dictionary."""
        discovered = scan_for_new_models(["github"])
        diff = compare_with_known(discovered)
        data = diff.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("new_models", data)
        self.assertIn("deprecated_models", data)

    def test_discovered_model_to_json(self) -> None:
        """DiscoveredModel to_json should return a dictionary."""
        model = DiscoveredModel(
            name="Test",
            source_url="https://example.com",
            category="image",
            subcategory="test",
            provider="Test",
            discovered_date="2026-05-30",
            confidence=0.8,
            evidence=[],
            status="new",
            detection_hints=[],
        )
        data = model.to_json()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["name"], "Test")


if __name__ == "__main__":
    unittest.main()

"""Tests for the inpainting detection module."""

from __future__ import annotations

import unittest
from pathlib import Path

from deepfake_lens.inpaint import (
    InpaintAnalysis,
    InpaintRegion,
    analyze_inpainting,
)


class InpaintAnalysisTest(unittest.TestCase):
    """Test cases for inpainting analysis functions."""

    def test_nonexistent_file_returns_error(self) -> None:
        """Analysis of nonexistent file should return error analysis."""
        result = analyze_inpainting(Path("/nonexistent/image.jpg"))
        self.assertEqual(result.score, 0)
        self.assertEqual(result.band, "unknown")
        self.assertIn("존재하지 않습니다", result.verdict)

    def test_unsupported_format_returns_error(self) -> None:
        """Analysis of unsupported format should return error analysis."""
        tmp_path = Path("/tmp") / "test.txt"
        tmp_path.write_bytes(b"not image")
        result = analyze_inpainting(tmp_path)
        self.assertEqual(result.score, 0)
        self.assertIn("지원하지 않는", result.verdict)
        tmp_path.unlink(missing_ok=True)

    def test_analysis_returns_dataclass(self) -> None:
        """Analysis should return an InpaintAnalysis dataclass."""
        result = analyze_inpainting(Path("nonexistent.jpg"))
        self.assertIsInstance(result, InpaintAnalysis)

    def test_to_json_returns_dict(self) -> None:
        """to_json should return a dictionary."""
        result = analyze_inpainting(Path("nonexistent.jpg"))
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        self.assertIn("band", data)
        self.assertIn("verdict", data)
        self.assertIn("regions_detected", data)

    def test_inpaint_region_dataclass(self) -> None:
        """InpaintRegion should be a valid dataclass."""
        region = InpaintRegion(x=10, y=20, width=100, height=100, confidence=0.8, reason="test")
        self.assertEqual(region.x, 10)
        self.assertEqual(region.y, 20)
        self.assertEqual(region.width, 100)
        self.assertEqual(region.height, 100)
        self.assertEqual(region.confidence, 0.8)
        self.assertEqual(region.reason, "test")


if __name__ == "__main__":
    unittest.main()

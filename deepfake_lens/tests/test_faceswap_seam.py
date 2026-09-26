"""Tests for faceswap boundary seam and multi-cue localized manipulation detector."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np

try:
    import cv2
    HAVE_CV2 = True
except ImportError:
    HAVE_CV2 = False

from deepfake_lens.cli import main
from deepfake_lens.core import _deep_image_layers
from deepfake_lens.faceswap_seam import (
    FaceSwapSeamAnalysis,
    analyze_faceswap_seam,
)


class FaceSwapSeamTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_missing_file_returns_graceful_analysis(self) -> None:
        analysis = analyze_faceswap_seam(self.root / "nonexistent.jpg")
        self.assertEqual(analysis.score, 0)
        self.assertEqual(analysis.band, "unknown")
        self.assertTrue(len(analysis.limitations) > 0)

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_blank_image_reports_no_faces(self) -> None:
        # Create solid gray image
        blank = np.full((300, 300, 3), 128, dtype=np.uint8)
        blank_path = self.root / "blank.png"
        cv2.imwrite(str(blank_path), blank)

        analysis = analyze_faceswap_seam(blank_path)
        self.assertEqual(analysis.face_count, 0)
        self.assertEqual(analysis.band, "unknown")
        self.assertEqual(analysis.score, 0)

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_synthetic_faceswap_with_simulated_seam(self) -> None:
        # Generate an image with a drawn synthetic face and artificial boundary seam
        img = np.full((400, 400, 3), 100, dtype=np.uint8)
        # Background noise
        noise = np.random.normal(0, 5, (400, 400, 3)).astype(np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)

        # Draw face ellipse
        center = (200, 180)
        axes = (80, 100)
        cv2.ellipse(img, center, axes, 0, 0, 360, (180, 190, 220), -1)

        # Draw eyes (dark circles)
        cv2.circle(img, (170, 160), 12, (50, 50, 50), -1)
        cv2.circle(img, (230, 160), 12, (50, 50, 50), -1)
        # Pupils / specular highlights
        cv2.circle(img, (172, 158), 3, (255, 255, 255), -1)
        cv2.circle(img, (226, 164), 3, (255, 255, 255), -1)  # Asymmetric highlight

        # Draw nose & mouth
        cv2.line(img, (200, 175), (200, 205), (120, 120, 150), 3)
        cv2.ellipse(img, (200, 230), (25, 10), 0, 0, 180, (80, 80, 180), -1)

        # Introduce high-contrast seam border around ellipse (Poisson artifact)
        cv2.ellipse(img, center, axes, 0, 0, 360, (0, 0, 0), 2)

        test_path = self.root / "synthetic_face.png"
        cv2.imwrite(str(test_path), img)

        analysis = analyze_faceswap_seam(test_path)
        self.assertIsInstance(analysis, FaceSwapSeamAnalysis)
        self.assertIn("score", analysis.to_json())
        self.assertIn("band", analysis.to_json())
        self.assertIn("signals", analysis.to_json())

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_cli_faceswap_seam_json_output(self) -> None:
        img = np.full((200, 200, 3), 150, dtype=np.uint8)
        test_path = self.root / "sample_face.png"
        cv2.imwrite(str(test_path), img)

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["faceswap-seam", str(test_path), "--format", "json"])
        self.assertEqual(code, 0)
        output = json.loads(buf.getvalue())
        self.assertIn("score", output)
        self.assertIn("band", output)
        self.assertIn("verdict", output)

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_core_deep_image_layers_integration(self) -> None:
        img = np.full((200, 200, 3), 150, dtype=np.uint8)
        test_path = self.root / "deep_sample.png"
        cv2.imwrite(str(test_path), img)

        signals, limitations = _deep_image_layers(test_path)
        self.assertIsInstance(signals, list)
        self.assertIsInstance(limitations, list)

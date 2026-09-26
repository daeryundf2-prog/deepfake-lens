"""Tests for air-gapped forensic lab model weight bundler and integrity verifier."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from deepfake_lens.cli import main
from deepfake_lens.vendor_weights import (
    bundle_offline_weights,
    inspect_model_manifest,
    verify_offline_integrity,
)


class VendorWeightsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

        # Create mock models directory
        self.models_dir = self.root / "models"
        self.models_dir.mkdir(parents=True, exist_ok=True)

        # 1. Profile with present weight
        weight_file = self.models_dir / "test_model.pth"
        weight_file.write_bytes(b"model checkpoint byte payload")

        profile_1 = self.models_dir / "test_detector-runtime.json"
        profile_1.write_text(
            json.dumps({
                "model_id": "test_detector",
                "checkpoint": "test_model.pth",
                "modality": "image",
                "engine": "pytorch",
            }),
            encoding="utf-8",
        )

        # 2. Profile with missing weight
        profile_2 = self.models_dir / "missing_detector-runtime.json"
        profile_2.write_text(
            json.dumps({
                "model_id": "missing_detector",
                "checkpoint": "nonexistent.pth",
                "modality": "audio",
                "engine": "pytorch",
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_inspect_model_manifest(self) -> None:
        manifest = inspect_model_manifest(self.models_dir)
        self.assertEqual(manifest.total_profiles, 2)
        self.assertEqual(manifest.available_weights, 1)
        self.assertEqual(manifest.missing_weights, 1)
        self.assertGreater(manifest.total_bytes, 0)

        # Check markdown generation
        md = manifest.to_markdown()
        self.assertIn("포렌식 폐쇄망", md)
        self.assertIn("test_detector", md)
        self.assertIn("missing_detector", md)

    def test_verify_offline_integrity(self) -> None:
        result = verify_offline_integrity(self.models_dir)
        self.assertEqual(result["total_profiles"], 2)
        self.assertEqual(result["available_weights"], 1)
        self.assertEqual(result["missing_weights"], 1)
        self.assertIn("missing_detector", result["missing"])

    def test_bundle_offline_weights(self) -> None:
        bundle_dir = self.root / "offline_bundle"
        manifest_file = bundle_offline_weights(bundle_dir, models_dir=self.models_dir, copy_weights=True)

        self.assertTrue(manifest_file.is_file())
        manifest_json = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertEqual(manifest_json["total_profiles"], 2)
        # Check files were copied
        self.assertTrue((bundle_dir / "test_detector-runtime.json").is_file())
        self.assertTrue((bundle_dir / "test_model.pth").is_file())

    def test_cli_vendor_weights_inspect(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([
                "vendor-weights",
                "--models-dir", str(self.models_dir),
                "--format", "json",
            ])
        self.assertEqual(code, 0)
        output = json.loads(buf.getvalue())
        self.assertEqual(output["total_profiles"], 2)

    def test_cli_vendor_weights_bundle(self) -> None:
        bundle_out = self.root / "cli_bundle"
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([
                "vendor-weights",
                "--models-dir", str(self.models_dir),
                "--bundle-to", str(bundle_out),
            ])
        self.assertEqual(code, 0)
        output = json.loads(buf.getvalue())
        self.assertEqual(output["bundle_dir"], str(bundle_out))
        self.assertTrue((bundle_out / "offline_manifest.json").is_file())

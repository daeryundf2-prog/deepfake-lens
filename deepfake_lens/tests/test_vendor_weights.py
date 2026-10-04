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
    fetch_weights,
    inspect_model_manifest,
    verify_offline_integrity,
    weights_coverage,
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
        # Missing required weights must FAIL loudly for air-gap custody —
        # a "warn" verdict lets an unprovisioned box look deployable.
        self.assertEqual(result["status"], "fail")

    def test_bundle_offline_weights(self) -> None:
        # Only the present-weight profile — a complete, deterministic bundle.
        (self.models_dir / "missing_detector-runtime.json").unlink()
        bundle_dir = self.root / "offline_bundle"
        manifest_file = bundle_offline_weights(bundle_dir, models_dir=self.models_dir, copy_weights=True)

        self.assertTrue(manifest_file.is_file())
        manifest_json = json.loads(manifest_file.read_text(encoding="utf-8"))
        self.assertEqual(manifest_json["total_profiles"], 1)
        # Check files were copied
        self.assertTrue((bundle_dir / "test_detector-runtime.json").is_file())
        self.assertTrue((bundle_dir / "test_model.pth").is_file())
        # Bundle manifests carry relative paths — no source-machine leakage.
        for entry in manifest_json["entries"]:
            self.assertNotIn("checkpoint_abspath", entry)
        self.assertEqual(manifest_json["models_dir"], ".")

    def test_bundle_refuses_incomplete_weights(self) -> None:
        """copy_weights with an absent required checkpoint fails the bundle."""
        with self.assertRaises(SystemExit):
            bundle_offline_weights(
                self.root / "bad_bundle",
                models_dir=self.models_dir,
                copy_weights=True,
            )

    def test_bundle_preserves_nested_checkpoint_paths(self) -> None:
        """Nested checkpoint relpaths must keep their structure so the copied
        runtime profile still resolves inside the bundle."""
        nested = self.models_dir / "checkpoints" / "sub"
        nested.mkdir(parents=True)
        (nested / "deep_model.onnx").write_bytes(b"nested weights")
        (self.models_dir / "nested_detector-runtime.json").write_text(
            json.dumps({"model_id": "nested_detector", "checkpoint": "checkpoints/sub/deep_model.onnx"}),
            encoding="utf-8",
        )

        (self.models_dir / "missing_detector-runtime.json").unlink()
        bundle_dir = self.root / "nested_bundle"
        bundle_offline_weights(bundle_dir, models_dir=self.models_dir, copy_weights=True)

        bundled = bundle_dir / "checkpoints" / "sub" / "deep_model.onnx"
        self.assertTrue(bundled.is_file())
        # The copied profile + bundled weights must resolve against each other.
        profile = json.loads((bundle_dir / "nested_detector-runtime.json").read_text(encoding="utf-8"))
        self.assertTrue((bundle_dir / profile["checkpoint"]).is_file())

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


class FetchAndCoverageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.models_dir = self.root / "models"
        self.models_dir.mkdir(parents=True, exist_ok=True)

    def test_weights_coverage_counts_without_hashing(self) -> None:
        (self.models_dir / "present.pth").write_bytes(b"w")
        (self.models_dir / "a-runtime.json").write_text(
            json.dumps({"name": "a", "checkpoint": "present.pth"}), encoding="utf-8"
        )
        (self.models_dir / "b-runtime.json").write_text(
            json.dumps({"name": "b", "checkpoint": "missing.pth"}), encoding="utf-8"
        )
        (self.models_dir / "rejected-runtime.json").write_text(
            json.dumps({"name": "rej", "supported": False, "checkpoint": "nope.pth"}),
            encoding="utf-8",
        )
        cov = weights_coverage(self.models_dir)
        self.assertEqual(cov["weights_total"], 2)
        self.assertEqual(cov["weights_available"], 1)
        self.assertEqual(cov["weights_missing"], 1)
        self.assertEqual(cov["weights_unsupported"], 1)

    def test_fetch_offline_refuses_network(self) -> None:
        result = fetch_weights(self.models_dir, offline=True)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("offline", result["reason"])
        self.assertEqual(result["failed"], [])

    def test_fetch_skips_profiles_without_url(self) -> None:
        (self.models_dir / "a-runtime.json").write_text(
            json.dumps({"name": "a", "checkpoint": "a.pth"}), encoding="utf-8"
        )
        result = fetch_weights(self.models_dir)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["fetched"], [])
        self.assertEqual(result["failed"], [])

    def test_fetch_verified_download_writes_checkpoint(self) -> None:
        import hashlib
        import unittest.mock as mock

        payload = b"fake weights blob"
        sha = hashlib.sha256(payload).hexdigest()
        (self.models_dir / "net-runtime.json").write_text(
            json.dumps({
                "name": "net",
                "checkpoint": "net.pth",
                "checkpoint_url": "https://example.invalid/net.pth",
                "sha256": sha,
            }),
            encoding="utf-8",
        )

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                data = payload if not hasattr(self, "_done") else b""
                self._done = True
                return data

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse()):
            result = fetch_weights(self.models_dir)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["fetched"], ["net"])
        self.assertEqual((self.models_dir / "net.pth").read_bytes(), payload)

    def test_fetch_sha256_mismatch_writes_nothing(self) -> None:
        import unittest.mock as mock

        (self.models_dir / "bad-runtime.json").write_text(
            json.dumps({
                "name": "bad",
                "checkpoint": "bad.pth",
                "checkpoint_url": "https://example.invalid/bad.pth",
                "sha256": "0" * 64,
            }),
            encoding="utf-8",
        )

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size=-1):
                data = b"tampered" if not hasattr(self, "_done") else b""
                self._done = True
                return data

        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse()):
            result = fetch_weights(self.models_dir)
        self.assertEqual(result["fetched"], [])
        self.assertEqual(len(result["failed"]), 1)
        self.assertIn("sha256 mismatch", result["failed"][0]["error"])
        self.assertFalse((self.models_dir / "bad.pth").exists())

    def test_cli_vendor_weights_fetch_offline(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([
                "vendor-weights",
                "--models-dir", str(self.models_dir),
                "--fetch",
                "--offline",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buf.getvalue())["status"], "skipped")

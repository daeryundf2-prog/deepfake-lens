"""Tests for air-gapped forensic lab model weight bundler and integrity verifier."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from deepfake_lens.cli import main
from deepfake_lens.vendor_weights import (
    bundle_offline_weights,
    fetch_weights,
    inspect_model_manifest,
    pin_profile,
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

    def test_cli_vendor_weights_default_table_is_korean_and_counts_match_rows(self) -> None:
        """G3 (round 5): no "Models Directory"/"Missing: 0"/"[MISSING]"; a disabled or hub
        profile is labelled as such, and the summary counts are the row labels' counts."""
        from deepfake_lens.error_text import english_prose

        (self.models_dir / "disabled_detector-runtime.json").write_text(
            json.dumps({"name": "disabled_detector", "checkpoint": "gone.pth", "supported": False}), encoding="utf-8"
        )
        (self.models_dir / "hub_detector-runtime.json").write_text(
            json.dumps({"name": "hub_detector", "hub_model": "org/model"}), encoding="utf-8"
        )
        for models_dir in (self.models_dir, None):
            buf = io.StringIO()
            with redirect_stdout(buf):
                argv = ["vendor-weights"] + (["--models-dir", str(models_dir)] if models_dir else [])
                self.assertEqual(main(argv), 0)
            text = buf.getvalue()
            for english in ("Models Directory", "Profiles:", "Available:", "Missing:", "MISSING", "[OK"):
                self.assertNotIn(english, text)
            for line in text.splitlines():
                self.assertIsNone(english_prose(line), line)
            rows = [line.strip() for line in text.splitlines() if line.startswith("  [")]
            summary = text.splitlines()[1]
            labels = [row[1:row.index("]")] for row in rows]
            for label in set(labels):
                self.assertIn(f"{label} {labels.count(label)}개", summary)
            if models_dir is not None:
                self.assertIn("  [없음] missing_detector", text)
                self.assertIn("  [비활성 프로필(집계 제외)] disabled_detector", text)
                self.assertIn("  [허브 모델(로컬 파일 없음)] hub_detector", text)
                self.assertIn("없음 1개", summary)

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
        self.assertIn("오프라인", result["reason"])  # R4
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
                "pin": {"sha256": sha},  # G9: the declared hash lives in the pin object
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
                "pin": {"sha256": "0" * 64},  # G9: the declared hash lives in the pin object
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
        self.assertIn("sha256 불일치", result["failed"][0]["error"])  # R4
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


class _FakeResponse:
    """Minimal urlopen() stand-in streaming ``payload`` once."""

    def __init__(self, payload: bytes) -> None:
        self._chunks = [payload]

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self._chunks.pop() if self._chunks else b""


class FetchHardeningTest(unittest.TestCase):
    """G9: https only, no "fetched" without a declared hash, size cap."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.models_dir = Path(self.tmp_dir.name) / "models"
        self.models_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _profile(self, url: str, pin: dict[str, str] | None = None) -> None:
        data: dict[str, object] = {"name": "net", "checkpoint": "net.pth", "checkpoint_url": url}
        if pin is not None:
            data["pin"] = pin
        (self.models_dir / "net-runtime.json").write_text(json.dumps(data), encoding="utf-8")

    def test_download_without_declared_hash_is_unverified_not_fetched(self) -> None:
        import unittest.mock as mock

        self._profile("https://example.invalid/net.pth", pin={"sha256": ""})
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(b"weights")):
            result = fetch_weights(self.models_dir)
        self.assertEqual(result["fetched"], [])
        self.assertEqual(result["unverified"], ["net"])
        self.assertEqual(result["status"], "unverified")
        [entry] = result["results"]
        self.assertEqual(entry["status"], "unverified")
        self.assertNotIn(entry["status"], {"fetched", "verified"})
        # The bytes are kept for the operator to pin, but nothing vouches for them.
        self.assertTrue((self.models_dir / "net.pth").is_file())

    def test_non_https_urls_are_refused(self) -> None:
        import unittest.mock as mock

        for url in ("http://example.invalid/net.pth", "file:///etc/passwd", "ftp://example.invalid/net.pth"):
            with self.subTest(url=url):
                self._profile(url, pin={"sha256": "0" * 64})
                with mock.patch("urllib.request.urlopen") as urlopen:
                    result = fetch_weights(self.models_dir)
                urlopen.assert_not_called()
                self.assertEqual(result["status"], "failed")
                self.assertIn("https", result["failed"][0]["error"])
                self.assertFalse((self.models_dir / "net.pth").exists())

    def test_download_over_size_cap_is_aborted(self) -> None:
        import unittest.mock as mock

        self._profile("https://example.invalid/net.pth", pin={"sha256": "0" * 64})
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(b"x" * 64)):
            result = fetch_weights(self.models_dir, max_bytes=16)
        self.assertEqual(result["status"], "failed")
        self.assertIn("크기 상한", result["failed"][0]["error"])  # R4
        self.assertFalse((self.models_dir / "net.pth").exists())
        self.assertEqual(list(self.models_dir.glob(".fetch-*")), [])

    def test_cli_fetch_unverified_exits_nonzero(self) -> None:
        import unittest.mock as mock

        self._profile("https://example.invalid/net.pth")
        buf = io.StringIO()
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(b"weights")), redirect_stdout(buf):
            code = main(["vendor-weights", "--models-dir", str(self.models_dir), "--fetch"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(buf.getvalue())["status"], "unverified")

    def test_manifest_reads_declared_hash_from_pin(self) -> None:
        payload = b"weights"
        (self.models_dir / "net.pth").write_bytes(payload)
        self._profile("https://example.invalid/net.pth", pin={"sha256": hashlib.sha256(payload).hexdigest()})
        result = verify_offline_integrity(self.models_dir)
        self.assertEqual(result["verified"], 1)
        self._profile("https://example.invalid/net.pth", pin={"sha256": "0" * 64})
        self.assertEqual(verify_offline_integrity(self.models_dir)["status"], "fail")


class PinProfileTest(unittest.TestCase):
    """`deepfake-lens vendor-weights pin <profile>` (G9)."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.models_dir = Path(self.tmp_dir.name) / "models"
        self.models_dir.mkdir()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _write(self, name: str, data: dict[str, object]) -> Path:
        path = self.models_dir / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_cli_pins_local_checkpoint_sha256(self) -> None:
        payload = b"local checkpoint bytes"
        (self.models_dir / "det.pth").write_bytes(payload)
        path = self._write("det-runtime.json", {"name": "det", "runtime": "torchvision", "checkpoint": "det.pth", "pin": {"sha256": ""}})
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["vendor-weights", "pin", "det", "--models-dir", str(self.models_dir)])
        self.assertEqual(code, 0)
        expected = hashlib.sha256(payload).hexdigest()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pin"], {"sha256": expected})
        self.assertEqual(json.loads(buf.getvalue())["pin"]["sha256"], expected)
        # Other profile fields survive the rewrite.
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["checkpoint"], "det.pth")

    def test_pinned_profile_then_loads_and_repin_after_swap(self) -> None:
        """The written pin is what the adapter verifies; a swapped file
        needs a fresh pin (and is refused until then)."""
        import unittest.mock as mock

        from deepfake_lens.model_adapter import analyze_external_model

        checkpoint = self.models_dir / "det.onnx"
        checkpoint.write_bytes(b"v1")
        path = self._write("det-runtime.json", {"name": "det", "runtime": "onnx", "checkpoint": "det.onnx", "modality": "image"})
        pin_profile(path)
        image = Path(self.tmp_dir.name) / "img.png"
        _write_png(image)
        with mock.patch("deepfake_lens.model_adapter._run_onnx", return_value=[0.0]) as run:
            self.assertTrue(analyze_external_model(image, path).available)
            checkpoint.write_bytes(b"v2")
            refused = analyze_external_model(image, path)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(refused.confidence, "failed")
        self.assertTrue(refused.detail.startswith("무결성 불일치"), refused.detail)

    def test_video_frames_pin_hashes_inner_checkpoint(self) -> None:
        (self.models_dir / "inner.onnx").write_bytes(b"inner")
        path = self._write("vf-runtime.json", {"name": "vf", "runtime": "video-frames", "inner": {"runtime": "onnx", "checkpoint": "inner.onnx"}, "pin": {"sha256": ""}})
        result = pin_profile(path)
        self.assertEqual(result["pin"], {"sha256": hashlib.sha256(b"inner").hexdigest()})

    def test_missing_checkpoint_is_an_error_and_writes_nothing(self) -> None:
        path = self._write("det-runtime.json", {"name": "det", "runtime": "onnx", "checkpoint": "absent.onnx", "pin": {"sha256": ""}})
        before = path.read_text(encoding="utf-8")
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["vendor-weights", "pin", str(path)])
        self.assertEqual(code, 1)
        self.assertIn("absent.onnx", err.getvalue())
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_hub_profile_uses_resolver(self) -> None:
        path = self._write("hub-runtime.json", {"name": "hub", "runtime": "hf-image-classifier", "hub_model": "org/model", "pin": {"revision": ""}})
        seen: list[str] = []

        def resolver(model_id: str) -> str:
            seen.append(model_id)
            return "a" * 40

        result = pin_profile(path, hub_resolver=resolver)
        self.assertEqual(seen, ["org/model"])
        self.assertEqual(result["pin"], {"revision": "a" * 40})

    def test_hub_profile_explicit_revision_and_validation(self) -> None:
        path = self._write("hub-runtime.json", {"name": "hub", "runtime": "hf-text-classifier", "hub_model": "org/model"})
        self.assertEqual(pin_profile(path, revision="B" * 40)["pin"], {"revision": "b" * 40})
        with self.assertRaises(ValueError):
            pin_profile(path, revision="main")

    def test_hub_without_huggingface_hub_prints_instructions(self) -> None:
        import unittest.mock as mock

        path = self._write("hub-runtime.json", {"name": "hub", "runtime": "hf-audio-classifier", "hub_model": "org/model", "pin": {"revision": ""}})
        before = path.read_text(encoding="utf-8")
        err = io.StringIO()
        with mock.patch.dict("sys.modules", {"huggingface_hub": None}), redirect_stderr(err):
            code = main(["vendor-weights", "pin", "hub-runtime.json", "--models-dir", str(self.models_dir)])
        self.assertEqual(code, 1)
        self.assertIn("huggingface_hub", err.getvalue())
        self.assertIn("--revision", err.getvalue())
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_pin_without_profile_argument_is_usage_error(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["vendor-weights", "pin"])
        self.assertEqual(code, 2)


def _write_png(path: Path, size: int = 128) -> None:
    from PIL import Image

    Image.new("RGB", (size, size), (120, 80, 40)).save(path)

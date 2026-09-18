from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from deepfake_lens.cli import DEFAULT_ENGINE_PROFILE, default_model_path, main as cli_main
from deepfake_lens.core import analyze_file
from deepfake_lens.model_adapter import analyze_external_model, load_model_threshold

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPO_ROOT / "models" / "aide-runtime.json"
CHECKPOINT_PATH = PROFILE_PATH.parent / "aide_progan_train.pth"


def _checkpoint_downloaded() -> bool:
    """True when a real checkpoint is present; unavailable-path tests skip then."""
    return CHECKPOINT_PATH.exists()


def _load_fetch_aide():
    spec = importlib.util.spec_from_file_location("fetch_aide", REPO_ROOT / "scripts" / "fetch_aide.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AideRuntimeProfileTest(unittest.TestCase):
    """models/aide-runtime.json must satisfy the model_adapter contract."""

    def test_committed_profile_matches_adapter_contract(self) -> None:
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

        self.assertEqual(profile["type"], "deepfake-lens-runtime-profile-v1")
        self.assertEqual(profile["runtime"], "aide")
        self.assertEqual(profile["input_size"], 256)
        self.assertEqual(profile["mean"], [0.485, 0.456, 0.406])
        self.assertEqual(profile["std"], [0.229, 0.224, 0.225])
        self.assertEqual(profile["score_index"], 1)
        self.assertEqual(profile["score_activation"], "softmax")
        # Relative checkpoint paths resolve against the profile directory.
        self.assertEqual((PROFILE_PATH.parent / profile["checkpoint"]).name, "aide_progan_train.pth")

    def test_load_model_threshold_reads_profile(self) -> None:
        self.assertEqual(load_model_threshold(PROFILE_PATH), 67)

    def test_missing_checkpoint_is_graceful(self) -> None:
        """Absent weights must produce available=False with a reason, not a crash."""
        if _checkpoint_downloaded():
            self.skipTest("checkpoint is present; unavailable-path assertion does not apply")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "sample.png"
            _write_rgb_png(image, 8, 8, lambda x, y: (255, 255, 255))

            analysis = analyze_external_model(image, PROFILE_PATH)

            self.assertIsNotNone(analysis)
            self.assertFalse(analysis.available)
            self.assertEqual(analysis.score, 0)
            self.assertIn("checkpoint was not found", analysis.detail)
            self.assertIn("AIDE", analysis.model)
            self.assertTrue(any("fetch_aide" in item for item in analysis.limitations))

            item = analyze_file(image, root=root, model_path=PROFILE_PATH)
            self.assertEqual(item.status, "analyzed")
            self.assertIsNotNone(item.result.model_analysis)
            self.assertFalse(item.result.model_analysis.available)
            # Profile limitations stay first-class on the merged result.
            self.assertTrue(any("fetch_aide" in item for item in item.result.limitations))
            # The external signal is distinct from pixel analysis in the JSON.
            payload = item.to_json()
            self.assertIn("model_analysis", payload["result"])
            self.assertIsNone(payload["result"]["pixel_analysis"])

    def test_copied_profile_resolves_checkpoint_relative_to_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile_copy = root / "aide-runtime.json"
            profile_copy.write_text(PROFILE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            image = root / "sample.png"
            _write_rgb_png(image, 8, 8, lambda x, y: (10, 20, 30))

            analysis = analyze_external_model(image, profile_copy)

            self.assertFalse(analysis.available)
            self.assertIn(str(root / "aide_progan_train.pth"), analysis.detail)


class FetchAideTest(unittest.TestCase):
    """scripts/fetch_aide.py arg parsing, checksum verification, overwrite rules."""

    def test_download_via_file_url_and_verify_sha256(self) -> None:
        fetch = _load_fetch_aide()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "progan_train.pth"
            payload = b"tiny fake checkpoint payload"
            source.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            dest_dir = root / "models"

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = fetch.main(["--url", source.as_uri(), "--dest", str(dest_dir), "--sha256", digest])

            self.assertEqual(rc, 0)
            downloaded = dest_dir / "aide_progan_train.pth"
            self.assertEqual(downloaded.read_bytes(), payload)
            self.assertIn(digest, out.getvalue())
            self.assertIn("license", out.getvalue().lower())

    def test_sha256_mismatch_fails_and_removes_file(self) -> None:
        fetch = _load_fetch_aide()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "progan_train.pth"
            source.write_bytes(b"payload")
            dest_dir = root / "models"

            rc = fetch.main(
                [
                    "--url",
                    source.as_uri(),
                    "--dest",
                    str(dest_dir),
                    "--sha256",
                    "0" * 64,
                ]
            )

            self.assertEqual(rc, 1)
            self.assertFalse((dest_dir / "aide_progan_train.pth").exists())
            self.assertFalse((dest_dir / "aide_progan_train.pth.part").exists())

    def test_download_without_sha256_still_prints_digest(self) -> None:
        fetch = _load_fetch_aide()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "ckpt.bin"
            source.write_bytes(b"weights")

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = fetch.main(["--url", source.as_uri(), "--dest", str(root / "models")])

            self.assertEqual(rc, 0)
            self.assertIn(hashlib.sha256(b"weights").hexdigest(), out.getvalue())
            self.assertIn("no --sha256", out.getvalue())

    def test_refuses_overwrite_without_force(self) -> None:
        fetch = _load_fetch_aide()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "ckpt.bin"
            source.write_bytes(b"new")
            dest_dir = root / "models"
            dest_dir.mkdir()
            existing = dest_dir / "aide_progan_train.pth"
            existing.write_bytes(b"old")

            rc = fetch.main(["--url", source.as_uri(), "--dest", str(dest_dir)])
            self.assertEqual(rc, 2)
            self.assertEqual(existing.read_bytes(), b"old")

            rc = fetch.main(["--url", source.as_uri(), "--dest", str(dest_dir), "--force"])
            self.assertEqual(rc, 0)
            self.assertEqual(existing.read_bytes(), b"new")

    def test_default_folder_url_prints_instructions_without_network(self) -> None:
        fetch = _load_fetch_aide()
        with tempfile.TemporaryDirectory() as tmp:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = fetch.main(["--dest", tmp])

            self.assertEqual(rc, 2)
            self.assertIn("Model Zoo", out.getvalue())
            self.assertIn("uc?id=<FILE_ID>", err.getvalue())
            self.assertFalse((Path(tmp) / "aide_progan_train.pth").exists())

    def test_url_classification(self) -> None:
        fetch = _load_fetch_aide()
        self.assertEqual(
            fetch.classify_url("https://drive.google.com/drive/folders/1qx76UFvDpgCxaPLBCmsA2WY-SSzeJrd4"),
            ("folder", "1qx76UFvDpgCxaPLBCmsA2WY-SSzeJrd4"),
        )
        self.assertEqual(
            fetch.classify_url("https://drive.google.com/file/d/AbC123/view?usp=sharing"),
            ("file", "AbC123"),
        )
        self.assertEqual(
            fetch.classify_url("https://drive.google.com/uc?id=ZzZ789&export=download"),
            ("file", "ZzZ789"),
        )
        kind, _ = fetch.classify_url("https://example.com/checkpoint.pth")
        self.assertEqual(kind, "direct")

    def test_sha256_file_helper(self) -> None:
        fetch = _load_fetch_aide()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blob.bin"
            path.write_bytes(b"abc")
            self.assertEqual(fetch.sha256_file(path), hashlib.sha256(b"abc").hexdigest())


class DefaultEngineDiscoveryTest(unittest.TestCase):
    """CLI auto-discovery of models/aide-runtime.json and clean fallbacks."""

    def test_discovers_committed_profile(self) -> None:
        self.assertEqual(default_model_path(), PROFILE_PATH)

    def test_returns_none_when_models_dir_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(default_model_path(Path(tmp)))

    def test_scan_auto_discovers_profile_and_degrades(self) -> None:
        if _checkpoint_downloaded():
            self.skipTest("checkpoint is present; auto-discovery would run real inference")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_rgb_png(root / "img.png", 8, 8, lambda x, y: (255, 255, 255))

            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["scan", str(root), "--format", "json"])

            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            item = payload["items"][0]
            self.assertIsNotNone(item["result"]["model_analysis"])
            self.assertFalse(item["result"]["model_analysis"]["available"])
            self.assertEqual(payload["summary"]["external_model_active"], 0)

    def test_scan_no_default_engine_matches_heuristic_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_rgb_png(root / "img.png", 8, 8, lambda x, y: (255, 255, 255))

            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["scan", str(root), "--format", "json", "--no-default-engine"])

            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertIsNone(payload["items"][0]["result"]["model_analysis"])

    def test_scan_falls_back_when_profile_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_rgb_png(root / "img.png", 8, 8, lambda x, y: (255, 255, 255))

            out = io.StringIO()
            with mock.patch("deepfake_lens.cli.default_model_path", return_value=None):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    rc = cli_main(["scan", str(root), "--format", "json"])

            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertIsNone(payload["items"][0]["result"]["model_analysis"])

    def test_explicit_model_path_wins_over_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_rgb_png(root / "img.png", 8, 8, lambda x, y: (255, 255, 255))
            sidecar_profile = root / "external.json"
            sidecar_profile.write_text(json.dumps({"type": "score-sidecar-v1", "name": "explicit fixture"}), encoding="utf-8")

            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["scan", str(root), "--format", "json", "--model-path", str(sidecar_profile)])

            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            analysis = payload["items"][0]["result"]["model_analysis"]
            self.assertEqual(analysis["model"], "explicit fixture")


def _write_rgb_png(path: Path, width: int, height: int, pixel_at) -> None:
    rows = []
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            row.extend(pixel_at(x, y))
        rows.append(bytes(row))
    compressed = zlib.compress(b"".join(rows))
    ihdr = _chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + ihdr + _chunk(b"IDAT", compressed) + _chunk(b"IEND", b""))


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + kind + payload + b"\x00\x00\x00\x00"


if __name__ == "__main__":
    unittest.main()

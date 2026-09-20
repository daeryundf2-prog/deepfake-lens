"""doctor diagnostics: profile checks degrade honestly, JSON contract holds."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from deepfake_lens import cli
from deepfake_lens.doctor import _check_profile, format_report, run_diagnostics


class DoctorProfileCheckTest(unittest.TestCase):
    def _write_profile(self, root: Path, profile: dict, name: str = "p.json") -> Path:
        path = root / name
        path.write_text(json.dumps(profile), encoding="utf-8")
        return path

    def test_missing_checkpoint_reports_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = self._write_profile(
                Path(tmp),
                {
                    "type": "deepfake-lens-runtime-profile-v1",
                    "name": "test-model",
                    "runtime": "torchvision",
                    "checkpoint": "absent.pth",
                },
            )
            check = _check_profile(profile)
        self.assertEqual(check.status, "missing")
        self.assertIn("absent.pth", check.detail)

    def test_present_checkpoint_reports_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "model.pth").write_bytes(b"weights")
            profile = self._write_profile(
                Path(tmp),
                {
                    "type": "deepfake-lens-runtime-profile-v1",
                    "name": "test-model",
                    "runtime": "torchvision",
                    "checkpoint": "model.pth",
                },
            )
            check = _check_profile(profile)
        self.assertEqual(check.status, "ok")
        self.assertIn("model.pth", check.detail)

    def test_sha256_mismatch_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "model.pth").write_bytes(b"weights")
            profile = self._write_profile(
                Path(tmp),
                {
                    "type": "deepfake-lens-runtime-profile-v1",
                    "name": "test-model",
                    "runtime": "torchvision",
                    "checkpoint": "model.pth",
                    "sha256": "0" * 64,
                },
            )
            check = _check_profile(profile)
        self.assertEqual(check.status, "warn")
        self.assertIn("sha256", check.detail)

    def test_unsupported_profile_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = self._write_profile(
                Path(tmp),
                {"type": "deepfake-lens-runtime-profile-v1", "runtime": "onnx", "supported": False},
            )
            check = _check_profile(profile)
        self.assertEqual(check.status, "warn")
        self.assertIn("supported:false", check.detail)

    def test_hub_profile_needs_no_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = self._write_profile(
                Path(tmp),
                {
                    "type": "deepfake-lens-runtime-profile-v1",
                    "name": "hub-model",
                    "runtime": "hf-image-classifier",
                    "hub_model": "org/model",
                },
            )
            check = _check_profile(profile)
        self.assertEqual(check.status, "ok")
        self.assertIn("org/model", check.detail)


class DoctorCliTest(unittest.TestCase):
    def test_doctor_json_output_parses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "doctor.json"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cli.main(["doctor", "--format", "json", "--json-out", str(out)])
            self.assertEqual(rc, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
        for section in ("profiles", "accelerators", "dependencies", "tools"):
            self.assertIn(section, payload)
        self.assertTrue(all("status" in check for check in payload["profiles"]))

    def test_doctor_table_runs(self) -> None:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["doctor"])
        self.assertEqual(rc, 0)
        self.assertIn("Model profiles", buf.getvalue())

    def test_report_format_covers_all_sections(self) -> None:
        report = run_diagnostics()
        text = format_report(report)
        for section in ("Model profiles", "Accelerators", "Dependencies", "External tools"):
            self.assertIn(section, text)


if __name__ == "__main__":
    unittest.main()

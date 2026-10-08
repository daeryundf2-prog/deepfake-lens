"""doctor diagnostics: profile checks degrade honestly, JSON contract holds."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deepfake_lens import cli, doctor
from deepfake_lens.doctor import _check_profile, format_report, run_diagnostics


def _all_imports_ok():
    """Pretend every runtime module imports (the test venv has no torch)."""
    return mock.patch.object(doctor, "_module_importable", lambda name, cache: None)


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
        # G29: OK now needs all three columns — a pinned sha256 that matches,
        # importable runtime modules, and the checkpoint on disk.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "model.pth").write_bytes(b"weights")
            profile = self._write_profile(
                Path(tmp),
                {
                    "type": "deepfake-lens-runtime-profile-v1",
                    "name": "test-model",
                    "runtime": "torchvision",
                    "checkpoint": "model.pth",
                    "pin": {"sha256": hashlib.sha256(b"weights").hexdigest()},
                },
            )
            with _all_imports_ok():
                check = _check_profile(profile)
        self.assertEqual(check.status, "ok")
        self.assertIn("model.pth", check.detail)

    def test_present_checkpoint_without_pin_is_not_ok(self) -> None:
        """G29: a checkpoint on disk with no pin is not runnable (the loader refuses it)."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "model.pth").write_bytes(b"weights")
            profile = self._write_profile(
                Path(tmp),
                {"type": "deepfake-lens-runtime-profile-v1", "name": "test-model", "runtime": "torchvision", "checkpoint": "model.pth"},
            )
            with _all_imports_ok():
                check = _check_profile(profile)
        self.assertNotEqual(check.status, "ok")
        self.assertIn("pin", check.detail)

    def test_sha256_mismatch_warns(self) -> None:
        # G9: the declared hash lives in the profile's pin object (a
        # top-level "sha256" is no longer read).
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "model.pth").write_bytes(b"weights")
            profile = self._write_profile(
                Path(tmp),
                {
                    "type": "deepfake-lens-runtime-profile-v1",
                    "name": "test-model",
                    "runtime": "torchvision",
                    "checkpoint": "model.pth",
                    "pin": {"sha256": "0" * 64},
                },
            )
            with _all_imports_ok():
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
                    # G29: a hub profile is OK only when pinned to a commit
                    # and its runtime modules import (no local file needed).
                    "pin": {"revision": "a" * 40},
                },
            )
            with _all_imports_ok():
                check = _check_profile(profile)
        self.assertEqual(check.status, "ok")
        self.assertIn("org/model", check.detail)

    def test_hub_profile_not_ok_without_torch_transformers(self) -> None:
        """G29: hub models are never OK when torch/transformers do not import."""
        missing = {"torch": "torch 미설치", "transformers": "transformers 미설치"}
        with tempfile.TemporaryDirectory() as tmp:
            profile = self._write_profile(
                Path(tmp),
                {
                    "type": "deepfake-lens-runtime-profile-v1",
                    "name": "hub-model",
                    "runtime": "hf-image-classifier",
                    "hub_model": "org/model",
                    "pin": {"revision": "a" * 40},
                },
            )
            with mock.patch.object(doctor, "_module_importable", lambda name, cache: missing.get(name)):
                status = doctor.profile_status(profile)
        self.assertEqual(status.runtime_deps, "missing")
        self.assertFalse(status.runnable)
        self.assertEqual(status.summary_check().status, "missing")
        self.assertIn("torch", status.runtime_deps_detail)

    def test_runtime_dependency_is_actually_imported(self) -> None:
        """The deps column really imports the module and names a missing one."""
        cache: dict[str, str | None] = {}
        self.assertIsNone(doctor._module_importable("json", cache))
        self.assertIn("definitely_not_a_module_xyz", doctor._module_importable("definitely_not_a_module_xyz", cache) or "")

    def test_thresholds_in_sample_label_shown(self) -> None:
        """G28: the packaged in-sample thresholds are labelled in doctor output."""
        from deepfake_lens.calibration import IN_SAMPLE_LABEL

        text = format_report(run_diagnostics())
        self.assertIn(IN_SAMPLE_LABEL, text)


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

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
        self.assertIn("== 모델 프로필 ==", buf.getvalue())  # B7: Korean section headers

    def test_report_format_covers_all_sections(self) -> None:
        report = run_diagnostics()
        text = format_report(report)
        for section in ("모델 프로필", "가속기", "의존성", "외부 도구"):  # B7: Korean section headers
            self.assertIn(f"== {section} ==", text)
        for english in ("Model profiles", "Accelerators", "Dependencies", "External tools", "summary:", " missing,", " warnings"):
            self.assertNotIn(english, text)

    def test_table_uses_display_names(self) -> None:
        """B7: the table names each profile by its Korean display_name; the raw name stays in JSON."""
        report = run_diagnostics()
        text = format_report(report)
        profiles = [status for status in report.model_profiles if status.display_name]
        self.assertTrue(profiles)
        for status in profiles:
            self.assertIn(f"] {status.display_name} ({status.file})", text)
            self.assertNotIn(f"] {status.name}", text)
            self.assertEqual(status.to_json()["display_name"], status.display_name)

    def test_json_stdout_parses_in_a_fresh_process(self) -> None:
        """B7: `doctor --format json` stdout is JSON even when PyMuPDF is installed.

        A fresh interpreter, so PyMuPDF's legacy ``fitz`` import (which
        prints a deprecation line to stdout) is not already cached.
        """
        import os
        import subprocess
        import sys

        repo = Path(__file__).resolve().parents[2]
        env = dict(os.environ, PYTHONPATH=str(repo) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        proc = subprocess.run(
            [sys.executable, "-m", "deepfake_lens", "doctor", "--format", "json"],
            cwd=repo, env=env, capture_output=True, text=True, timeout=300, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        payload = json.loads(proc.stdout)
        self.assertIn("model_profiles", payload)
        names = {check["name"] for check in payload["dependencies"]}
        self.assertIn("PyMuPDF", names)
        self.assertNotIn("deprecated", proc.stdout)

    def test_pymupdf_is_imported_through_pdf_backend(self) -> None:
        """B7: doctor never imports the legacy fitz module directly."""
        from deepfake_lens import doctor as doctor_module

        calls: list[str] = []
        real = doctor_module.importlib.import_module

        def spy(name: str, package: str | None = None) -> object:
            calls.append(name)
            return real(name, package)

        with mock.patch.object(doctor_module.importlib, "import_module", side_effect=spy), \
                mock.patch("deepfake_lens.pdf_backend.import_pymupdf", side_effect=ImportError("no pymupdf")) as backend:
            report = run_diagnostics()
        self.assertNotIn("fitz", calls)
        self.assertNotIn("pymupdf", calls)
        backend.assert_called()
        [pymupdf_check] = [check for check in report.dependencies if check.name == "PyMuPDF"]
        self.assertEqual(pymupdf_check.status, "missing")


if __name__ == "__main__":
    unittest.main()

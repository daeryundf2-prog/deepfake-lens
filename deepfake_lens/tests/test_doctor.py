"""doctor diagnostics: profile checks degrade honestly, JSON contract holds."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
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


class DoctorAssetPinTest(unittest.TestCase):
    """R16-9 (round 16): doctor shows each model asset's pin state (table and JSON) — it used to show none."""

    def test_every_asset_state_is_reported(self) -> None:
        import os

        from deepfake_lens.model_assets import FACE_LANDMARKER, HAAR_FRONTALFACE, PACKAGED_MANIFEST, SYNCNET_WEIGHTS

        bundled = PACKAGED_MANIFEST.parent / HAAR_FRONTALFACE
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            payload = json.loads(PACKAGED_MANIFEST.read_text(encoding="utf-8"))
            for entry in payload["assets"]:
                if entry["name"] == SYNCNET_WEIGHTS:
                    entry["sha256"] = hashlib.sha256(b"syncnet").hexdigest()  # pinned, file absent
            (folder / "assets.json").write_text(json.dumps(payload), encoding="utf-8")
            (folder / FACE_LANDMARKER).write_bytes(b"unpinned task")  # present, no pin
            other = folder / "my_cascade.xml"
            other.write_bytes(bundled.read_bytes() + b" ")  # override with other bytes
            with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_HAAR_CASCADE": str(other)}):
                report = run_diagnostics(folder)
            states = {row["asset"]: row["state"] for row in report.model_assets}
            self.assertEqual(states, {HAAR_FRONTALFACE: "mismatch", FACE_LANDMARKER: "unpinned", SYNCNET_WEIGHTS: "absent", "sfd_face.pth": "unpinned"})
            rows = {row["asset"]: row for row in json.loads(json.dumps(report.to_json()))["model_assets"]}
            self.assertEqual(rows[HAAR_FRONTALFACE]["override"], "DEEPFAKE_LENS_HAAR_CASCADE")
            self.assertIn("로드 거부", str(rows[FACE_LANDMARKER]["detail"]))
            table = format_report(report)
            self.assertIn("== 모델 자산 핀(models/assets.json) ==", table)
            for asset in states:
                self.assertIn(asset, table)
            self.assertIn("sha256 불일치", table)
            report = run_diagnostics(folder)  # no override: the bundled cascade, pinned and matching
        self.assertEqual({row["asset"]: row["state"] for row in report.model_assets}[HAAR_FRONTALFACE], "ok")


class DoctorMatchesTheLoaderTest(unittest.TestCase):
    """R17-9 (round 17): doctor and the loaders read the override and the manifest the same way.

    ``DEEPFAKE_LENS_HAAR_CASCADE=~/x.xml`` was ok in doctor (``~`` expanded)
    and "재정의 cascade 파일이 없습니다" in a scan; a broken or BOM-prefixed
    assets.json left doctor's asset section empty while every face check
    said "미고정 모델".
    """

    def test_a_tilde_override_is_the_same_file_for_doctor_and_the_scan(self) -> None:
        import os

        from deepfake_lens import face
        from deepfake_lens.model_assets import HAAR_FRONTALFACE, PACKAGED_MANIFEST

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            (home / "x.xml").write_bytes((PACKAGED_MANIFEST.parent / HAAR_FRONTALFACE).read_bytes())
            with mock.patch.dict(os.environ, {"HOME": str(home), "DEEPFAKE_LENS_HAAR_CASCADE": "~/x.xml"}):
                self.assertEqual(face.haar_cascade_override(), str(home / "x.xml"))
                self.assertEqual(face.haar_cascade_candidates(), [str(home / "x.xml")])
                row = {r["asset"]: r for r in run_diagnostics().model_assets}[HAAR_FRONTALFACE]
                self.assertEqual((row["state"], row["path"]), ("ok", str(home / "x.xml")))
                if importlib.util.find_spec("cv2") is not None:
                    import cv2

                    if hasattr(cv2, "CascadeClassifier"):
                        self.assertFalse(face.load_face_cascade().empty())  # was CascadeLoadError

    def test_a_broken_manifest_is_shown(self) -> None:
        import os

        from deepfake_lens.model_assets import ASSET_MANIFEST_ERROR, HAAR_FRONTALFACE, KNOWN_ASSETS, expected_sha256

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            (folder / "assets.json").write_text("{broken", encoding="utf-8")
            with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_MODELS_DIR": str(folder), "DEEPFAKE_LENS_HAAR_CASCADE": ""}):
                report = run_diagnostics(folder)
                self.assertIsNone(expected_sha256(HAAR_FRONTALFACE))  # the loaders refuse every asset …
            rows = report.model_assets
            self.assertEqual(rows[0]["state"], ASSET_MANIFEST_ERROR)  # … and doctor says why
            self.assertIn("올바른 JSON이 아닙니다", str(rows[0]["detail"]))
            self.assertEqual(sorted(str(row["asset"]) for row in rows[1:]), sorted(KNOWN_ASSETS))
            self.assertTrue(all(row["state"] == "unpinned" for row in rows[1:]))
            self.assertIn("매니페스트", format_report(report))

    def test_a_bom_manifest_pins_for_the_scan_and_doctor_alike(self) -> None:
        import os

        from deepfake_lens.model_assets import HAAR_FRONTALFACE, PACKAGED_MANIFEST, expected_sha256, manifest_error

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            (folder / "assets.json").write_bytes(b"\xef\xbb\xbf" + PACKAGED_MANIFEST.read_bytes())
            with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_MODELS_DIR": str(folder), "DEEPFAKE_LENS_HAAR_CASCADE": ""}):
                self.assertIsNone(manifest_error())
                self.assertIsNotNone(expected_sha256(HAAR_FRONTALFACE))
                report = run_diagnostics(folder)
        self.assertEqual({row["asset"]: row["state"] for row in report.model_assets}[HAAR_FRONTALFACE], "ok")


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

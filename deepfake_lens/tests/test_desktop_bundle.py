"""DESK-8: the pure parts of ``scripts/build_desktop_bundle.py`` — no network, no pip, no python.org.

The download/install steps (``fetch_embed``, ``build_repo_wheel``, ``download_wheels``, ``build_windows``,
``build_macos``) are not called here; they ran for real when the handover bundles were built.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_desktop_bundle", REPO_ROOT / "scripts" / "build_desktop_bundle.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_desktop_bundle", module)
    spec.loader.exec_module(module)
    return module


bundle = _load()

EMBED_PTH = "python312.zip\r\n.\r\n\r\n# Uncomment to run site.main() automatically\r\n#import site\r\n"


def _metadata(name: str, version: str, *requires: str) -> str:
    lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
    lines += [f"Requires-Dist: {r}" for r in requires]
    return "\n".join(lines) + "\n\nlong description\nRequires-Dist: not-a-header\n"


class PthRewriteTest(unittest.TestCase):
    def test_embeddable_pth_gets_site_packages_and_import_site(self) -> None:
        out = bundle.rewrite_pth(EMBED_PTH)
        lines = out.split("\r\n")
        self.assertEqual(lines[:3], ["python312.zip", ".", "Lib\\site-packages"])
        self.assertIn("import site", lines)
        self.assertNotIn("#import site", lines)
        self.assertTrue(out.endswith("\r\n"))

    def test_rewrite_is_idempotent(self) -> None:
        once = bundle.rewrite_pth(EMBED_PTH)
        self.assertEqual(bundle.rewrite_pth(once), once)
        self.assertEqual(once.count("import site"), 1)
        self.assertEqual(once.count("Lib\\site-packages"), 1)

    def test_pth_without_dot_or_site_line(self) -> None:
        lines = bundle.rewrite_pth("python312.zip\n").split("\r\n")
        self.assertEqual(lines[:4], ["python312.zip", ".", "Lib\\site-packages", "import site"])


class LauncherTextTest(unittest.TestCase):
    def test_windows_launcher_matches_plan(self) -> None:
        text = bundle.windows_launcher_text()
        lines = text.split("\r\n")
        self.assertEqual(lines[0], "@echo off")
        self.assertEqual(lines[1], "chcp 65001 >nul")  # before any Korean line
        self.assertTrue(all(line.isascii() for line in lines[:2]))
        self.assertIn("title Deepfake Lens — 이 창을 닫으면 프로그램이 꺼집니다", lines)
        self.assertIn('cd /d "%~dp0"', lines)
        self.assertIn('"python\\python.exe" -X utf8 -m deepfake_lens desktop %*', lines)
        self.assertIn('"python\\python.exe" -X utf8 -m deepfake_lens desktop --folder "%~1"', lines)
        self.assertIn('if not exist "%~1\\" goto run_plain', lines)  # only a folder becomes --folder
        self.assertNotIn("\n", text.replace("\r\n", ""))  # CRLF only
        self.assertFalse(text.encode("utf-8").startswith(b"\xef\xbb\xbf"))

    def test_doctor_launcher_runs_doctor_and_shows_log_folder(self) -> None:
        text = bundle.doctor_launcher_text()
        self.assertTrue(text.startswith("@echo off\r\nchcp 65001 >nul\r\n"))
        self.assertIn('"python\\python.exe" -X utf8 -m deepfake_lens doctor', text)
        self.assertIn("%USERPROFILE%\\.cache\\deepfake-lens\\logs", text)  # cli_logging.builtin_log_dir on Windows
        self.assertIn("DEEPFAKE_LENS_LOG_DIR", text)
        self.assertTrue(text.rstrip().endswith("pause"))

    def test_log_folder_matches_cli_logging(self) -> None:
        from deepfake_lens.cli_logging import LOG_DIR_ENV

        self.assertIn(LOG_DIR_ENV, bundle.doctor_launcher_text())
        self.assertEqual(bundle.WIN_DOCTOR, "문제 확인.cmd")

    def test_readme_covers_the_plan_points(self) -> None:
        text = bundle.readme_text()
        for needle in ("x64", "C:\\DeepfakeLens", "추가 정보", "실행", "Unblock-File", "차단 해제", "OneDrive", "종료", "%APPDATA%\\deepfake-lens\\report-key", "이 PC의 이 Windows 계정에서만", ".cache\\deepfake-lens\\logs", "문제 확인.cmd"):
            self.assertIn(needle, text)

    def test_version_text(self) -> None:
        text = bundle.version_text("abc123", datetime(2026, 10, 23, 1, 2, 3, tzinfo=timezone.utc), "windows")
        self.assertIn("commit abc123", text)
        self.assertIn("built 2026-10-23T01:02:03Z", text)

    def test_mac_launcher(self) -> None:
        text = bundle.mac_launcher_text("deepfake_lens-0.1.0-py3-none-any.whl", ["numpy==2.5.4", "pymupdf==1.28.2"])
        self.assertTrue(text.startswith("#!/bin/bash\n"))
        self.assertIn("--no-index --find-links wheels", text)
        self.assertIn("'numpy==2.5.4' 'pymupdf==1.28.2'", text)
        self.assertIn('exec .venv/bin/python -X utf8 -m deepfake_lens desktop "$@"', text)
        self.assertIn("for cand in python3.13 python3.12 python3; do", text)


class DependencyCheckTest(unittest.TestCase):
    def _site(self, root: Path, dists: dict[str, str]) -> Path:
        site = root / "site-packages"
        for name, meta in dists.items():
            info = site / f"{name}.dist-info"
            info.mkdir(parents=True)
            (info / "METADATA").write_text(meta, encoding="utf-8")
        return site

    def test_complete_set_passes_and_markers_extras_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = self._site(Path(tmp), {
                "py7zr-1.1.4": _metadata(
                    "py7zr", "1.1.4", "texttable", "brotli>=1.2.0; platform_python_implementation == \"CPython\"",
                    "brotlicffi>=1.2.0.0; platform_python_implementation == \"PyPy\"", "psutil; sys_platform != \"cygwin\"",
                    "backports.zstd>=1.0.0; python_version < \"3.14\"", "pytest; extra == \"test\"",
                ),
                "texttable-1.7.2": _metadata("texttable", "1.7.2"),
                "brotli-1.2.1": _metadata("Brotli", "1.2.1"),
                "psutil-7.2.2": _metadata("psutil", "7.2.2", "pywin32 ; (os_name == \"nt\") and extra == 'dev'"),
                "backports_zstd-1.8.0": _metadata("backports-zstd", "1.8.0"),
            })
            dists = bundle.site_packages_dists(site)
            self.assertEqual(set(dists), {"py7zr", "texttable", "Brotli", "psutil", "backports-zstd"})
            self.assertEqual(bundle.missing_requirements(dists, bundle.marker_environment("windows")), [])

    def test_missing_and_too_old_dists_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = self._site(Path(tmp), {
                "c2pa_python-0.38.0": _metadata("c2pa-python", "0.38.0", "toml>=0.10.2", "cryptography>=41.0.0", "requests>=2.0.0"),
                "toml-0.10.2": _metadata("toml", "0.10.2"),
                "cryptography-40.0.0": _metadata("cryptography", "40.0.0", "cffi>=2.0.0 ; platform_python_implementation != 'PyPy'"),
            })
            problems = bundle.missing_requirements(bundle.site_packages_dists(site), bundle.marker_environment("windows"))
            joined = "\n".join(problems)
            self.assertIn("requests", joined)
            self.assertIn("cffi", joined)  # cryptography's marker applies to CPython
            self.assertIn("40.0.0", joined)  # too old for >=41
            self.assertEqual(len(problems), 3)

    def test_marker_environment_is_windows_cpython_312(self) -> None:
        env = bundle.marker_environment("windows")
        self.assertEqual((env["sys_platform"], env["os_name"], env["python_version"], env["extra"]), ("win32", "nt", "3.12", ""))
        self.assertEqual(bundle.marker_environment("macos", "3.13")["sys_platform"], "darwin")

    def test_wheel_dists_reads_wheel_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / "syhwp-0.0.8-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("syhwp-0.0.8.dist-info/METADATA", _metadata("syhwp", "0.0.8", "olefile>=0.46", "cryptography>=42; extra == 'fast'"))
            dists = bundle.wheel_dists([wheel])
            self.assertEqual(dists["syhwp"][0], "0.0.8")
            problems = bundle.missing_requirements(dists, bundle.marker_environment("macos", "3.12"))
            self.assertEqual(len(problems), 1)
            self.assertIn("olefile", problems[0])

    def test_pinned_set_names_every_top_level_package(self) -> None:
        names = {bundle.canonical(r.split("==")[0]) for r in bundle.WINDOWS_WHEELS}
        for need in ("pymupdf", "numpy", "pillow", "c2pa-python", "py7zr", "syhwp", "olefile"):
            self.assertIn(need, names)
        self.assertTrue(all("==" in r for r in bundle.WINDOWS_WHEELS))
        self.assertEqual(len(names), len(bundle.WINDOWS_WHEELS))


class ZipAndVerifyTest(unittest.TestCase):
    def test_zip_uses_slashes_and_utf8_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "DeepfakeLens-win64"
            (folder / "python").mkdir(parents=True)
            (folder / "python" / "python.exe").write_bytes(b"MZ")
            (folder / "문제 확인.cmd").write_text("@echo off\r\n", encoding="utf-8")
            zip_path = Path(tmp) / "out.zip"
            self.assertEqual(bundle.zip_folder(folder, zip_path), 2)
            with zipfile.ZipFile(zip_path) as archive:
                infos = {info.filename: info for info in archive.infolist()}
            self.assertIn("DeepfakeLens-win64/python/python.exe", infos)
            korean = infos["DeepfakeLens-win64/문제 확인.cmd"]
            self.assertTrue(korean.flag_bits & 0x800)
            self.assertFalse(any("\\" in name for name in infos))

    def test_prune_removes_only_linker_and_debug_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "c2pa" / "libs").mkdir(parents=True)
            for name in ("c2pa_c.dll", "c2pa_c.lib", "c2pa_c.pdb"):
                (site / "c2pa" / "libs" / name).write_bytes(b"x" * 10)
            self.assertEqual(bundle.prune_site_packages(site), (2, 20))
            self.assertEqual([p.name for p in (site / "c2pa" / "libs").iterdir()], ["c2pa_c.dll"])

    def test_verify_reports_an_empty_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            problems = bundle.verify_windows_bundle(Path(tmp))
            joined = "\n".join(problems)
            for needle in ("python/python.exe", "python312._pth", "deepfake_lens/gui.js", "pymupdf/__init__.py", "Deepfake Lens.cmd"):
                self.assertIn(needle, joined)

    def test_verify_checks_pth_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            python = Path(tmp) / "python"
            python.mkdir()
            (python / "python312._pth").write_text(EMBED_PTH, encoding="utf-8")
            joined = "\n".join(bundle.verify_windows_bundle(Path(tmp)))
            self.assertIn("'Lib\\\\site-packages'", joined)
            self.assertIn("'import site'", joined)
            (python / "python312._pth").write_text(bundle.rewrite_pth(EMBED_PTH), encoding="utf-8")
            self.assertNotIn("._pth에", "\n".join(bundle.verify_windows_bundle(Path(tmp))))

    def test_wheel_fits(self) -> None:
        fits = bundle._wheel_fits
        self.assertTrue(fits("numpy-2.5.4-cp312-cp312-win_amd64.whl", "win_amd64", "cp312"))
        self.assertFalse(fits("numpy-2.5.4-cp313-cp313-win_amd64.whl", "win_amd64", "cp312"))
        self.assertTrue(fits("pymupdf-1.28.2-cp310-abi3-win_amd64.whl", "win_amd64", "cp312"))
        self.assertFalse(fits("pymupdf-1.28.2-cp310-abi3-win32.whl", "win_amd64", "cp312"))
        self.assertTrue(fits("brotli-1.2.1-cp313-cp313-macosx_10_13_universal2.whl", "macosx_14_0_arm64", "cp313"))
        self.assertFalse(fits("numpy-2.5.4-cp312-cp312-macosx_14_0_x86_64.whl", "macosx_14_0_arm64", "cp312"))
        self.assertTrue(fits("olefile-0.47-py2.py3-none-any.whl", "win_amd64", "cp312"))


if __name__ == "__main__":
    unittest.main()

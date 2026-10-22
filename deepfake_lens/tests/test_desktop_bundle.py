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
        # RISK B12: pushd (a UNC folder gets a drive letter; `cd /d` failed there) and popd on the way out
        self.assertIn('pushd "%~dp0" || goto no_folder', lines)
        self.assertNotIn("cd /d", text)
        self.assertIn("popd", lines)
        # RISK B11: `desktop %*` passed a dropped file to argparse (English error); now only DFL_FOLDER, quoted
        self.assertNotIn("%*", text)
        self.assertIn('"python\\python.exe" -X utf8 -m deepfake_lens desktop', lines)
        self.assertIn('"python\\python.exe" -X utf8 -m deepfake_lens desktop --folder "%DFL_FOLDER%"', lines)
        self.assertIn('if exist "%~1\\" set "DFL_FOLDER=%~1"', lines)
        self.assertIn('if not defined DFL_FOLDER if exist "%~1" set "DFL_FOLDER=%~dp1"', lines)
        self.assertIn('if "%DFL_FOLDER:~-1%"=="\\" set "DFL_FOLDER=%DFL_FOLDER:~0,-1%"', lines)
        self.assertNotIn("%1", text.replace("%~1", "").replace("%~dp1", ""))  # never unquoted / unexpanded
        self.assertNotIn("\n", text.replace("\r\n", ""))  # CRLF only
        self.assertFalse(text.encode("utf-8").startswith(b"\xef\xbb\xbf"))

    def test_exit_code_is_compared_as_a_string(self) -> None:
        """RISK A6: `if errorlevel 1` is `>= 1` — a native crash's negative code closed the window without a word,
        and Ctrl-C (130) was reported as an error."""
        text = bundle.windows_launcher_text()
        lines = text.split("\r\n")
        self.assertNotIn("if errorlevel", text.lower())
        self.assertIn('set "DFL_CODE=%errorlevel%"', lines)
        self.assertIn('if "%DFL_CODE%"=="0" goto end', lines)
        self.assertIn('if "%DFL_CODE%"=="130" echo 사용자가 중단했습니다.', lines)
        self.assertIn("echo 프로그램이 오류로 끝났습니다(코드 %DFL_CODE%). 위 내용을 캡처해 보내 주십시오.", lines)
        error_at = lines.index("echo 프로그램이 오류로 끝났습니다(코드 %DFL_CODE%). 위 내용을 캡처해 보내 주십시오.")
        self.assertEqual(lines[error_at + 1], "pause")

    def test_start_message_comes_before_python(self) -> None:
        """RISK A7: an empty window during the first slow start made people click again."""
        lines = bundle.windows_launcher_text().split("\r\n")
        message = lines.index("echo 시작하는 중입니다. 처음에는 1분 정도 걸릴 수 있으니 기다려 주십시오...")
        first_python = next(i for i, line in enumerate(lines) if line.startswith('"python\\python.exe"'))
        self.assertLess(message, first_python)

    def test_location_checks_come_before_python(self) -> None:
        for text in (bundle.windows_launcher_text(), bundle.doctor_launcher_text()):
            lines = text.split("\r\n")
            first_python = next(i for i, line in enumerate(lines) if line.startswith('"python\\python.exe"'))
            for needle in ("\\Temp\\", "Rar$", "\\7z", "PROCESSOR_ARCHITEW6432", "OneDrive"):
                at = next(i for i, line in enumerate(lines) if needle in line)
                self.assertLess(at, first_python, needle)
            self.assertIn("압축을 먼저 푸십시오(오른쪽 클릭 → 모두 압축 풀기)", text)
            self.assertIn("x64 Windows 전용", text)
            # a `)` in a folder name ends a parenthesised block early — none in the launchers
            self.assertFalse([line for line in lines if line.rstrip().endswith(")") and line.startswith("if ")])
            self.assertNotIn(" ( ", text)

    def test_doctor_launcher_runs_doctor_and_shows_log_folder(self) -> None:
        text = bundle.doctor_launcher_text()
        self.assertTrue(text.startswith("@echo off\r\nchcp 65001 >nul\r\n"))
        self.assertNotIn("cd /d", text)  # RISK B12
        self.assertIn('"python\\python.exe" -X utf8 -m deepfake_lens doctor', text)
        self.assertIn("%USERPROFILE%\\.cache\\deepfake-lens\\logs", text)  # cli_logging.builtin_log_dir on Windows
        self.assertIn("DEEPFAKE_LENS_LOG_DIR", text)
        # RISK B12: the file now ends in the shared `:end` (popd); the window still waits after the report
        self.assertIn("echo 문제를 알릴 때는 이 창의 내용과 로그 폴더의 파일을 함께 보내 주십시오.\r\npause\r\ngoto end\r\n", text)
        self.assertTrue(text.rstrip().endswith("popd\r\nexit /b %DFL_CODE%"))

    def test_log_folder_matches_cli_logging(self) -> None:
        from deepfake_lens.cli_logging import LOG_DIR_ENV

        self.assertIn(LOG_DIR_ENV, bundle.doctor_launcher_text())
        self.assertEqual(bundle.WIN_DOCTOR, "문제 확인.cmd")

    def test_readme_covers_the_plan_points(self) -> None:
        text = bundle.readme_text()
        for needle in ("x64", "C:\\DeepfakeLens", "추가 정보", "실행", "Unblock-File", "차단 해제", "OneDrive", "종료", "%APPDATA%\\deepfake-lens\\report-key", "이 PC의 이 Windows 계정에서만", ".cache\\deepfake-lens\\logs", "문제 확인.cmd"):
            self.assertIn(needle, text)

    def test_readme_covers_the_risk_review_points(self) -> None:
        text = bundle.readme_text()
        for needle in (
            "SSE4.2", "2010년", "(Y/N)?", "Y를 누르십시오", "스마트 앱 컨트롤(SAC)", "이 PC에서는 쓸 수 없습니다", "AppLocker",
            "IT 담당자", "V3: 환경 설정 → 검사 예외 설정", "DRM", "Edge", "127.0.0.1", "압축을 푼 폴더의 「Deepfake Lens.cmd」",
            "1분", "모두 압축 풀기",
        ):
            self.assertIn(needle, text)
        self.assertNotIn("바탕 화면의 Deepfake Lens", text)  # RISK B13: there is no desktop shortcut
        self.assertTrue(all(len(line) <= 120 for line in text.split("\r\n")))

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

    def test_build_traces_are_removed(self) -> None:
        """RISK B22: `bin/` (shebangs naming the build machine's Python) and direct_url.json (its wheel paths)."""
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp)
            (site / "bin").mkdir()
            (site / "bin" / "normalizer").write_text("#!/Users/x/.venv/bin/python\n", encoding="utf-8")
            for name in ("deepfake_lens-0.1.0.dist-info", "numpy-2.5.4.dist-info"):
                (site / name).mkdir()
                (site / name / "direct_url.json").write_text("{}", encoding="utf-8")
                (site / name / "METADATA").write_text("Name: x\n", encoding="utf-8")
            self.assertEqual(len(bundle.build_traces(site)), 3)
            removed = bundle.remove_build_traces(site)
            self.assertEqual(removed, ["bin/", "deepfake_lens-0.1.0.dist-info/direct_url.json", "numpy-2.5.4.dist-info/direct_url.json"])
            self.assertEqual(bundle.build_traces(site), [])
            self.assertTrue((site / "numpy-2.5.4.dist-info" / "METADATA").is_file())

    def test_verify_names_the_cpp_runtime_traces_and_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "python" / "Lib" / "site-packages"
            (site / "bin").mkdir(parents=True)
            joined = "\n".join(bundle.verify_windows_bundle(Path(tmp)))
            for dll in bundle.MSVC_RUNTIME_DLLS:
                self.assertIn(f"python/{dll} 없음", joined)  # RISK A1
            self.assertIn("site-packages/bin/ 남음", joined)  # RISK B22
            self.assertIn("__pycache__ 없음", joined)  # RISK A7

    def test_pinned_runtime_and_bytecode(self) -> None:
        self.assertEqual(bundle.MSVC_RUNTIME_DLLS, ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll"))
        self.assertRegex(bundle.MSVC_RUNTIME_SHA256, r"^[0-9a-f]{64}$")
        self.assertIn(bundle.MSVC_RUNTIME_VERSION, bundle.MSVC_RUNTIME_WHEEL)
        self.assertTrue(bundle.MSVC_RUNTIME_WHEEL.endswith("-cp312-cp312-win_amd64.whl"))
        if sys.version_info[:2] == (3, 12):
            self.assertEqual(bundle.PYC_MAGIC_312, importlib.util.MAGIC_NUMBER)
        self.assertEqual(bundle.PYC_MAGIC_312, (3531).to_bytes(2, "little") + b"\r\n")

    def test_precompile_skips_only_olefile2(self) -> None:
        import re

        skip = re.compile(bundle.PRECOMPILE_SKIP)
        self.assertTrue(skip.search("/b/python/Lib/site-packages/olefile/olefile2.py"))
        self.assertTrue(skip.search("C:\\b\\python\\Lib\\site-packages\\olefile\\olefile2.py"))
        for kept in ("/s/olefile/olefile.py", "/s/olefile/olefile2.pyc", "/s/x/olefile2.py", "/s/olefile/olefile2.py.bak"):
            self.assertIsNone(skip.search(kept), kept)

    def test_install_msvc_runtime_takes_the_data_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wheel = Path(tmp) / bundle.MSVC_RUNTIME_WHEEL
            prefix = f"msvc_runtime-{bundle.MSVC_RUNTIME_VERSION}.data/data/"
            with zipfile.ZipFile(wheel, "w") as archive:
                for dll in (*bundle.MSVC_RUNTIME_DLLS, "vcomp140.dll"):
                    archive.writestr(prefix + dll, dll.encode())
                    archive.writestr(prefix + "Scripts/" + dll, b"wrong copy")
            python = Path(tmp) / "python"
            python.mkdir()
            self.assertEqual(bundle.install_msvc_runtime(wheel, python), list(bundle.MSVC_RUNTIME_DLLS))
            self.assertEqual(sorted(p.name for p in python.iterdir()), sorted(bundle.MSVC_RUNTIME_DLLS))
            self.assertEqual((python / "msvcp140.dll").read_bytes(), b"msvcp140.dll")


def _pe(imports: list[str], *, pe32: bool = False) -> bytes:
    """A minimal PE image: DOS header, PE signature, COFF header, optional header with 16 data directories, one
    section `.idata` (RVA 0x1000, file offset 0x200) holding the import descriptors and the DLL names."""
    optional_size = (96 if pe32 else 112) + 16 * 8
    out = bytearray(0x200)
    out[0:2] = b"MZ"
    out[0x3C:0x40] = (0x40).to_bytes(4, "little")
    out[0x40:0x44] = b"PE\0\0"
    coff = 0x44
    out[coff:coff + 2] = (0x14C if pe32 else 0x8664).to_bytes(2, "little")
    out[coff + 2:coff + 4] = (1).to_bytes(2, "little")
    out[coff + 16:coff + 18] = optional_size.to_bytes(2, "little")
    optional = coff + 20
    out[optional:optional + 2] = (0x10B if pe32 else 0x20B).to_bytes(2, "little")
    count_at, dirs_at = (92, 96) if pe32 else (108, 112)
    out[optional + count_at:optional + count_at + 4] = (16).to_bytes(4, "little")
    descriptors = 20 * (len(imports) + 1)
    out[optional + dirs_at + 8:optional + dirs_at + 12] = (0x1000).to_bytes(4, "little")
    out[optional + dirs_at + 12:optional + dirs_at + 16] = descriptors.to_bytes(4, "little")
    section = optional + optional_size
    out[section:section + 8] = b".idata\0\0"
    for at, value in ((8, 0x400), (12, 0x1000), (16, 0x400), (20, 0x200)):
        out[section + at:section + at + 4] = value.to_bytes(4, "little")
    body = bytearray(0x400)
    name_at = descriptors
    for index, name in enumerate(imports):
        body[20 * index + 12:20 * index + 16] = (0x1000 + name_at).to_bytes(4, "little")
        body[20 * index + 16:20 * index + 20] = (0x1000 + 0x300).to_bytes(4, "little")  # FirstThunk (unused here)
        body[name_at:name_at + len(name) + 1] = name.encode("ascii") + b"\0"
        name_at += len(name) + 1
    return bytes(out + body)


class PEImportTableTest(unittest.TestCase):
    """RISK A1: PyMuPDF imported MSVCP140.dll, which neither the embeddable Python nor the bundle had."""

    def test_reads_the_import_names_pe32_plus_and_pe32(self) -> None:
        names = ["mupdfcpp64.dll", "python3.dll", "MSVCP140.dll", "KERNEL32.dll", "api-ms-win-crt-heap-l1-1-0.dll"]
        self.assertEqual(bundle.pe_imported_dlls(_pe(names)), names)
        self.assertEqual(bundle.pe_imported_dlls(_pe(names[:2], pe32=True)), names[:2])
        self.assertEqual(bundle.pe_imported_dlls(_pe([])), [])

    def test_rejects_what_is_not_a_pe(self) -> None:
        for data in (b"", b"#!/bin/sh\n", b"MZ" + b"\0" * 0x3A + (0x40).to_bytes(4, "little") + b"ELF\0"):
            with self.assertRaises(bundle.PEFormatError):
                bundle.pe_imported_dlls(data)
        broken = bytearray(_pe(["a.dll"]))
        broken[0x44 + 20:0x44 + 22] = (0x999).to_bytes(2, "little")
        with self.assertRaises(bundle.PEFormatError):
            bundle.pe_imported_dlls(bytes(broken))

    def test_unresolved_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            python = Path(tmp) / "python"
            pymupdf = python / "Lib" / "site-packages" / "pymupdf"
            libs = python / "Lib" / "site-packages" / "numpy.libs"
            pymupdf.mkdir(parents=True)
            libs.mkdir()
            (python / "python3.dll").write_bytes(_pe(["KERNEL32.dll"]))
            (python / "vcruntime140.dll").write_bytes(_pe(["api-ms-win-crt-runtime-l1-1-0.dll"]))
            (libs / "msvcp140-abc.dll").write_bytes(_pe(["kernel32.dll"]))
            (pymupdf / "mupdfcpp64.dll").write_bytes(_pe(["MSVCP140.dll", "VCRUNTIME140.dll"]))
            (pymupdf / "_mupdf.pyd").write_bytes(_pe(["mupdfcpp64.dll", "python3.dll", "MSVCP140.dll", "KERNEL32.dll", "msvcp140-ABC.dll"]))
            (pymupdf / "README.txt").write_text("not a binary", encoding="utf-8")
            problems, checked = bundle.unresolved_imports(python)
            self.assertEqual(checked, 5)
            self.assertEqual(problems, [
                "Lib/site-packages/pymupdf/_mupdf.pyd: MSVCP140.dll을(를) 찾을 수 없음",
                "Lib/site-packages/pymupdf/mupdfcpp64.dll: MSVCP140.dll을(를) 찾을 수 없음",
            ])
            (python / "msvcp140.dll").write_bytes(_pe(["VCRUNTIME140.dll", "api-ms-win-crt-heap-l1-1-0.dll"]))
            self.assertEqual(bundle.unresolved_imports(python), ([], 6))
            (pymupdf / "_extra.pyd").write_bytes(b"MZ not really")
            self.assertIn("PE를 읽지 못함", "\n".join(bundle.unresolved_imports(python)[0]))

    def test_system_dlls(self) -> None:
        for name in ("KERNEL32.dll", "api-ms-win-crt-runtime-l1-1-0.dll", "ucrtbase.dll", "WS2_32.dll", "ext-ms-win-x.dll"):
            self.assertTrue(bundle.is_system_dll(name), name)
        for name in ("MSVCP140.dll", "VCRUNTIME140.dll", "python312.dll", "mupdfcpp64.dll"):
            self.assertFalse(bundle.is_system_dll(name), name)


HANDOVER = Path.home() / "Desktop" / "deepfake-lens-windows-handover" / "desktop" / bundle.WIN_DIR


@unittest.skipUnless((HANDOVER / "python" / "python.exe").is_file(), "no built Windows bundle on this machine")
class BuiltBundleTest(unittest.TestCase):
    """The handover bundle when it is present (built by `scripts/build_desktop_bundle.py --platform windows`)."""

    def test_built_bundle_imports_resolve(self) -> None:
        problems, checked = bundle.unresolved_imports(HANDOVER / "python")
        self.assertGreater(checked, 50)
        self.assertEqual(problems, [])

    def test_built_bundle_passes_verify(self) -> None:
        self.assertEqual(bundle.verify_windows_bundle(HANDOVER), [])


class BundleInfoTest(unittest.TestCase):
    """RISK B10: the bundle has no pip — pip advice there pointed at a command the user cannot run."""

    def test_running_from_bundle_looks_for_a_pth_file(self) -> None:
        from deepfake_lens.bundle_info import running_from_bundle

        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(running_from_bundle(tmp))
            (Path(tmp) / "python312._pth").write_text("python312.zip\r\n.\r\n", encoding="utf-8")
            self.assertTrue(running_from_bundle(tmp))
        self.assertFalse(running_from_bundle())  # the test interpreter is not the embeddable one

    def test_install_hint(self) -> None:
        from deepfake_lens.bundle_info import install_hint

        self.assertEqual(install_hint("pymupdf", bundle=False), "`pip install pymupdf`")
        self.assertEqual(install_hint("pymupdf", bundle=True), "이 기능은 꾸러미에 포함되어 있지 않습니다 — 담당자에게 요청하십시오(pymupdf)")

    def test_bundle_advice_rewrites_only_in_the_bundle(self) -> None:
        from deepfake_lens.bundle_info import bundle_advice
        from deepfake_lens.evidence_statement import PDF_DEPENDENCY_MESSAGE, PDF_REPORT_DEPENDENCY_MESSAGE

        for text in (PDF_DEPENDENCY_MESSAGE, PDF_REPORT_DEPENDENCY_MESSAGE):
            self.assertIn("(설치: `pip install pymupdf`)", text)  # this interpreter is not the bundle
            self.assertEqual(bundle_advice(text, bundle=False), text)
            inside = bundle_advice(text, bundle=True)
            self.assertNotIn("pip", inside)
            self.assertIn("(이 기능은 꾸러미에 포함되어 있지 않습니다 — 담당자에게 요청하십시오(pymupdf)).", inside)
        archive = "7z 해제에는 py7zr이 필요합니다 (`pip install deepfake-lens[archive]`)"
        self.assertEqual(
            bundle_advice(archive, bundle=True),
            "7z 해제에는 py7zr이 필요합니다 (이 기능은 꾸러미에 포함되어 있지 않습니다 — 담당자에게 요청하십시오(deepfake-lens[archive]))",
        )

    def test_bundle_info_writes_nothing(self) -> None:
        source = (REPO_ROOT / "deepfake_lens" / "bundle_info.py").read_text(encoding="utf-8")
        for needle in ("open(", "write_", "mkdir", "unlink", "replace("):
            self.assertNotIn(needle, source.replace("_PIP_ADVICE.sub", ""))


if __name__ == "__main__":
    unittest.main()

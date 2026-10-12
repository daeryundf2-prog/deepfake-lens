"""R19-4 / R19-5 (round 19): tool folders named by variables, and "~user" paths in arguments and requests.

R19-4: since R18-2 an override variable "~nouser/l" was read as the literal
*relative* path, so ``DEEPFAKE_LENS_LOG_DIR`` / ``DEEPFAKE_LENS_HEATMAP_DIR``
made their folders in the working folder (the evidence folder, ``cd case;
scan .``); a relative or absolute value inside the scanned folder was never
checked like the output arguments (P11). Now the value is made absolute, an
unknown "~user" is exit 2, and a folder inside a scanned folder is exit 2
(the web server's scans fall back to the built-in heatmap folder).

R19-5: ``--cache '~nouser/c.json'``, ``--hash-db '~nouser/h.json'`` and
``scan '~nouser/x'`` ended in an English ``RuntimeError: Could not determine
home directory`` traceback (rc 1); ``/api/scan?folder=~nouser/x``,
``/api/analyze-file?file=~nouser/a.jpg`` and ``/api/preview?path=…`` were
HTTP 500. Every user-given path now takes "~nouser" literally, as the shell
does.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest import mock

from deepfake_lens import core, webapp_api
from deepfake_lens.native_path import ToolFolderError, tool_folder_from_env

PACKAGE = Path(__file__).resolve().parents[1]
NO_SUCH_USER = "~dfl-r19-no-such-user"


def _run(args: list[str], cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    base = {key: value for key, value in os.environ.items() if not key.startswith("DEEPFAKE_LENS_")}
    base.update(PYTHONPATH=str(PACKAGE.parent), HOME=str(cwd.parent / "home"))
    base.update(env)  # R20-7: a test may name its own HOME
    return subprocess.run([sys.executable, "-m", "deepfake_lens", *args], cwd=cwd, env=base, capture_output=True, text=True, timeout=300)


class ToolFolderVariableTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.case = Path(self._tmp.name).resolve() / "case"
        self.case.mkdir()
        (self.case / "memo.txt").write_text("사람이 쓴 짧은 메모입니다.", encoding="utf-8")
        self.listing = sorted(os.listdir(self.case))

    def assert_untouched(self) -> None:
        self.assertEqual(sorted(os.listdir(self.case)), self.listing)

    def test_a_relative_log_folder_inside_the_scanned_folder_is_exit_2(self) -> None:
        for value in ("logs", "./nested/logs"):
            with self.subTest(value=value):
                done = _run(["scan", "."], self.case, DEEPFAKE_LENS_LOG_DIR=value)
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn("환경 변수 DEEPFAKE_LENS_LOG_DIR의 폴더가 검사 대상 폴더 안에 있습니다", done.stderr)
                self.assertNotIn("Traceback", done.stderr)
                self.assert_untouched()

    def test_an_unknown_home_in_a_variable_is_exit_2(self) -> None:
        for env in ("DEEPFAKE_LENS_LOG_DIR", "DEEPFAKE_LENS_HEATMAP_DIR"):
            with self.subTest(env=env):
                done = _run(["scan", ".", "--pixel", "deep", "--heatmaps"], self.case, **{env: f"{NO_SUCH_USER}/l"})
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn(f"환경 변수 {env}의 경로를 해석할 수 없습니다", done.stderr)
                self.assert_untouched()

    def test_a_heatmap_folder_inside_the_scanned_folder_is_exit_2(self) -> None:
        done = _run(["scan", str(self.case), "--pixel", "deep", "--heatmaps"], self.case.parent, DEEPFAKE_LENS_HEATMAP_DIR=str(self.case / "hm"))
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("환경 변수 DEEPFAKE_LENS_HEATMAP_DIR의 폴더가 검사 대상 폴더 안에 있습니다", done.stderr)
        self.assert_untouched()
        # Without --heatmaps the variable is not used, and a folder outside is fine.
        self.assertEqual(_run(["scan", str(self.case)], self.case.parent, DEEPFAKE_LENS_HEATMAP_DIR=str(self.case / "hm")).returncode, 0)
        outside = _run(["scan", str(self.case)], self.case.parent, DEEPFAKE_LENS_LOG_DIR=str(self.case.parent / "logs"))
        self.assertEqual(outside.returncode, 0, outside.stderr)
        self.assert_untouched()

    def test_the_variable_is_made_absolute_and_an_unknown_home_raises(self) -> None:
        with mock.patch.dict(os.environ, {"X_R19_DIR": "rel/dir"}):
            self.assertEqual(tool_folder_from_env("X_R19_DIR", Path("/d")), Path(os.path.abspath("rel/dir")))
        with mock.patch.dict(os.environ, {"X_R19_DIR": "  "}):
            self.assertEqual(tool_folder_from_env("X_R19_DIR", Path("/d")), Path("/d"))
        with mock.patch.dict(os.environ, {"X_R19_DIR": f"{NO_SUCH_USER}/x"}), self.assertRaises(ToolFolderError):
            tool_folder_from_env("X_R19_DIR", Path("/d"))

    def test_a_web_scan_never_writes_heatmaps_inside_the_scanned_folder(self) -> None:
        with mock.patch.dict(os.environ, {core.HEATMAP_DIR_ENV: str(self.case / "hm")}):
            target = core._heatmap_path_for(self.case / "a.png", root=self.case, heatmap_dir=None)
            self.assertFalse(str(target).startswith(str(self.case)), target)
            self.assertTrue(core.is_default_heatmap_output(target.with_name("a.png" + core.HEATMAP_SUFFIX)))
        with mock.patch.dict(os.environ, {core.HEATMAP_DIR_ENV: str(self.case.parent / "hm")}):
            target = core._heatmap_path_for(self.case / "a.png", root=self.case, heatmap_dir=None)
            self.assertTrue(str(target).startswith(str(self.case.parent / "hm")), target)


class HomeAndTempToolFoldersTest(unittest.TestCase):
    """R20-7 (round 20): the tool folders HOME and TMPDIR decide were not checked like the variables of R19-4.
    ``cd case; HOME=rel scan case`` wrote ``case/rel/.cache/deepfake-lens/logs/…`` (rc 0), ``TMPDIR=case/tmpd``
    made and removed the session folder inside the evidence (its mtime changed), and ``cd case;
    DEEPFAKE_LENS_LOG_DIR=logs evidence case/a.txt`` made ``case/logs`` (only scanned folders were checked)."""

    setUp = ToolFolderVariableTest.setUp
    assert_untouched = ToolFolderVariableTest.assert_untouched

    def _mtimes(self) -> dict[str, int]:
        return {str(path): path.stat().st_mtime_ns for path in [self.case, *self.case.rglob("*")]}

    def test_a_relative_home_is_exit_2(self) -> None:
        done = _run(["scan", str(self.case)], self.case, HOME="rel")
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("환경 변수 HOME이 절대 경로가 아닙니다: rel", done.stderr)
        self.assert_untouched()
        # The log folder named by its variable needs no HOME.
        named = _run(["scan", str(self.case)], self.case, HOME="rel", DEEPFAKE_LENS_LOG_DIR=str(self.case.parent / "logs"))
        self.assertEqual(named.returncode, 0, named.stderr)
        self.assert_untouched()

    def test_a_home_inside_the_scanned_folder_is_exit_2(self) -> None:
        home = self.case / "home"
        home.mkdir()
        self.listing = sorted(os.listdir(self.case))
        done = _run(["scan", str(self.case), "--recursive"], self.case.parent, HOME=str(home))
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("HOME에서 정해지는 도구 폴더(로그)가 검사 대상 폴더 안에 있습니다", done.stderr)
        self.assertEqual(os.listdir(home), [])
        self.assert_untouched()

    def test_a_temp_folder_inside_the_scanned_folder_is_skipped_with_a_notice(self) -> None:
        import zipfile

        tmpd = self.case / "tmpd"
        tmpd.mkdir()
        with zipfile.ZipFile(self.case / "a.zip", "w") as archive:  # extracted into the session folder
            archive.writestr("m.txt", "메모입니다.")
        self.listing = sorted(os.listdir(self.case))
        for env in ("TMPDIR", "DEEPFAKE_LENS_TMPDIR"):
            with self.subTest(env=env):
                before = self._mtimes()
                done = _run(["scan", str(self.case), "--recursive"], self.case.parent, **{env: str(tmpd)})
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertIn("검사 대상 폴더 안", done.stderr)  # the R15-6 notice names the reason
                self.assertEqual(os.listdir(tmpd), [])
                self.assertEqual(self._mtimes(), before)
                self.assert_untouched()

    def test_a_single_file_commands_log_folder_inside_its_files_folder_is_exit_2(self) -> None:
        for argv in (["evidence", "memo.txt"], ["classify", "memo.txt"]):
            with self.subTest(argv=argv):
                done = _run(argv, self.case, DEEPFAKE_LENS_LOG_DIR="logs")
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn("환경 변수 DEEPFAKE_LENS_LOG_DIR의 폴더가 검사 대상 폴더 안에 있습니다", done.stderr)
                self.assert_untouched()

    def test_a_file_in_the_home_folder_is_exit_2_like_a_scan_of_it(self) -> None:
        """R21-6 (round 21): this test pinned R20-7's documented exception — only scanned folders were checked against
        HOME's tool folders, so ``HOME=/E1 classify /E1/a.png`` wrote ``/E1/.cache/deepfake-lens/logs`` next to the
        evidence (rc 0). The folder of an analyzed file is now examined for the built-in folders as it already was
        for the variables: exit 2 before any work, nothing written; a file in a subfolder of HOME is analyzed."""
        home = Path(self._tmp.name) / "home"
        home.mkdir()
        (home / "note.txt").write_text("메모입니다.", encoding="utf-8")
        for argv in (["classify", str(home / "note.txt")], ["evidence", str(home / "note.txt")]):
            with self.subTest(argv=argv[0]):
                done = _run(argv, self.case.parent, HOME=str(home))
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn("HOME에서 정해지는 도구 폴더(로그)가 검사 대상 폴더 안에 있습니다", done.stderr)
                self.assertEqual(os.listdir(home), ["note.txt"])
        (home / "docs").mkdir()
        (home / "docs" / "note.txt").write_text("메모입니다.", encoding="utf-8")
        done = _run(["classify", str(home / "docs" / "note.txt")], self.case.parent, HOME=str(home))
        self.assertEqual(done.returncode, 0, done.stderr)


class UnknownHomeInArgumentsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.case = self.base / "case"
        self.case.mkdir()
        (self.case / "memo.txt").write_text("사람이 쓴 짧은 메모입니다.", encoding="utf-8")
        self.work = self.base / "work"
        self.work.mkdir()

    def test_cache_and_hash_db_take_the_literal_path(self) -> None:
        for option, name in (("--cache", "c.json"), ("--hash-db", "h.json")):
            with self.subTest(option=option):
                done = _run(["scan", str(self.case), option, f"{NO_SUCH_USER}/{name}", *(["--dedupe"] if option == "--hash-db" else [])], self.work)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertNotIn("Traceback", done.stderr)
                self.assertNotIn("RuntimeError", done.stderr)
                self.assertTrue((self.work / NO_SUCH_USER / name).is_file())

    def test_a_folder_argument_takes_the_literal_path(self) -> None:
        missing = _run(["scan", f"{NO_SUCH_USER}/x"], self.work)
        self.assertEqual(missing.returncode, 2, missing.stderr)
        self.assertIn("폴더를 찾을 수 없습니다", missing.stderr)
        literal = self.work / NO_SUCH_USER / "x"
        literal.mkdir(parents=True)
        (literal / "memo.txt").write_text("메모", encoding="utf-8")
        found = _run(["scan", f"{NO_SUCH_USER}/x"], self.work)
        self.assertEqual(found.returncode, 0, found.stderr)
        self.assertNotIn("Traceback", found.stderr)

    def test_web_requests_are_403_not_500(self) -> None:
        with mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict()):
            webapp_api.configure_read_roots(self.case)
            for call in (
                lambda: webapp_api._scan_payload(f"folder={NO_SUCH_USER}/x", default_folder=self.case),
                lambda: webapp_api._scan_job_start(f"folder={NO_SUCH_USER}/x", default_folder=self.case),
                lambda: webapp_api._analyze_file_payload(f"file={NO_SUCH_USER}/a.jpg"),
                lambda: webapp_api._preview_payload(f"path={NO_SUCH_USER}/a.jpg"),
            ):
                with self.subTest(call=call):
                    try:
                        outcome = call()
                    except webapp_api.ReadRootDenied:
                        continue
                    status = outcome[0] if isinstance(outcome, tuple) else webapp_api.api_status(outcome)
                    self.assertEqual(status, 403, outcome)


if __name__ == "__main__":
    unittest.main()

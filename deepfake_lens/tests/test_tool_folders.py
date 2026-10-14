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

import contextlib
import io
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


RELOCATED = "알림: 도구 폴더(로그·히트맵·오디오 numba 캐시)의 기본 위치"


def _snapshot(folder: Path) -> dict[str, int]:
    """QA-IN-1: every entry under ``folder`` (and the folder itself) with its mtime — a new, removed or touched entry differs."""
    return {str(path.relative_to(folder)): path.lstat().st_mtime_ns for path in [folder, *folder.rglob("*")]}


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

    def test_a_relative_home_relocates_the_tool_folders(self) -> None:
        """R21-10 (round 21, user decision 2026-10-12: relocate instead of refuse): R20-7 pinned exit 2 here
        ("환경 변수 HOME이 절대 경로가 아닙니다: rel"). The tool folders now go to the first place outside the scanned
        folder, with one notice; nothing in the working (evidence) folder changes."""
        before = self._mtimes()
        done = _run(["scan", str(self.case)], self.case, HOME="rel")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr.count(RELOCATED), 1, done.stderr)
        self.assertIn("HOME 「rel」이 절대 경로가 아님", done.stderr)
        self.assertEqual(self._mtimes(), before)
        self.assert_untouched()
        # The log folder named by its variable needs no HOME.
        named = _run(["scan", str(self.case)], self.case, HOME="rel", DEEPFAKE_LENS_LOG_DIR=str(self.case.parent / "logs"))
        self.assertEqual(named.returncode, 0, named.stderr)
        self.assert_untouched()

    def test_a_home_inside_the_scanned_folder_relocates_the_tool_folders(self) -> None:
        """R21-10: R20-7 pinned exit 2 for a HOME inside the scanned folder; the tool folders are relocated now."""
        home = self.case / "home"
        home.mkdir()
        self.listing = sorted(os.listdir(self.case))
        before = self._mtimes()
        done = _run(["scan", str(self.case), "--recursive", "--pixel", "deep", "--heatmaps"], self.case.parent, HOME=str(home))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stderr.count(RELOCATED), 1, done.stderr)
        self.assertEqual(os.listdir(home), [])
        self.assertEqual(self._mtimes(), before)
        self.assert_untouched()

    def test_an_explicit_folder_inside_the_evidence_is_still_exit_2(self) -> None:
        """R21-10 keeps the operator's explicit choice refused (R19-4/R20-7): nothing is relocated for it."""
        for env in ("DEEPFAKE_LENS_LOG_DIR", "DEEPFAKE_LENS_HEATMAP_DIR"):
            with self.subTest(env=env):
                before = self._mtimes()
                done = _run(["scan", str(self.case), "--pixel", "deep", "--heatmaps"], self.case.parent, **{env: str(self.case / "tool")})
                self.assertEqual(done.returncode, 2, done.stderr)
                self.assertIn(f"환경 변수 {env}의 폴더가 검사 대상 폴더 안에 있습니다", done.stderr)
                self.assertNotIn(RELOCATED, done.stderr)
                self.assertEqual(self._mtimes(), before)

    def test_no_place_outside_the_evidence_is_still_exit_2(self) -> None:
        from deepfake_lens import native_path
        from deepfake_lens.cli_inputs import UsageError, relocate_tool_base

        home = self.case / "home"
        self.addCleanup(native_path.set_tool_cache_base, None)
        with mock.patch.dict(os.environ, {"HOME": str(home)}), \
                mock.patch.object(native_path, "relocated_tool_base_candidates", lambda: [self.case / "c1", self.case.parent]):
            with self.assertRaises(UsageError) as caught:
                relocate_tool_base([self.case])
        self.assertIn("도구 폴더(로그·히트맵·오디오 numba 캐시)를 검사 대상 폴더 밖에 둘 곳이 없습니다", str(caught.exception))
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

    def test_a_file_in_the_home_folder_relocates_the_tool_folders(self) -> None:
        """R21-6 (round 21): ``HOME=/E1 classify /E1/a.png`` wrote ``/E1/.cache/deepfake-lens/logs`` next to the evidence
        (rc 0); R21-6 made it exit 2. R21-10 (user decision 2026-10-12: relocate instead of refuse): it is analyzed
        with the tool folders outside the file's folder, one notice, and nothing in that folder changes."""
        import wave

        home = Path(self._tmp.name) / "home"
        home.mkdir()
        (home / "note.txt").write_text("메모입니다.", encoding="utf-8")
        with wave.open(str(home / "tone.wav"), "wb") as handle:  # the audio worker (a child process) logs too
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"".join(int(8000 * ((n // 20) % 2 * 2 - 1)).to_bytes(2, "little", signed=True) for n in range(16000)))
        for argv in (["classify", str(home / "note.txt")], ["evidence", str(home / "note.txt")], ["classify", str(home / "tone.wav")]):
            with self.subTest(argv=argv):
                before = _snapshot(home)
                done = _run(argv, self.case.parent, HOME=str(home))
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertEqual(done.stderr.count(RELOCATED), 1, done.stderr)
                self.assertEqual(_snapshot(home), before)
        (home / "docs").mkdir()
        (home / "docs" / "note.txt").write_text("메모입니다.", encoding="utf-8")
        done = _run(["classify", str(home / "docs" / "note.txt")], self.case.parent, HOME=str(home))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn(RELOCATED, done.stderr)  # ~/.cache is not inside docs: the default place


class ServerDefaultRootToolFoldersTest(unittest.TestCase):
    """R21-4 (round 21): ``web`` and ``api-serve`` without ``--folder`` / ``--allow-root`` read the working folder.
    Started in the home folder (``HOME=/tmp/HM``, cwd ``/tmp/HM``), a scan request of it wrote
    ``.cache/deepfake-lens/logs`` and ``.cache/deepfake-lens/numba`` into the folder being served, and api-serve
    wrote its log there at start-up. The working folder is the examined folder of the tool-folder check; R21-4 made
    that exit 2, and R21-10 (user decision 2026-10-12: relocate instead of refuse) relocates the tool folders — the
    server starts, says where once, and nothing in the folder it serves changes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name).resolve() / "HM"
        self.home.mkdir()
        (self.home / "a.txt").write_text("메모입니다.", encoding="utf-8")

    def _serve_and_scan(self, argv: list[str]) -> tuple[str, int]:
        import socket
        import time
        import urllib.request
        from urllib.parse import quote

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base = {key: value for key, value in os.environ.items() if not key.startswith("DEEPFAKE_LENS_")}
        base.update(PYTHONPATH=str(PACKAGE.parent), HOME=str(self.home))
        proc = subprocess.Popen([sys.executable, "-m", "deepfake_lens", *argv, "--port", str(port)], cwd=self.home, env=base,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        status = 0
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    socket.create_connection(("127.0.0.1", port), 0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
            if proc.poll() is None:
                request = urllib.request.Request(f"http://127.0.0.1:{port}/api/scan?folder={quote(str(self.home))}",
                                                 headers={"X-Deepfake-Lens-Client": "gui", "Host": "localhost"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    status = response.status
        finally:
            proc.terminate()
            _, err = proc.communicate(timeout=60)
        return err, status

    def test_a_server_started_in_its_home_folder_without_a_root_relocates_the_tool_folders(self) -> None:
        import importlib.util

        servers = [["web"]] + ([["api-serve"]] if importlib.util.find_spec("fastapi") and importlib.util.find_spec("uvicorn") else [])
        for argv in servers:
            with self.subTest(argv=argv[0]):
                before = _snapshot(self.home)
                err, status = self._serve_and_scan(argv)
                self.assertEqual(status, 200, err)
                self.assertEqual(err.count(RELOCATED), 1, err)
                self.assertEqual(_snapshot(self.home), before)

    def test_the_working_folder_is_checked_only_when_no_root_is_named(self) -> None:
        from deepfake_lens import native_path
        from deepfake_lens.cli import build_parser
        from deepfake_lens.cli_inputs import check_command_inputs

        parser, _ = build_parser()
        elsewhere = Path(self._tmp.name).resolve() / "case"
        elsewhere.mkdir()
        previous = os.getcwd()
        self.addCleanup(native_path.set_examined_folders, [])
        self.addCleanup(native_path.set_tool_cache_base, None)
        try:
            os.chdir(self.home)
            with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
                for argv in (["web"], ["api-serve"]):
                    with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                        check_command_inputs(parser.parse_args(argv))
                        base = native_path.tool_cache_base()
                        assert base is not None
                        self.assertFalse(str(base.resolve()).startswith(str(self.home)), base)
                # a named root outside HOME: the working folder is not a read root, the default place is kept
                check_command_inputs(parser.parse_args(["web", "--folder", str(elsewhere)]))
                self.assertEqual(native_path.tool_cache_base(), self.home / ".cache" / "deepfake-lens")
        finally:
            os.chdir(previous)


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


class WindowsProgramDataToolBaseTest(unittest.TestCase):
    """R22-2 (round 22, user decision 2026-10-14): R21-10 relocated the tool base on macOS/Linux, but on Windows both
    candidates (%LOCALAPPDATA%, %TEMP%) are inside the user profile — `classify %USERPROFILE%\\x.jpg` and
    `cd ~; deepfake-lens web` were still exit 2 (the verifier's mock). %ProgramData%\\deepfake-lens\\<user> is now a
    candidate outside the profile, made with an owner-only ACL and verified like the POSIX candidates (this user's
    own folder nobody else can write; its shared parent lets nobody else replace it). Windows is mocked
    (HANDOFF §7: Windows paths are verified with fakes and mocks)."""

    def setUp(self) -> None:
        from deepfake_lens import native_path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.profile = base / "Users" / "alice"
        (self.profile / "AppData" / "Local" / "Temp").mkdir(parents=True)
        self.program_data = base / "ProgramData"
        self.program_data.mkdir()
        self.addCleanup(native_path.set_tool_cache_base, None)
        self.env = {
            "HOME": str(self.profile), "USERPROFILE": str(self.profile), "USERNAME": "alice",
            "LOCALAPPDATA": str(self.profile / "AppData" / "Local"), "TEMP": str(self.profile / "AppData" / "Local" / "Temp"),
            "TMPDIR": str(self.profile / "AppData" / "Local" / "Temp"), "ProgramData": str(self.program_data),
        }
        self.calls: list[tuple[str, str]] = []

    def _windows(self, *, owner_only: bool = True, parent_safe: bool = True, made: bool = True) -> contextlib.ExitStack:
        from deepfake_lens import native_path, windows_acl

        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.dict(os.environ, self.env))
        stack.enter_context(mock.patch.object(native_path, "_is_windows", return_value=True))
        stack.enter_context(mock.patch.object(windows_acl, "make_owner_only", lambda path: self.calls.append(("make", path)) or made))
        stack.enter_context(mock.patch.object(windows_acl, "owner_only", lambda path: self.calls.append(("owner", path)) or owner_only))
        stack.enter_context(mock.patch.object(windows_acl, "parent_safe", lambda path: self.calls.append(("parent", path)) or parent_safe))
        return stack

    def test_the_candidates_include_program_data_after_the_profile_cache(self) -> None:
        from deepfake_lens import native_path

        with self._windows():
            candidates = native_path.relocated_tool_base_candidates()
        target = self.program_data / "deepfake-lens" / "alice"
        self.assertIn(target, candidates)
        self.assertLess(candidates.index(self.profile / "AppData" / "Local" / "deepfake-lens"), candidates.index(target))

    def test_a_file_in_the_profile_relocates_to_program_data(self) -> None:
        """classify %USERPROFILE%\\x.jpg: the examined folder is the profile — every profile candidate overlaps."""
        from deepfake_lens import native_path
        from deepfake_lens.cli_inputs import relocate_tool_base

        err = io.StringIO()
        with self._windows(), contextlib.redirect_stderr(err):
            relocate_tool_base([self.profile])
            chosen = native_path.tool_cache_base()
        target = self.program_data / "deepfake-lens" / "alice"
        self.assertEqual(chosen, target)
        self.assertTrue(target.is_dir())
        self.assertIn(("make", str(target)), self.calls)  # a folder made here gets the owner-only ACL
        self.assertIn(("owner", str(target)), self.calls)
        self.assertIn(("parent", str(target.parent)), self.calls)
        self.assertEqual(err.getvalue().count(RELOCATED), 1)
        self.assertEqual(sorted(p.name for p in self.profile.rglob("*")), ["AppData", "Local", "Temp"])  # nothing in the profile

    def test_an_existing_folder_is_verified_not_re_permissioned(self) -> None:
        from deepfake_lens.cli_inputs import relocate_tool_base

        target = self.program_data / "deepfake-lens" / "alice"
        target.mkdir(parents=True)
        with self._windows(), contextlib.redirect_stderr(io.StringIO()):
            relocate_tool_base([self.profile])
        self.assertNotIn(("make", str(target)), self.calls)
        self.assertIn(("owner", str(target)), self.calls)

    def test_a_folder_of_another_account_or_a_replaceable_one_is_skipped(self) -> None:
        import shutil

        from deepfake_lens import native_path
        from deepfake_lens.cli_inputs import UsageError, relocate_tool_base

        target = self.program_data / "deepfake-lens" / "alice"
        real_candidates = native_path.relocated_tool_base_candidates
        for flags in ({"owner_only": False}, {"parent_safe": False}, {"made": False}):
            shutil.rmtree(self.program_data / "deepfake-lens", ignore_errors=True)
            native_path.set_tool_cache_base(None)
            # only the profile candidates and ProgramData (no POSIX /tmp candidate on a real Windows)
            with self.subTest(**flags), self._windows(**flags), contextlib.redirect_stderr(io.StringIO()), \
                    mock.patch.object(native_path, "_system_temp_dir", lambda: self.env["TEMP"]), \
                    mock.patch.object(native_path, "relocated_tool_base_candidates",
                                      lambda: [c for c in real_candidates() if c.parent != Path("/tmp")]):
                with self.assertRaises(UsageError) as caught:
                    relocate_tool_base([self.profile])
                self.assertIn("둘 곳이 없습니다", str(caught.exception))
                self.assertNotEqual(native_path.tool_cache_base(), target)

    def test_a_server_started_in_the_profile_relocates(self) -> None:
        """cd ~; deepfake-lens web: the default read root is the profile itself."""
        from deepfake_lens import native_path
        from deepfake_lens.cli_inputs import relocate_tool_base

        with self._windows(), contextlib.redirect_stderr(io.StringIO()):
            relocate_tool_base([self.profile])
            self.assertEqual(native_path.tool_cache_base(), self.program_data / "deepfake-lens" / "alice")

    def test_the_user_folder_name_is_never_a_path(self) -> None:
        from deepfake_lens.windows_acl import user_folder_name

        for name, expected in (("alice", "alice"), ("..\\..\\x", "_.._x"), ("a/b", "a_b"), ("", "user"), ("홍길동", "홍길동")):
            with self.subTest(name=name), mock.patch.dict(os.environ, {"USERNAME": name}):
                self.assertEqual(user_folder_name(), expected)


class WindowsAclDecisionTest(unittest.TestCase):
    """R22-2: the ACL decisions (pure functions over the owner SID and the DACL entries)."""

    USER = "S-1-5-21-1-2-3-1001"
    OTHER = "S-1-5-21-1-2-3-1002"
    USERS = "S-1-5-32-545"

    def _security(self, owner: str, *aces: tuple[int, int, str]) -> object:
        from deepfake_lens.windows_acl import Ace, Security

        return Security(owner, [Ace(kind, 0, mask, sid) for kind, mask, sid in aces])

    def test_owner_only(self) -> None:
        from deepfake_lens import windows_acl as w

        full = 0x1F01FF
        ok = self._security(self.USER, (0, full, self.USER), (0, full, w.SID_SYSTEM), (0, full, w.SID_ADMINISTRATORS), (0, full, w.SID_OWNER_RIGHTS))
        self.assertTrue(w.dacl_owner_only(ok, self.USER))  # type: ignore[arg-type]
        cases = {
            "owned by another account": self._security(self.OTHER, (0, full, self.USER)),
            "another account may write": self._security(self.USER, (0, full, self.USER), (0, w.FILE_WRITE_DATA, self.OTHER)),
            "Users may add a folder": self._security(self.USER, (0, w.FILE_APPEND_DATA, self.USERS)),
            "an unknown allow entry": self._security(self.USER, (5, 0x1, self.USER)),
        }
        for name, security in cases.items():
            with self.subTest(name):
                self.assertFalse(w.dacl_owner_only(security, self.USER))  # type: ignore[arg-type]
        self.assertFalse(w.dacl_owner_only(w.Security(self.USER, None), self.USER))  # NULL DACL: everyone
        readers = self._security(self.USER, (0, full, self.USER), (0, 0x1200A9, self.USERS), (1, full, self.OTHER))
        self.assertTrue(w.dacl_owner_only(readers, self.USER))  # type: ignore[arg-type]  # read-only others, a deny entry

    def test_parent_safe(self) -> None:
        from deepfake_lens import windows_acl as w

        full = 0x1F01FF
        # the default ProgramData inheritance: Users may add files and folders, not delete or replace them
        default = self._security(self.USER, (0, full, w.SID_SYSTEM), (0, full, w.SID_ADMINISTRATORS), (0, full, self.USER),
                                 (0, 0x1200A9 | w.FILE_WRITE_DATA | w.FILE_APPEND_DATA, self.USERS))
        self.assertTrue(w.dacl_parent_safe(default, self.USER))  # type: ignore[arg-type]
        self.assertTrue(w.dacl_parent_safe(self._security(w.SID_ADMINISTRATORS, (0, full, w.SID_ADMINISTRATORS)), self.USER))  # type: ignore[arg-type]
        for name, security in {
            "made by another account": self._security(self.OTHER, (0, full, self.OTHER)),
            "others may delete entries": self._security(self.USER, (0, w.FILE_DELETE_CHILD, self.USERS)),
            "others may change permissions": self._security(self.USER, (0, w.WRITE_DAC, self.OTHER)),
        }.items():
            with self.subTest(name):
                self.assertFalse(w.dacl_parent_safe(security, self.USER))  # type: ignore[arg-type]

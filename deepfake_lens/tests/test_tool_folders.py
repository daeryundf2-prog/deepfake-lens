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

from deepfake_lens import webapp_api

PACKAGE = Path(__file__).resolve().parents[1]
NO_SUCH_USER = "~dfl-r19-no-such-user"


def _run(args: list[str], cwd: Path, **env: str) -> subprocess.CompletedProcess[str]:
    base = {key: value for key, value in os.environ.items() if not key.startswith("DEEPFAKE_LENS_")}
    base.update(PYTHONPATH=str(PACKAGE.parent), HOME=str(cwd.parent / "home"), **env)
    return subprocess.run([sys.executable, "-m", "deepfake_lens", *args], cwd=cwd, env=base, capture_output=True, text=True, timeout=300)


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

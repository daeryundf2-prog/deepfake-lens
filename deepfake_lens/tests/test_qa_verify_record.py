"""R18-19 (e) (round 18): `qa_phase0.py --verify-record` in a shallow clone names the shallow clone.

In a ``--depth 1`` clone HEAD's parent (the commit the record was made on)
is not fetched, and the script said "기록 커밋 …이 HEAD나 그 부모가 아닙니다
— 다시 생성하세요" — a wrong instruction (``build_traceability_commits
--check`` says "얕은 클론"). The test builds a throwaway repository, so it
needs only the ``git`` binary.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]


def _harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("qa_phase0_r18", REPO_ROOT / "scripts" / "qa_phase0.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(shutil.which("git"), "git not available")
class VerifyRecordShallowCloneTest(unittest.TestCase):
    def git(self, cwd: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def test_a_depth_1_clone_is_named(self) -> None:
        harness = _harness()
        with tempfile.TemporaryDirectory() as tmp:
            origin = Path(tmp, "origin")
            origin.mkdir()
            self.git(origin, "init", "-q", "-b", "main")
            for key, value in (("user.email", "t@example.invalid"), ("user.name", "t"), ("commit.gpgsign", "false")):
                self.git(origin, "config", key, value)
            (origin / "f.txt").write_text("0", encoding="utf-8")
            self.git(origin, "add", "-A")
            self.git(origin, "commit", "-q", "-m", "work")
            measured = self.git(origin, "rev-parse", "HEAD")
            (origin / "docs").mkdir()
            (origin / "docs" / "CONFORMANCE.md").write_text(f"# 적합성\n\n{harness.COMMIT_LINE_PREFIX}{measured}` (깨끗한 트리)\n", encoding="utf-8")
            self.git(origin, "add", "-A")
            self.git(origin, "commit", "-q", "-m", "docs(qa): record")
            for depth, expected_code, expected_text in ((1, 1, "얕은 복제(shallow clone)라 HEAD의 부모가 없어"), (2, 0, "기록 확인")):
                with self.subTest(depth=depth):
                    clone = Path(tmp, f"clone{depth}")
                    subprocess.run(["git", "clone", "-q", f"--depth={depth}", origin.as_uri(), str(clone)], check=True, capture_output=True)
                    out, err = io.StringIO(), io.StringIO()
                    with mock.patch.object(harness, "REPO_ROOT", clone), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        code = harness.verify_record(clone / "docs" / "CONFORMANCE.md")
                    self.assertEqual(code, expected_code, err.getvalue())
                    self.assertIn(expected_text, out.getvalue() + err.getvalue())
                    self.assertNotIn("다시 생성하세요", err.getvalue())


if __name__ == "__main__":
    unittest.main()

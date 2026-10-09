"""P12/P14 (round 8): scripts/build_traceability_commits.py.

The Y13 table listed 115 commit hashes from before the history rewrite. The
script now rebuilds ``commits[]`` from ``git log`` (matching the previous
entries by subject) and ``--check`` fails when the committed table is not
what the history gives. P14: a ``Gaps:`` in the subject only is accepted.

The integration test builds a throwaway git repository, so it needs only
the ``git`` binary — not this checkout's history (CI clones may be shallow).
"""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_traceability_commits", REPO_ROOT / "scripts" / "build_traceability_commits.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


btc = _load()


class ParseTest(unittest.TestCase):
    def test_gaps_from_body_line(self) -> None:
        parsed = btc.parse_gaps("fix(x): thing (P1)", "text\n\nGaps: G30, G31\n\nCo-Authored-By: x")
        self.assertEqual((parsed["gaps"], parsed["new"], parsed["gaps_source"]), (["G30", "G31"], False, "body"))
        self.assertEqual(parsed["gaps_line"], "Gaps: G30, G31")

    def test_gaps_from_subject_only_p14(self) -> None:
        """P14: the subject's "(Z1-Z5; Gaps: G7, 신규)" is a valid Gaps statement."""
        parsed = btc.parse_gaps("fix(cli): Z1-Z5 usage errors (Z1-Z5; Gaps: G7, 신규)", "body without the line")
        self.assertEqual((parsed["gaps"], parsed["new"], parsed["gaps_source"]), (["G7"], True, "subject"))
        self.assertEqual(parsed["gaps_line"], "Gaps: G7; 신규(스펙 외)")

    def test_body_wins_over_subject(self) -> None:
        parsed = btc.parse_gaps("x (P1; Gaps: G7)", "Gaps: G30")
        self.assertEqual((parsed["gaps"], parsed["gaps_source"]), (["G30"], "body"))

    def test_no_gaps(self) -> None:
        self.assertEqual(btc.parse_gaps("merge: x", "")["gaps_line"], None)

    def test_round5_ids_are_not_gaps(self) -> None:
        self.assertEqual(btc.parse_gaps("x", "Gaps: G9; 신규(스펙 외) (V5-G3)")["gaps"], ["G9"])
        self.assertEqual(btc.gaps_named_in_subject("fix: V5-G12 ruff (G32)"), ["G32"])

    def test_subject_ids(self) -> None:
        self.assertEqual(btc.subject_ids("fix: a (P1; Gaps: G30, G31)"), ["P1"])
        self.assertEqual(btc.subject_ids("fix: a (P3, P5; Gaps: G32)"), ["P3", "P5"])
        self.assertEqual(btc.subject_ids("fix: a (Z1-Z5; Gaps: G7, 신규)"), ["Z1", "Z2", "Z3", "Z4", "Z5"])
        self.assertEqual(btc.subject_ids("docs(qa): regenerated (WP-J)"), ["WP-J"])
        self.assertEqual(btc.subject_ids("chore: nothing here"), [])
        # R9-10: round-9 IDs; "R9-10" is not the range R9..R10
        self.assertEqual(btc.subject_ids("fix: a (R9-4, R9-5; Gaps: G31, G7)"), ["R9-4", "R9-5"])
        self.assertEqual(btc.subject_ids("fix: a (R9-10; Gaps: 신규)"), ["R9-10"])
        self.assertEqual(btc.subject_ids("fix: a (R9-1; Gaps: G30, G34)"), ["R9-1"])

    def test_subject_without_gaps(self) -> None:
        self.assertEqual(btc.subject_without_gaps("test(qa): leg raw (QA-OUT-4; Gaps: G7, G8)"), "test(qa): leg raw")
        self.assertEqual(btc.subject_without_gaps("fix: keep (WP-J)"), "fix: keep (WP-J)")


@unittest.skipUnless(shutil.which("git"), "git binary required")
class GitRoundTripTest(unittest.TestCase):
    """A table regenerated in its own commit passes --check; stale or rewritten history fails."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "user.name", "t")
        self.git("config", "commit.gpgsign", "false")
        (self.root / "docs").mkdir()
        (self.root / "f.txt").write_text("0", encoding="utf-8")
        self.commit("chore: base")
        self.git("checkout", "-q", "-b", "phase0")
        self.json_path = self.root / "docs" / "traceability-commits.json"
        self.md_path = self.root / "docs" / "TRACEABILITY-COMMITS.md"
        seed = {
            "description": "test", "wp_gaps": {"WP-A": ["G5"]}, "unused_ids": {"R10": "unused"},
            "ids": [{"id": "P1", "round": 8, "source": "r8", "summary": "s", "gaps": ["G30"], "new": None},
                    {"id": "P9", "round": 8, "source": "r8", "summary": "s", "gaps": [], "new": "rule 3"}],
            "commits": [{"subject": "fix: old subject", "round": "7", "ids": ["P9"], "new_reasons": ["P9: curated"]}],
        }
        self.json_path.write_text(json.dumps(seed), encoding="utf-8")
        self.md_path.write_text("", encoding="utf-8")
        self.commit("docs: seed table")
        self.commit("fix: one (P1; Gaps: G30)", "a")
        self.commit("fix: old subject (P9; Gaps: 신규)", "b", body="Gaps: 신규(스펙 외)")
        self.patches = [mock.patch.object(btc, name, value) for name, value in (
            ("REPO_ROOT", self.root), ("JSON_PATH", self.json_path), ("MD_PATH", self.md_path))]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in self.patches:
            patch.stop()
        self._tmp.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def commit(self, subject: str, content: str | None = None, body: str = "", only: str | None = None) -> None:
        if content is not None:
            (self.root / "f.txt").write_text(content, encoding="utf-8")
        self.git("add", *([only] if only else ["-A"]))
        self.git("commit", "-q", "--allow-empty", "-m", subject + ("\n\n" + body if body else ""))

    def run_main(self, *args: str) -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = btc.main(list(args))
        return code, out.getvalue() + err.getvalue()

    def test_regenerate_commit_then_check(self) -> None:
        head_before = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.run_main("--check")[0], 0, "uncommitted regeneration of HEAD is current")
        self.commit("docs: regenerate table (P12)")
        code, text = self.run_main("--check")
        self.assertEqual(code, 0, text)
        table = json.loads(self.json_path.read_text(encoding="utf-8"))
        self.assertEqual(table["range"]["head"], head_before, "covers up to the parent of the regenerating commit")
        subjects = [c["subject"] for c in table["commits"]]
        self.assertNotIn("docs: regenerate table (P12)", subjects)
        by_subject = {c["subject"]: c for c in table["commits"]}
        one = by_subject["fix: one (P1; Gaps: G30)"]
        self.assertEqual((one["ids"], one["gaps"], one["gaps_source"], one["round"]), (["P1"], ["G30"], "subject", "8"))
        self.assertEqual(one["sha"], self.git("rev-parse", "HEAD~2").strip())
        matched = by_subject["fix: old subject (P9; Gaps: 신규)"]
        self.assertEqual((matched["new_reasons"], matched["round"], matched["pre_rewrite_subject"]), (["P9: curated"], "7", "fix: old subject"))
        self.assertEqual([i["commits"] for i in table["ids"]], [[one["short"]], [matched["short"]]])
        self.assertIn(one["short"], self.md_path.read_text(encoding="utf-8"))
        # R9-10 (round 9): this asserted that "later commits that do not touch the
        # table are fine" (encoded the defect — the table went stale unnoticed). A
        # commit after the regenerating one fails --check until the table is
        # regenerated again as the last commit.
        self.commit("fix: later (P1; Gaps: G30)", "c")
        code, text = self.run_main("--check")
        self.assertEqual(code, 1, text)
        self.assertIn("표 이후 커밋이 2개입니다", text)
        self.assertEqual(self.run_main()[0], 0)
        self.commit("docs: regenerate table again (P12)")
        code, text = self.run_main("--check")
        self.assertEqual(code, 0, text)
        self.assertIn("표 이후 커밋 1개(재생성 커밋만)", text)

    def test_r9_10_regeneration_commit_touches_only_the_table(self) -> None:
        """R9-10: the one commit after the table may change only the two table files."""
        self.run_main()
        (self.root / "f.txt").write_text("sneaked", encoding="utf-8")
        self.commit("docs: regenerate table (P12)")  # -A: f.txt rides along
        code, text = self.run_main("--check")
        self.assertEqual(code, 1, text)
        self.assertIn("추적표 파일 밖의 파일도 바꿨습니다: f.txt", text)
        # R10-8: the message names every file the regeneration commit may hold,
        # the conformance record included.
        self.assertIn("docs/CONFORMANCE.md", text)

    def test_r10_8_conformance_record_may_ride_with_the_regeneration(self) -> None:
        """R10-8: docs/CONFORMANCE.md (scripts/qa_phase0.py) is a generated record the
        regeneration commit may carry; the docstring and the message say so."""
        self.run_main()
        (self.root / "docs" / "CONFORMANCE.md").write_text("# 적합성 표\n", encoding="utf-8")
        self.commit("docs: regenerate table and conformance record (P12)")
        code, text = self.run_main("--check")
        self.assertEqual(code, 0, text)
        self.assertIn("docs/CONFORMANCE.md", btc.__doc__ or "")

    def test_check_fails_on_hand_edit_and_on_rewritten_history(self) -> None:
        self.run_main()
        self.commit("docs: regenerate table (P12)")
        good = self.json_path.read_text(encoding="utf-8")
        self.json_path.write_text(good.replace('"G30"', '"G31"', 1), encoding="utf-8")
        self.commit("docs: hand edit")
        code, text = self.run_main("--check")
        self.assertEqual(code, 1)
        self.assertIn("재생성한 커밋의 부모", text)
        # rewrite the history under the table: its range is gone
        self.git("reset", "-q", "--hard", "HEAD~1")
        self.git("checkout", "-q", "--orphan", "rewritten")
        self.commit("chore: rewritten root")
        code, text = self.run_main("--check")
        self.assertEqual(code, 1)
        self.assertIn("HEAD의 조상이 아닙니다", text)
        # a range sha that no longer exists at all (garbage-collected rewrite, shallow clone)
        table = json.loads(good)
        table["range"]["base"] = "0" * 40
        self.json_path.write_text(json.dumps(table), encoding="utf-8")
        code, text = self.run_main("--check")
        self.assertEqual(code, 1)
        self.assertIn("히스토리에 없습니다", text)

    def test_check_fails_when_table_lags_its_commit(self) -> None:
        self.run_main()
        self.commit("fix: sneaked in before committing the table", "z", only="f.txt")
        self.commit("docs: regenerate table (P12)")  # table covers HEAD~2, not this commit's parent
        code, text = self.run_main("--check")
        self.assertEqual(code, 1)
        self.assertIn("부모", text)


if __name__ == "__main__":
    unittest.main()

"""The QA traceability table is consistent with the tests (WP-J).

- every automated QA ID has exactly one test whose docstring first line is
  "<QA ID>: <통과 기준 원문>" (the canonical test scripts/qa_phase0.py reports);
- every QA ID a test is tagged with exists in traceability.json;
- every QA ID a requirement row names is defined, and every automated QA ID
  backs at least one requirement;
- every manual QA ID has its checklist section in docs/QA-MANUAL.md.
"""

from __future__ import annotations

import sys
import unittest
from typing import Any

from .traceability import (
    REPO_ROOT,
    TESTS_DIR,
    automated_criteria,
    canonical_tests,
    iter_tests,
    load_traceability,
    qa_tag,
)


class TraceabilityTest(unittest.TestCase):
    data: dict[str, Any]
    criteria: dict[str, str]
    tests: list[unittest.TestCase]

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_traceability()
        cls.criteria = automated_criteria(cls.data)
        suite = unittest.TestLoader().discover(str(TESTS_DIR), top_level_dir=str(REPO_ROOT))
        cls.tests = [test for test in iter_tests(suite) if not test.id().startswith("unittest.loader")]

    def test_loader_found_the_suite(self) -> None:
        self.assertGreater(len(self.tests), 739)

    def test_exactly_one_canonical_test_per_automated_qa_id(self) -> None:
        found = canonical_tests(self.tests, self.criteria)
        self.assertEqual(len(self.criteria), 20)
        for qa_id, ids in sorted(found.items()):
            with self.subTest(qa=qa_id):
                self.assertEqual(len(ids), 1, f"{qa_id}: {ids or 'no test'} has the criterion as its docstring first line")

    def test_tags_name_known_qa_ids(self) -> None:
        known = set(self.data["qa"])
        for test in self.tests:
            tag = qa_tag(test)
            if tag is not None:
                with self.subTest(test=test.id()):
                    self.assertIn(tag, known)
                    self.assertEqual(self.data["qa"][tag]["mode"], "automated", "only automated QA IDs have tests")

    def test_requirement_rows_are_consistent(self) -> None:
        known = set(self.data["qa"])
        backed: set[str] = set()
        for row in self.data["requirements"]:
            with self.subTest(requirement=row["id"]):
                self.assertTrue(set(row["qa"]) <= known, row)
                self.assertTrue(row["qa"] or row.get("phase1"), "a row needs a QA ID or a phase-1 note")
                self.assertTrue(all(gap.startswith("G") for gap in row["gaps"]))
                backed.update(row["qa"])
        self.assertEqual(sorted(set(self.criteria) - backed), [])

    def test_manual_checklists_exist(self) -> None:
        manual_doc = (REPO_ROOT / "docs" / "QA-MANUAL.md").read_text(encoding="utf-8")
        for qa_id, entry in self.data["qa"].items():
            if entry["mode"] == "manual":
                with self.subTest(qa=qa_id):
                    self.assertIn(f"## {qa_id}", manual_doc)
                    self.assertTrue(entry["checklist"].startswith("docs/QA-MANUAL.md#"))



class HarnessLogicTest(unittest.TestCase):
    """scripts/qa_phase0.py result folding (no suite run)."""

    @classmethod
    def setUpClass(cls) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("qa_phase0_harness", REPO_ROOT / "scripts" / "qa_phase0.py")
        assert spec is not None and spec.loader is not None
        cls.harness = importlib.util.module_from_spec(spec)
        # dataclasses resolve annotations through sys.modules[__module__].
        sys.modules[spec.name] = cls.harness
        spec.loader.exec_module(cls.harness)
        cls.data = load_traceability()

    harness: Any
    data: dict[str, Any]

    def _records(self, **statuses: str) -> dict[str, Any]:
        records: dict[str, Any] = {}
        for name, status in statuses.items():
            qa_id = "QA-" + name.split("__")[0].replace("_", "-")
            records[name] = self.harness.TestRecord(name, qa_id, status=status)
        return records

    def test_failure_of_a_tagged_test_fails_the_qa_id_and_sys10(self) -> None:
        records = self._records(IN_1__canon="passed", IN_1__other="failed", SYS_10__canon="passed")
        outcomes = self.harness.qa_outcomes(records, {"QA-IN-1": ["IN_1__canon"], "QA-SYS-10": ["SYS_10__canon"]}, full_suite=True)
        self.assertEqual(outcomes["QA-IN-1"]["result"], "실패")
        self.assertEqual(outcomes["QA-SYS-10"]["result"], "실패", "any suite failure fails QA-SYS-10")

    def test_canonical_count_must_be_one_and_skips_are_not_passes(self) -> None:
        records = self._records(IN_2__a="passed", IN_2__b="passed", IN_4__canon="skipped")
        outcomes = self.harness.qa_outcomes(records, {"QA-IN-2": ["IN_2__a", "IN_2__b"], "QA-IN-4": ["IN_4__canon"], "QA-IN-5": []}, full_suite=True)
        self.assertEqual(outcomes["QA-IN-2"]["result"], "실패")
        self.assertEqual(outcomes["QA-IN-4"]["result"], "건너뜀")
        self.assertEqual(outcomes["QA-IN-5"]["result"], "실패")

    def test_summary_and_rows_cover_every_requirement(self) -> None:
        criteria = automated_criteria(self.data)
        outcomes = {qa_id: {"result": "통과"} for qa_id in criteria}
        counts = self.harness.summary_counts(self.data, outcomes)
        self.assertEqual(self.harness.summary_line(counts), "20 통과 / 0 실패 / 4 수동 / 12 1단계")
        logs = {qa_id: REPO_ROOT / "build" / "qa-logs" / f"{qa_id}.log" for qa_id in criteria}
        rows = self.harness.conformance_rows(self.data, outcomes, logs)
        self.assertEqual({row[0] for row in rows} - {"—"}, {row["id"] for row in self.data["requirements"]})
        self.assertIn(("R-TXT-3", "G2, G25", "QA-SYS-9 (게이트)", "통과", "`build/qa-logs/QA-SYS-9.log`"), rows)
        self.assertIn(("R-IN-4", "—", "QA-IN-3", "수동", "`docs/QA-MANUAL.md#qa-in-3`"), rows)
        # QA-OUT-5 is a structural check in phase 0 and says so on every row.
        out5 = [row for row in rows if row[2].startswith("QA-OUT-5")]
        self.assertTrue(out5)
        self.assertTrue(all("구조 검사(0단계에 보정 모델 없음)" in row[2] for row in out5), out5)

    def test_recorded_run_requires_fastapi(self) -> None:
        """D6/QA-OUT-4: without fastapi the harness exits 1 ("fastapi 필요")
        before running anything, instead of recording a skipped API leg."""
        import contextlib
        import io
        from unittest import mock

        real_find_spec = self.harness.importlib.util.find_spec

        def no_fastapi(name: str, *args: Any, **kwargs: Any) -> Any:
            return None if name in {"fastapi", "httpx"} else real_find_spec(name, *args, **kwargs)

        stderr = io.StringIO()
        with mock.patch.object(self.harness.importlib.util, "find_spec", no_fastapi), \
                mock.patch.object(self.harness, "load_suite", side_effect=AssertionError("suite must not run")), \
                contextlib.redirect_stderr(stderr):
            code = self.harness.main(["--out", str(REPO_ROOT / "build" / "never-written.md")])
        self.assertEqual(code, 1)
        self.assertIn("fastapi 필요", stderr.getvalue())
        self.assertIn("pip install fastapi httpx uvicorn", stderr.getvalue())

    def test_dirty_tree_is_refused_without_allow_dirty(self) -> None:
        import contextlib
        import io
        from unittest import mock

        def fake_git(*args: str) -> str:
            return {"rev-parse": "a" * 40, "status": " M deepfake_lens/core.py"}[args[0]]

        stderr = io.StringIO()
        with mock.patch.object(self.harness.importlib.util, "find_spec", return_value=object()), \
                mock.patch.object(self.harness, "_git", fake_git), \
                mock.patch.object(self.harness, "load_suite", side_effect=AssertionError("suite must not run")), \
                contextlib.redirect_stderr(stderr):
            code = self.harness.main(["--out", str(REPO_ROOT / "build" / "never-written.md")])
        self.assertEqual(code, 1)
        self.assertIn("--allow-dirty", stderr.getvalue())

    def test_verify_record_checks_commit_and_later_changes(self) -> None:
        import contextlib
        import io
        import tempfile
        from unittest import mock

        from pathlib import Path

        parent, head = "b" * 40, "c" * 40
        out_rel = "docs/CONFORMANCE.md"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "CONFORMANCE.md"
            out.write_text(f"# x\n\n{self.harness.COMMIT_LINE_PREFIX}{parent}` — 변경 없는 작업 트리에서 실행.\n", encoding="utf-8")

            def run(diff: str) -> int:
                answers = {"HEAD": head, "HEAD^": parent}

                def fake_git(*args: str) -> str:
                    if args[0] == "rev-parse":
                        return answers[args[1]]
                    return diff

                with mock.patch.object(self.harness, "_git", fake_git), \
                        mock.patch.object(self.harness, "_rel", return_value=out_rel), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    return int(self.harness.verify_record(out))

            self.assertEqual(run(out_rel), 0)
            self.assertEqual(run(f"{out_rel}\ndeepfake_lens/core.py"), 1)
            out.write_text(f"{self.harness.COMMIT_LINE_PREFIX}{'d' * 40}`\n", encoding="utf-8")
            self.assertEqual(run(out_rel), 1, "recorded commit is neither HEAD nor its parent")


if __name__ == "__main__":
    unittest.main()

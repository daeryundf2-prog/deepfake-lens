"""GUI-REV G-1/G-9: the exhibit choice never drops a row from a signed artifact.

The redesigned GUI posted only the chosen verdict groups to /api/report, so
the signed JSON, HTML, forensic PDF and evidence statement said e.g. "총 2건
… 기록되지 않은 파일: 없음" for a 14-file folder. Now every row is posted;
``exhibit_paths`` only picks which rows get exhibit numbers in the evidence
statement, and the other rows are kept — verdict and SHA-256 — in the
statement's signed appendix (``excluded_by_selection``). ``submitter_side``
names the submitting party (갑호증 / 을호증) without touching a verdict.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens import webapp_api
from deepfake_lens.analysis_api import scan_folder_run, scan_payload
from deepfake_lens.evidence_statement import SELECTION_APPENDIX_TITLE, build_evidence_statement, signed_statement_body
from deepfake_lens.signing import verify_report

KEY = b"gui-rev-test-key-0123456789abcdef"


def _folder(tmp: str) -> Path:
    folder = Path(tmp)
    for index in range(4):
        (folder / f"note{index}.txt").write_text(f"사람이 쓴 글 {index}번입니다.", encoding="utf-8")
    (folder / "odd.xyz").write_bytes(b"?")
    return folder


def _scan(folder: Path) -> tuple[Any, list[Any], dict[str, Any]]:
    options = webapp_api._web_options({"no_default_engine": ["true"]})
    run = scan_folder_run(folder, options)
    return run.summary, run.items, scan_payload(run.summary, run.items, run.thresholds, options)


class StatementSelectionTest(unittest.TestCase):
    def test_unchosen_rows_go_to_a_signed_appendix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary, items, _ = _scan(_folder(tmp))
            chosen = {items[0].path}
            statement = build_evidence_statement(items, summary=summary, exhibit_paths=chosen)
            everything = build_evidence_statement(items, summary=summary)
        self.assertEqual([entry.file_path for entry in statement.entries], [items[0].path])
        self.assertEqual(statement.entries[0].exhibit_no, "갑 제1호증")
        appendix = statement.excluded_by_selection
        self.assertEqual(sorted(row["file_path"] for row in appendix), sorted(item.path for item in items[1:]))
        for row in appendix:
            self.assertTrue(row["sha256"] or row["hash_unavailable"], row)
            self.assertTrue(row["verdict_label"], row)
        # 기록되지 않은 파일 is still counted over the whole scan (odd.xyz is unsupported).
        self.assertEqual(statement.unrecorded_files, everything.unrecorded_files)
        self.assertEqual(statement.unrecorded_files["total_files"], 1)
        markdown = statement.to_markdown()
        self.assertIn(SELECTION_APPENDIX_TITLE, markdown)
        for item in items[1:]:
            self.assertIn(item.path.replace(".", "\\."), markdown.replace("\\\\", "\\"))
        signed = signed_statement_body(statement, KEY)
        self.assertTrue(verify_report(signed, KEY).verified)
        forged = json.loads(json.dumps(signed))
        forged["excluded_by_selection"] = forged["excluded_by_selection"][1:]
        self.assertFalse(verify_report(forged, KEY).verified, "dropping an appendix row must break the signature")

    def test_the_long_standing_statement_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary, items, _ = _scan(_folder(tmp))
            statement = build_evidence_statement(items, summary=summary)
        body = statement.to_json()
        self.assertNotIn("excluded_by_selection", body)
        self.assertNotIn("submitter_side", body)
        self.assertEqual(len(statement.entries), len(items))
        self.assertNotIn(SELECTION_APPENDIX_TITLE, statement.to_markdown())
        self.assertIn("원고(고소인)의 대리인", statement.to_markdown())

    def test_defendant_side_numbers_eul_exhibits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary, items, _ = _scan(_folder(tmp))
            statement = build_evidence_statement(items, summary=summary, submitter_side="defendant")
        self.assertEqual(statement.entries[0].exhibit_no, "을 제1호증")
        self.assertEqual(statement.to_json()["submitter_side"], "defendant")
        markdown = statement.to_markdown()
        self.assertIn("피고(피의자)의 대리인", markdown)
        self.assertIn("**피고(피의자) 변호인**", markdown)
        self.assertNotIn("원고(고소인)의 대리인", markdown)
        verdicts = [entry.verdict_label for entry in statement.entries]
        with tempfile.TemporaryDirectory() as tmp:
            summary, items, _ = _scan(_folder(tmp))
            self.assertEqual(verdicts, [entry.verdict_label for entry in build_evidence_statement(items, summary=summary).entries])
        with self.assertRaises(ValueError):
            build_evidence_statement(items, submitter_side="court")


class WebReportSelectionTest(unittest.TestCase):
    def setUp(self) -> None:
        roots = mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = _folder(self._tmp.name)
        webapp_api.configure_read_roots(self.folder)
        _, self.items, payload = _scan(self.folder)
        self.rows = payload["items"]
        self.summary = payload["summary"]

    def _body(self, **extra: object) -> bytes:
        body = {"items": self.rows, "summary": self.summary, "scan_root": str(self.folder.resolve()),
                "options": {"no_default_engine": "true"}, **extra}
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def test_evidence_statement_keeps_every_row(self) -> None:
        captured: dict[str, Any] = {}

        def fake_pdf(path: Any, statement: Any, *, signed: Any = None, unsigned_rows: Any = None, **_: Any) -> dict[str, Any]:
            captured["statement"], captured["signed"] = statement, signed
            Path(path).write_bytes(b"%PDF-fake")
            return signed

        chosen = [self.rows[0]["path"]]
        with mock.patch("deepfake_lens.evidence_statement.write_evidence_statement_pdf", fake_pdf):
            out = webapp_api._report_payload(self._body(exhibit_paths=chosen, submitter_side="defendant"), "evidence", default_folder=self.folder)
        self.assertEqual(out, b"%PDF-fake")
        statement, signed = captured["statement"], captured["signed"]
        self.assertEqual([entry.file_path for entry in statement.entries], chosen)
        self.assertEqual(statement.entries[0].exhibit_no, "을 제1호증")
        appendix = sorted(row["file_path"] for row in signed["excluded_by_selection"])
        self.assertEqual(appendix, sorted(row["path"] for row in self.rows[1:]))
        self.assertEqual(signed["unrecorded_files"]["total_files"], self.summary["unsupported_or_failed"])

    def test_other_formats_carry_every_row_whatever_the_choice(self) -> None:
        signed = webapp_api._report_payload(self._body(exhibit_paths=[self.rows[0]["path"]]), "json", default_folder=self.folder)
        assert isinstance(signed, dict)
        self.assertEqual(signed["summary"]["total"], self.summary["total"])
        self.assertEqual(len(signed["items"]), len(self.rows))

    def test_malformed_choice_is_refused(self) -> None:
        for extra in ({"exhibit_paths": "a.txt"}, {"exhibit_paths": [1]}, {"submitter_side": "court"}):
            with self.subTest(extra=extra):
                out = webapp_api._report_payload(self._body(**extra), "evidence", default_folder=self.folder)
                assert isinstance(out, dict)
                self.assertEqual(webapp_api.report_error_status(out), 400, out)


if __name__ == "__main__":
    unittest.main()

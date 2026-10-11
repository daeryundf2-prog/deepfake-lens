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
    import zipfile

    with zipfile.ZipFile(folder / "evid.zip", "w") as archive:
        archive.writestr("inner/memo.txt", "압축 안의 글입니다.")
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

    def test_an_ordered_choice_numbers_exhibits_in_its_order(self) -> None:
        """GUI-REV2 H-1: the exhibits are numbered in the posted order (the preview's), not the scan's."""
        with tempfile.TemporaryDirectory() as tmp:
            summary, items, _ = _scan(_folder(tmp))
            paths = [item.path for item in items]
            member = next(path for path in paths if "::" in path)
            container = member.split("::", 1)[0]
            order = [paths[-1], member, container, paths[0]]
            statement = build_evidence_statement(items, summary=summary, exhibit_paths=order, submitter_side="defendant")
        self.assertEqual([(entry.exhibit_no, entry.file_path) for entry in statement.entries],
                         [(f"을 제{index}호증", path) for index, path in enumerate(dict.fromkeys(order), start=1)])

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
        # GUI-REV2 H-3: this pinned "피고(피의자)의 대리인" (Markdown) while the PDF said
        # "변호인"; the defendant side now says 변호인 in every rendering, lists no statute
        # and puts the client placeholder in the defendant box.
        self.assertIn("피고(피의자)의 변호인", markdown)
        self.assertIn("**피고(피의자) 변호인**", markdown)
        self.assertNotIn("대리인", markdown)
        self.assertNotIn("검토 참고", markdown)
        self.assertNotIn("정보통신망", markdown)
        self.assertTrue(all(entry.statutes == [] for entry in statement.entries))
        verdicts = [entry.verdict_label for entry in statement.entries]
        with tempfile.TemporaryDirectory() as tmp:
            summary, items, _ = _scan(_folder(tmp))
            self.assertEqual(verdicts, [entry.verdict_label for entry in build_evidence_statement(items, summary=summary).entries])
        with self.assertRaises(ValueError):
            build_evidence_statement(items, submitter_side="court")


def _manual_row(path: str, *, evidence: list[Any] = (), coverage: list[Any] = (), reference: bool = False) -> Any:
    from deepfake_lens.result_types import (
        ClassificationResult, Grade, RiskBand, ScanItem, SourceConfidence, SourceGuess, Verdict,
    )

    result = ClassificationResult(
        score=0, band=RiskBand.UNKNOWN, band_label="판단 불가", verdict="판단 불가", signals=[], limitations=[],
        source_guess=SourceGuess("출처 단서 없음", SourceConfidence.UNKNOWN, []), next_checks=[],
        evidence=list(evidence), coverage=list(coverage), verdict_code=Verdict.UNDETERMINED,
        grade=Grade.REFERENCE if reference else Grade.EVIDENCE,
    )
    return ScanItem(path, path, "image", "analyzed", 1, result, sha256="c" * 64)


class AppendixAspectsTest(unittest.TestCase):
    def test_appendix_rows_keep_why_they_are_undetermined(self) -> None:
        """GUI-REV2 H-4: an unchosen 판단 불가 row keeps its cue / failure / grade in the appendix."""
        from deepfake_lens.result_types import CoverageEntry, CoverageStatus, EvidenceDirection, EvidenceItem, EvidenceKind, EvidenceStrength

        cue = EvidenceItem("생성 도구 메타데이터 단서", "Midjourney v6", EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE, "metadata")
        c2pa = EvidenceItem("C2PA 매니페스트 존재(검증 미완료)", "", EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "c2pa")
        failed = CoverageEntry("metadata", CoverageStatus.FAILED, "AnalyzerError: 잘림")
        rows = [
            _manual_row("chosen.png"),
            _manual_row("cue.png", evidence=[cue]),
            _manual_row("cue_failed.png", evidence=[cue], coverage=[failed]),
            _manual_row("signed.png", evidence=[c2pa]),
            _manual_row("meta.pdf", evidence=[cue], reference=True),
            _manual_row("plain.png"),
        ]
        statement = build_evidence_statement(rows, exhibit_paths=["chosen.png"])
        appendix = {row["file_path"]: row for row in statement.excluded_by_selection}
        self.assertEqual(appendix["cue.png"]["undetermined"], ["단서 1건"])
        self.assertIn("생성 도구 메타데이터 단서", appendix["cue.png"]["note"])
        self.assertEqual(appendix["cue_failed.png"]["undetermined"], ["단서 1건", "검사 실패"])
        self.assertIn("검사 실패: 메타데이터", appendix["cue_failed.png"]["note"])
        self.assertEqual(appendix["signed.png"]["undetermined"], ["확인 안 된 기록"])
        self.assertEqual(appendix["meta.pdf"]["undetermined"], ["단서 1건", "참고 전용"])
        self.assertEqual(appendix["plain.png"]["undetermined"], ["근거 없음"])
        lines = "\n".join(statement.selection_lines())
        self.assertIn("cue_failed.png — 판단 불가(단서 1건 · 검사 실패) — 단서: 생성 도구 메타데이터 단서 / 검사 실패: 메타데이터 — SHA-256: " + "c" * 64, lines)
        self.assertIn("plain.png — 판단 불가(근거 없음) — SHA-256:", lines)


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

    def test_rendered_pdf_lists_the_appendix(self) -> None:
        """GUI-REV G-10: the real evidence-statement PDF (pymupdf) prints the appendix rows and hashes."""
        from deepfake_lens.pdf_backend import import_pymupdf, pymupdf_available

        if not pymupdf_available():
            self.skipTest("pymupdf not installed")
        chosen = [self.rows[0]["path"]]
        pdf = webapp_api._report_payload(self._body(exhibit_paths=chosen, submitter_side="plaintiff"), "evidence", default_folder=self.folder)
        assert isinstance(pdf, bytes), pdf
        with import_pymupdf().open(stream=pdf, filetype="pdf") as doc:
            text = "".join(page.get_text() for page in doc).replace("\n", "")
        self.assertIn(SELECTION_APPENDIX_TITLE, text)
        self.assertIn(f"서증으로 넣지 않은 파일 {len(self.rows) - 1}개", text)
        for row in self.rows[1:]:
            self.assertIn(row["path"], text)
            if row.get("sha256"):
                self.assertIn(row["sha256"], text)
        self.assertIn("갑 제1호증", text)
        self.assertNotIn("갑 제2호증", text)

    def test_web_statement_follows_the_posted_order(self) -> None:
        captured: dict[str, Any] = {}

        def fake_pdf(path: Any, statement: Any, *, signed: Any = None, unsigned_rows: Any = None, **_: Any) -> dict[str, Any]:
            captured["statement"] = statement
            Path(path).write_bytes(b"%PDF-fake")
            return signed

        order = [row["path"] for row in reversed(self.rows) if row.get("result")]
        with mock.patch("deepfake_lens.evidence_statement.write_evidence_statement_pdf", fake_pdf):
            webapp_api._report_payload(self._body(exhibit_paths=order, submitter_side="plaintiff"), "evidence", default_folder=self.folder)
        self.assertEqual([entry.file_path for entry in captured["statement"].entries], order)
        self.assertEqual(captured["statement"].entries[0].exhibit_no, "갑 제1호증")

    def test_evidence_json_is_the_signed_statement_in_the_posted_order(self) -> None:
        """GUI-REV2 H-1: format=evidence-json is the body the evidence PDF is rendered from."""
        order = [row["path"] for row in reversed(self.rows) if row.get("result")][:3]
        with mock.patch.dict("os.environ", {"DEEPFAKE_LENS_REPORT_KEY": KEY.decode()}):
            body = webapp_api._report_payload(self._body(exhibit_paths=order, submitter_side="defendant"), "evidence-json", default_folder=self.folder)
        assert isinstance(body, dict), body
        self.assertEqual(body["report_type"], "evidence-statement")
        self.assertEqual([(entry["exhibit_no"], entry["file_path"]) for entry in body["entries"]],
                         [(f"을 제{index}호증", path) for index, path in enumerate(order, start=1)])
        self.assertEqual(len(body["excluded_by_selection"]), len(self.rows) - len(order))
        self.assertTrue(verify_report(body, KEY).verified)

    def test_other_formats_carry_every_row_whatever_the_choice(self) -> None:
        signed = webapp_api._report_payload(self._body(exhibit_paths=[self.rows[0]["path"]]), "json", default_folder=self.folder)
        assert isinstance(signed, dict)
        self.assertEqual(signed["summary"]["total"], self.summary["total"])
        self.assertEqual(len(signed["items"]), len(self.rows))

    def test_defendant_side_placeholders_and_statutes_on_the_web(self) -> None:
        """GUI-REV2 H-3: the empty-box placeholders follow the side; plaintiff is unchanged."""
        bodies = {}
        for side in ("plaintiff", "defendant"):
            out = webapp_api._report_payload(self._body(submitter_side=side), "evidence-json", default_folder=self.folder)
            assert isinstance(out, dict), out
            bodies[side] = out
        self.assertEqual((bodies["plaintiff"]["plaintiff"], bodies["plaintiff"]["defendant"]), ("(의뢰사 상호명 입력) 귀하", "(피고/피의자 성명 입력)"))
        # GUI-REV3 J-10: no "귀하" in the defendant box (it pinned "(의뢰인 성명 입력) 귀하").
        self.assertEqual((bodies["defendant"]["plaintiff"], bodies["defendant"]["defendant"]), ("(원고/고소인 성명 입력)", "(의뢰인 성명 입력)"))
        self.assertTrue(all("피고·피의자 측 제출" in entry["purpose_of_proof"] for entry in bodies["defendant"]["entries"]))

    def test_rendered_defendant_pdf_says_byeonhoin(self) -> None:
        from deepfake_lens.evidence_statement import write_evidence_statement_pdf
        from deepfake_lens.pdf_backend import import_pymupdf, pymupdf_available

        if not pymupdf_available():
            self.skipTest("pymupdf not installed")
        with tempfile.TemporaryDirectory() as tmp:
            summary, items, _ = _scan(_folder(tmp))
            statement = build_evidence_statement(items, summary=summary, submitter_side="defendant")
            pdf = Path(tmp) / "s.pdf"
            write_evidence_statement_pdf(pdf, statement, key=KEY)
            with import_pymupdf().open(str(pdf)) as doc:
                text = "".join(page.get_text() for page in doc).replace("\n", "")
        self.assertIn("피고(피의자)의 변호인은", text)
        self.assertNotIn("대리인", text)
        self.assertNotIn("검토 참고", text)

    def test_preview_is_the_pdf_template(self) -> None:
        """GUI-REV2 H-2: format=evidence-preview gives the PDF's own sections; every line is in the PDF."""
        chosen = [self.rows[0]["path"]]
        preview = webapp_api._report_payload(self._body(exhibit_paths=chosen, submitter_side="defendant"), "evidence-preview", default_folder=self.folder)
        assert isinstance(preview, dict), preview
        titles = [title for title, _ in preview["boxes"]]
        self.assertEqual(titles[:2], ["무결성/해석 고지", "기록되지 않은 파일"])
        self.assertEqual(titles[2], SELECTION_APPENDIX_TITLE)
        self.assertEqual([entry["exhibit_no"] for entry in preview["entries"]], ["을 제1호증"])
        self.assertIn("피고(피의자)의 변호인은", preview["case_lines"][3])
        flat = json.dumps(preview, ensure_ascii=False)
        for absent in ("결론 요약", "검사의 한계"):
            self.assertNotIn(absent, flat)
        from deepfake_lens.pdf_backend import import_pymupdf, pymupdf_available

        if not pymupdf_available():
            self.skipTest("pymupdf not installed")
        pdf = webapp_api._report_payload(self._body(exhibit_paths=chosen, submitter_side="defendant"), "evidence", default_folder=self.folder)
        assert isinstance(pdf, bytes), pdf
        with import_pymupdf().open(stream=pdf, filetype="pdf") as doc:
            text = "".join("".join(page.get_text().split()) for page in doc)
        for line in [*preview["case_lines"], *(line for _, lines in preview["boxes"] for line in lines), *preview["signoff"]]:
            self.assertIn("".join(line.split()), text, line)

    def test_an_empty_unknown_or_repeated_choice_is_refused(self) -> None:
        """GUI-REV2 H-7: no statement is signed with 0 exhibits or with paths that are not its rows."""
        first = self.rows[0]["path"]
        cases = {
            "empty": ([], None),
            "unknown": (["nope.png", first, "../../etc/passwd"], ["nope.png", "../../etc/passwd"]),
            "repeated": ([first, first], None),
        }
        for name, (paths, unknown) in cases.items():
            for fmt in ("evidence", "evidence-json"):
                with self.subTest(case=name, format=fmt):
                    out = webapp_api._report_payload(self._body(exhibit_paths=paths), fmt, default_folder=self.folder)
                    assert isinstance(out, dict), out
                    self.assertEqual(webapp_api.report_error_status(out), 400, out)
                    if unknown is not None:
                        self.assertEqual(out["unknown_exhibit_paths"], unknown)

    def test_a_chosen_file_gone_since_the_scan_is_said_so(self) -> None:
        """GUI-REV3 J-7: a chosen row the server cannot re-analyze is not called "not a signed row"."""
        chosen = next(row["path"] for row in self.rows if row["path"].endswith(".txt") and "::" not in row["path"])
        (self.folder / chosen).unlink()
        out = webapp_api._report_payload(self._body(exhibit_paths=[chosen]), "evidence-json", default_folder=self.folder)
        assert isinstance(out, dict), out
        self.assertEqual(webapp_api.report_error_status(out), 400)
        self.assertEqual(out["code"], "exhibit_not_rederived")
        self.assertEqual([row["path"] for row in out["not_rederived_exhibit_paths"]], [chosen])
        self.assertTrue(out["not_rederived_exhibit_paths"][0]["reason"])
        unknown = webapp_api._report_payload(self._body(exhibit_paths=["nope.png"]), "evidence-json", default_folder=self.folder)
        assert isinstance(unknown, dict)
        self.assertEqual(unknown["code"], "exhibit_unknown")

    def test_unsupported_format_lists_every_format(self) -> None:
        """GUI-REV3 J-10: the refusal names evidence-json and evidence-preview too (both servers share it)."""
        out = webapp_api.report_format(b"", "docx")
        assert isinstance(out, dict)
        for name in webapp_api.REPORT_FORMATS:
            self.assertIn(f"`{name}`", out["error"])
        self.assertIn("`evidence-json`", out["error"])
        self.assertIn("`evidence-preview`", out["error"])

    def test_malformed_choice_is_refused(self) -> None:
        for extra in ({"exhibit_paths": "a.txt"}, {"exhibit_paths": [1]}, {"submitter_side": "court"}):
            with self.subTest(extra=extra):
                out = webapp_api._report_payload(self._body(**extra), "evidence", default_folder=self.folder)
                assert isinstance(out, dict)
                self.assertEqual(webapp_api.report_error_status(out), 400, out)


if __name__ == "__main__":
    unittest.main()

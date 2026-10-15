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

    # Merge of phase0 R22-7: without pymupdf the evidence format is the 501 before any re-analysis or
    # check; these tests mock the PDF writer or check what comes before rendering, so the engine is
    # reported present (they failed in the main venv, which has no pymupdf).
    @mock.patch("deepfake_lens.pdf_backend.pymupdf_available", return_value=True)
    def test_evidence_statement_keeps_every_row(self, _pdf: Any) -> None:
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

    # Merge of phase0 R22-7: without pymupdf the evidence format is the 501 before any re-analysis or
    # check; these tests mock the PDF writer or check what comes before rendering, so the engine is
    # reported present (they failed in the main venv, which has no pymupdf).
    @mock.patch("deepfake_lens.pdf_backend.pymupdf_available", return_value=True)
    def test_web_statement_follows_the_posted_order(self, _pdf: Any) -> None:
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
        # Merge of R19-11: no side lists a statute — the defendant line "기재하지 않음(피고·피의자 측
        # 제출)" became R19-11's side-neutral "관련 법조:" lines, the same for both sides.
        for side in ("plaintiff", "defendant"):
            for entry in bodies[side]["entries"]:
                self.assertIn("관련 법조:", entry["purpose_of_proof"])
                self.assertNotIn("검토 참고", entry["purpose_of_proof"])

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

    # Merge of phase0 R22-7: without pymupdf the evidence format is the 501 before any re-analysis or
    # check; these tests mock the PDF writer or check what comes before rendering, so the engine is
    # reported present (they failed in the main venv, which has no pymupdf).
    @mock.patch("deepfake_lens.pdf_backend.pymupdf_available", return_value=True)
    def test_an_empty_unknown_or_repeated_choice_is_refused(self, _pdf: Any) -> None:
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
        """GUI-REV3 J-7 + R19-13: a chosen file deleted after the scan is a changed row (409, it
        names the file); a chosen row the server never re-analyzes (an upload) is
        exhibit_not_rederived with its reason, apart from a path it does not know."""
        chosen = next(row["path"] for row in self.rows if row["path"].endswith(".txt") and "::" not in row["path"])
        upload = dict(next(row for row in self.rows if row["path"] == chosen), path="uploaded.txt", name="uploaded.txt", source="upload")
        upload.pop("path_b64", None)
        out = webapp_api._report_payload(self._body(exhibit_paths=["uploaded.txt"], items=[*self.rows, upload]), "evidence-json", default_folder=self.folder)
        assert isinstance(out, dict), out
        self.assertEqual(webapp_api.report_error_status(out), 400)
        self.assertEqual(out["code"], "exhibit_not_rederived")
        self.assertEqual([row["path"] for row in out["not_rederived_exhibit_paths"]], ["uploaded.txt"])
        self.assertTrue(out["not_rederived_exhibit_paths"][0]["reason"])
        unknown = webapp_api._report_payload(self._body(exhibit_paths=["nope.png"]), "evidence-json", default_folder=self.folder)
        assert isinstance(unknown, dict)
        self.assertEqual(unknown["code"], "exhibit_unknown")
        (self.folder / chosen).unlink()
        gone = webapp_api._report_payload(self._body(exhibit_paths=[chosen]), "evidence-json", default_folder=self.folder)
        assert isinstance(gone, dict), gone
        self.assertEqual(webapp_api.api_status(gone), 409)
        self.assertIn(chosen, [row["path"] for row in gone["changed_rows"]])

    def test_preview_refuses_a_malformed_duplicate_too(self) -> None:
        """Merge of phase0 R20-4: a posted duplicate naming itself is a malformed request (400)
        for the GUI's evidence-preview as well as for the signed formats."""
        dup = dict(next(row for row in self.rows if row.get("result")))
        dup.update(status="duplicate", duplicate_of=dup["path"])
        rows = [dup if row["path"] == dup["path"] else row for row in self.rows]
        for fmt in ("evidence-preview", "json"):
            with self.subTest(fmt=fmt):
                out = webapp_api._report_payload(self._body(items=rows), fmt, default_folder=self.folder)
                assert isinstance(out, dict), out
                self.assertEqual(webapp_api.report_error_status(out), 400, out)
                self.assertIn("duplicate_of", out["error"])

    def test_preview_lists_a_walk_limit_row_as_the_export_does(self) -> None:
        """Merge of phase0 R21-1: the export never signs a walk-limit folder row (excluded with
        REPORT_ROW_WALK_LIMIT); the preview, built from the posted rows, lists it the same way
        and never as an exhibit or appendix row — preview equals output."""
        from deepfake_lens.scan_cache import WALK_LIMIT_REASON

        (self.folder / "sub").mkdir()
        template = next(row for row in self.rows if row["path"] == "odd.xyz")
        limit_row = dict(template, path="sub", name="sub", kind="unknown", status="skipped", size_bytes=0,
                         error=WALK_LIMIT_REASON.format(limit="폴더 20000개"), sha256=None, result=None)
        limit_row.pop("path_b64", None)
        body = self._body(items=[*self.rows, limit_row])
        signed = webapp_api._report_payload(body, "evidence-json", default_folder=self.folder)
        preview = webapp_api._report_payload(body, "evidence-preview", default_folder=self.folder)
        assert isinstance(signed, dict) and isinstance(preview, dict), (signed, preview)
        self.assertIn(("sub", webapp_api.REPORT_ROW_WALK_LIMIT), [(row["path"], row["reason"]) for row in signed["excluded_items"]])
        self.assertNotIn("sub", [row["file_path"] for row in signed.get("excluded_by_selection", [])])
        unsigned = "\n".join(preview["unsigned"])
        self.assertIn("sub", unsigned)
        self.assertIn(webapp_api.REPORT_ROW_WALK_LIMIT, unsigned)
        self.assertNotIn(WALK_LIMIT_REASON.split("(", 1)[0], json.dumps(preview["boxes"], ensure_ascii=False))

    def test_rows_posted_as_the_scan_returned_them_are_never_a_path_400(self) -> None:
        """Merge of phase0 R21-5: the GUI posts each row exactly as /api/scan returned it (JSON
        round trip). Names that are escaped, decomposed (NFD), carry "::" or a backslash, sit in a
        subfolder or inside an archive are the scan's own canonical paths — every format is 200."""
        import unicodedata
        import zipfile

        odd = self.folder / "odd"
        (odd / "깊은 곳").mkdir(parents=True)
        (odd / unicodedata.normalize("NFD", "한글 이름.txt")).write_text("분해된 이름입니다.", encoding="utf-8")
        (odd / "a::b.txt").write_text("두 쌍점 이름입니다.", encoding="utf-8")
        (odd / "back\\slash.txt").write_text("역슬래시 이름입니다.", encoding="utf-8")
        (odd / "깊은 곳" / "~tilde.txt").write_text("물결 이름입니다.", encoding="utf-8")
        with zipfile.ZipFile(odd / "묶음.zip", "w") as archive:
            archive.writestr("안/글.txt", "압축 안 글입니다.")
        options = webapp_api._web_options({"no_default_engine": ["true"], "recursive": ["true"]})
        run = scan_folder_run(odd, options)
        rows = json.loads(json.dumps(scan_payload(run.summary, run.items, run.thresholds, options)["items"]))
        self.assertGreaterEqual(len(rows), 6)
        body = json.dumps({"items": rows, "summary": {}, "scan_root": str(odd.resolve()),
                           "options": {"no_default_engine": "true", "recursive": "true"}}, ensure_ascii=False).encode("utf-8")
        for fmt in ("json", "html", "evidence-json", "evidence-preview"):
            with self.subTest(fmt=fmt):
                out = webapp_api._report_payload(body, fmt, default_folder=odd)
                if isinstance(out, dict):
                    self.assertNotIn("error", out, out.get("error"))

    def test_preview_takes_the_servers_listing_like_the_export(self) -> None:
        """Merge of phase0 R22-1: the 기록되지 않은 파일 box is the server's own listing of the folder;
        the preview (built from the posted rows, no re-analysis) uses the same listing, so a row
        left out of the post is "보고서에 행 없음" in both, and the GUI gets the listing to say so."""
        posted = [row for row in self.rows if row["path"] != "note0.txt"]
        body = self._body(items=posted, exhibit_paths=[posted[0]["path"]], submitter_side="plaintiff")
        preview = webapp_api._report_payload(body, "evidence-preview", default_folder=self.folder)
        signed = webapp_api._report_payload(body, "evidence-json", default_folder=self.folder)
        assert isinstance(preview, dict) and isinstance(signed, dict), (preview, signed)
        self.assertEqual(preview["unrecorded_listing"], {"source": "walk", "not_in_report": 1})
        box = dict((title, lines) for title, lines in preview["boxes"])["기록되지 않은 파일"]
        self.assertEqual(box, signed["unrecorded_files"]["lines"])
        self.assertEqual(signed["unrecorded_files"]["listing"], preview["unrecorded_listing"])
        self.assertTrue(any(line.startswith("보고서에 행 없음") for line in box), box)
        uploads_only = webapp_api._report_payload(
            json.dumps({"items": [], "summary": {}, "options": {"no_default_engine": "true"}}).encode(), "evidence-preview", default_folder=self.folder)
        if isinstance(uploads_only, dict) and "unrecorded_listing" in uploads_only:
            self.assertEqual(uploads_only["unrecorded_listing"]["source"], "unknown")

    def test_preview_draws_glyphless_names_as_the_pdf_does(self) -> None:
        """Merge of phase0 R23-4: the evidence PDF prints a character its font cannot draw (Hebrew …)
        as its \\uNNNN escape; the preview gives the same text and says it did (pdf_glyph_escapes),
        while the row identity (file_path) stays the real name."""
        from deepfake_lens.pdf_backend import import_pymupdf, pymupdf_available

        if not pymupdf_available():
            self.skipTest("pymupdf not installed")
        name = "note \u05e2\u05d1\u05e8\u05d9\u05ea.txt"
        (self.folder / name).write_text("히브리 이름 파일입니다.", encoding="utf-8")
        _, _, payload = _scan(self.folder)
        rows = payload["items"]
        body = json.dumps({"items": rows, "summary": payload["summary"], "scan_root": str(self.folder.resolve()),
                           "options": {"no_default_engine": "true"}, "exhibit_paths": [name], "submitter_side": "plaintiff",
                           "case_no": "1", "case_name": "x"}, ensure_ascii=False).encode("utf-8")
        preview = webapp_api._report_payload(body, "evidence-preview", default_folder=self.folder)
        assert isinstance(preview, dict), preview
        self.assertTrue(preview["pdf_glyph_escapes"])
        [entry] = preview["entries"]
        self.assertEqual(entry["file_path"], name)
        self.assertIn("note \\u05e2\\u05d1\\u05e8\\u05d9\\u05ea.txt", entry["document_name"])
        pdf = webapp_api._report_payload(body, "evidence", default_folder=self.folder)
        assert isinstance(pdf, bytes), pdf
        with import_pymupdf().open(stream=pdf, filetype="pdf") as doc:
            text = "".join("".join(page.get_text().split()) for page in doc)
        self.assertIn("".join(entry["document_name"].split()), text)

    def test_preview_excludes_a_cancelled_archive_like_the_export(self) -> None:
        """Merge of phase0 R24-1: in a report of a cancelled scan an archive row of status "cancelled" is
        excluded with REPORT_ROW_CANCELLED (its analyzed members are signed as rows); the preview, built
        without re-analysis, excludes it the same way — preview equals output."""
        # Merge of phase0 R25-1 (adapted): a "cancelled" row is excluded only when the server's own cancelled scan
        # job holds it as a cancelled container — the job is registered here; posted without it, it is a 400 for
        # the preview as for every other format.
        import time

        rows = [dict(row, status="cancelled") if row["path"] == "evid.zip" else row for row in self.rows]
        summary = dict(self.summary, cancelled=True, files_cancelled=1)
        no_job = webapp_api._report_payload(self._body(items=rows, summary=summary), "evidence-preview", default_folder=self.folder)
        assert isinstance(no_job, dict)
        self.assertEqual(webapp_api.report_error_status(no_job), 400, no_job)
        result = {"scan_root": webapp_api.scan_root_text(self.folder.resolve()), "items": rows, "summary": summary}
        webapp_api._SCAN_JOBS["gui-cancelled"] = {"status": "cancelled", "created": time.time(), "finished": time.time(), "result": result}
        self.addCleanup(webapp_api._SCAN_JOBS.pop, "gui-cancelled", None)
        body = self._body(items=rows, summary=summary, scan_job="gui-cancelled", exhibit_paths=[self.rows[0]["path"]], submitter_side="plaintiff")
        signed = webapp_api._report_payload(body, "evidence-json", default_folder=self.folder)
        preview = webapp_api._report_payload(body, "evidence-preview", default_folder=self.folder)
        assert isinstance(signed, dict) and isinstance(preview, dict), (signed, preview)
        self.assertIn(("evid.zip", webapp_api.REPORT_ROW_CANCELLED), [(row["path"], row["reason"]) for row in signed["excluded_items"]])
        unsigned = "\n".join(preview["unsigned"])
        self.assertIn("evid.zip", unsigned)
        self.assertIn(webapp_api.REPORT_ROW_CANCELLED.split("(", 1)[0], unsigned)
        self.assertEqual(dict(preview["boxes"])["기록되지 않은 파일"], signed["unrecorded_files"]["lines"])

    def test_preview_case_fields_are_drawn_as_the_pdf_draws_them(self) -> None:
        """Merge of phase0 R24-7: the case fields go through display_name in the PDF (a literal backslash
        doubled); the preview shows the same lines, so "ע corp" and a typed "\\u05e2 corp" differ."""
        def case_lines(plaintiff: str) -> list[str]:
            out = webapp_api._report_payload(self._body(exhibit_paths=[self.rows[0]["path"]], submitter_side="plaintiff", plaintiff=plaintiff),
                                             "evidence-preview", default_folder=self.folder)
            assert isinstance(out, dict), out
            return list(out["case_lines"])

        hebrew, typed = case_lines("\u05e2 corp"), case_lines("\\u05e2 corp")
        self.assertNotEqual(hebrew[1], typed[1])
        self.assertIn("\\\\u05e2 corp", typed[1])
        # Merge of phase0 R25-3: "|" prints as typed, and the table's 「작성자 및 일자」 cell shows the law firm in
        # the same PDF form (it was drawn as typed).
        self.assertIn("x|y", case_lines("x|y")[1])
        out = webapp_api._report_payload(self._body(exhibit_paths=[self.rows[0]["path"]], submitter_side="plaintiff", law_firm="법무법인 \\u05e2"),
                                         "evidence-preview", default_folder=self.folder)
        assert isinstance(out, dict), out
        self.assertTrue(out["entries"][0]["author_date"].startswith("법무법인 \\\\u05e2\n"), out["entries"][0]["author_date"])

    def test_excluded_rows_are_named_in_response_headers(self) -> None:
        """GUI-REV5 L-2: a successful report of any format names the rows it lists as 서명 제외
        (here an upload the server never re-analyzes) in X-Deepfake-Lens-Excluded-* headers, so
        the GUI can say so after a PDF or HTML export too; a report without such rows says 0."""
        import base64

        source = next(row for row in self.rows if row.get("result"))
        upload = dict(source, path="uploaded.png", name="uploaded.png", source="upload")
        upload.pop("path_b64", None)
        for fmt in ("html", "json"):
            with self.subTest(fmt=fmt):
                status, _, _, headers = webapp_api.report_http_response(self._body(items=[*self.rows, upload]), fmt, default_folder=self.folder)
                self.assertEqual(status, 200)
                self.assertEqual(headers[webapp_api.EXCLUDED_COUNT_HEADER], "1")
                listed = json.loads(base64.urlsafe_b64decode(headers[webapp_api.EXCLUDED_ROWS_HEADER]))
                self.assertEqual([row["path"] for row in listed], ["uploaded.png"])
                self.assertTrue(listed[0]["reason"])
        status, _, _, headers = webapp_api.report_http_response(self._body(), "html", default_folder=self.folder)
        self.assertEqual((status, headers[webapp_api.EXCLUDED_COUNT_HEADER]), (200, "0"))
        refused, _, _, headers = webapp_api.report_http_response(self._body(exhibit_paths=["nope.png"]), "evidence-json", default_folder=self.folder)
        self.assertEqual(refused, 400)
        self.assertNotIn(webapp_api.EXCLUDED_COUNT_HEADER, headers)

    def test_unsupported_format_lists_every_format(self) -> None:
        """GUI-REV3 J-10: the refusal names evidence-json and evidence-preview too (both servers share it)."""
        out = webapp_api.report_format(b"", "docx")
        assert isinstance(out, dict)
        for name in webapp_api.REPORT_FORMATS:
            self.assertIn(f"`{name}`", out["error"])
        self.assertIn("`evidence-json`", out["error"])
        self.assertIn("`evidence-preview`", out["error"])

    # Merge of phase0 R22-7: without pymupdf the evidence format is the 501 before any re-analysis or
    # check; these tests mock the PDF writer or check what comes before rendering, so the engine is
    # reported present (they failed in the main venv, which has no pymupdf).
    @mock.patch("deepfake_lens.pdf_backend.pymupdf_available", return_value=True)
    def test_malformed_choice_is_refused(self, _pdf: Any) -> None:
        for extra in ({"exhibit_paths": "a.txt"}, {"exhibit_paths": [1]}, {"submitter_side": "court"}):
            with self.subTest(extra=extra):
                out = webapp_api._report_payload(self._body(**extra), "evidence", default_folder=self.folder)
                assert isinstance(out, dict)
                self.assertEqual(webapp_api.report_error_status(out), 400, out)


if __name__ == "__main__":
    unittest.main()

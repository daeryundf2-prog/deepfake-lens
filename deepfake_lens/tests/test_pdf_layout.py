"""G2 (round 5): the Korean PDF reports lose no text.

The verifier found, on the forensic PDF (CLI, web and API) of a hostile
folder: 18 lines running past the 595 pt page edge (the summary line cut at
"…판단 불가 29건(검사", the engine version and the notice sentences cut), the
결론 column painted over by the 등급 column, "어휘 0" under "[결정]"; and on
the evidence-statement PDF an empty "무결성/해석 고지" box (insert_textbox
overflow writes nothing — the in-sample caveat, the hash count and the
disclaimer were lost) and a body SHA-256 showing 62 of its 64 characters.

These tests render both PDFs for a hostile folder (archive with a generator
member and traversal/symlink members, a zip bomb, nested zip, tar.gz,
symbolic links in and out of the folder, very long Korean and Latin file
names, broken files, a text file) and check, with
``page.get_text("dict")``:

* every span lies inside the page minus :data:`pdf_layout.PAGE_MARGINS`;
* no two spans overlap (cells never paint over each other);
* the full 64-hex signed-body SHA-256 is on one line, every row's full
  64-hex file SHA-256 is present, every row's full path is present;
* the summary line, the notice box texts and the signature lines are
  present in full.

The ``layout`` unit tests check :class:`pdf_layout.PdfLayout` itself.
"""

from __future__ import annotations

import importlib.util
import io
import re
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None
HAVE_NUMPY = importlib.util.find_spec("numpy") is not None

# Two spans "overlap" when their boxes intersect by more than this in both
# directions (pt) — adjacent lines of one cell touch but do not overlap.
OVERLAP_TOLERANCE = 0.6
# Span boxes may exceed the measured line box by rounding only.
MARGIN_TOLERANCE = 0.5
LONG_KOREAN_NAME = "아주아주긴한국어파일이름으로열이넘치는지확인하는증거사진파일_" * 2 + ".png"  # < 255 bytes (UTF-8)
LONG_LATIN_NAME = "very_long_latin_file_name_without_any_spaces_to_force_character_wrapping_" * 2 + ".jpg"


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def write_hostile_pdf_folder(folder: Path) -> Path:
    from deepfake_lens.tests.qa.test_qa_out import write_hostile_folder

    write_hostile_folder(folder)
    a1111 = (folder / "a1111.png").read_bytes()
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("deep/level/generated_image_from_a_long_pipeline.png", a1111)
    with zipfile.ZipFile(folder / "nested.zip", "w") as zf:
        zf.writestr("mid.zip", inner.getvalue())
        zf.writestr("readme.txt", "메모")
    with tarfile.open(folder / "gen.tar.gz", "w:gz") as tf:
        info = tarfile.TarInfo("x/gen.png")
        info.size = len(a1111)
        tf.addfile(info, io.BytesIO(a1111))
    (folder / LONG_KOREAN_NAME).write_bytes(a1111)
    (folder / LONG_LATIN_NAME).write_bytes(b"\xff\xd8\xff\xe0 truncated jpeg")
    (folder / "empty.jpg").write_bytes(b"")
    (folder / "essay.txt").write_text("As an AI language model, I think this essay is fine. " * 8, encoding="utf-8")
    (folder / "in_link.png").symlink_to(folder / "a1111.png")
    return folder


def _spans(page: Any) -> list[tuple[Any, str]]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if span["text"].strip():
                    out.append((span["bbox"], span["text"]))
    return out


@unittest.skipUnless(HAVE_PYMUPDF and HAVE_NUMPY, "pymupdf (and numpy for the fixture) — the venv_api / extras run covers this")
class HostileFolderPdfTest(unittest.TestCase):
    """G2: the forensic and evidence-statement PDFs of a hostile folder keep every character on the page."""

    _tmp: tempfile.TemporaryDirectory[str]
    folder: Path
    summary: Any
    items: list[Any]
    thresholds: Any
    out: Path

    @classmethod
    def setUpClass(cls) -> None:
        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder_run

        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name).resolve()
        cls.folder = write_hostile_pdf_folder(base / "case")
        run = scan_folder_run(cls.folder, AnalysisOptions(recursive=True))
        cls.summary, cls.items, cls.thresholds = run.summary, run.items, run.thresholds
        cls.out = base / "out"
        cls.out.mkdir()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _open(self, path: Path) -> Any:
        from deepfake_lens.pdf_backend import import_pymupdf

        return import_pymupdf().open(str(path))

    def _assert_layout(self, path: Path) -> str:
        """Every span inside the margins, no two spans overlapping; returns the text."""
        from deepfake_lens.pdf_layout import PAGE_MARGINS

        left, top, right, bottom = PAGE_MARGINS
        problems: list[str] = []
        texts: list[str] = []
        with self._open(path) as doc:
            for number, page in enumerate(doc, 1):
                area = page.rect
                inner = (area.x0 + left - MARGIN_TOLERANCE, area.y0 + top - MARGIN_TOLERANCE,
                         area.x1 - right + MARGIN_TOLERANCE, area.y1 - bottom + MARGIN_TOLERANCE)
                spans = _spans(page)
                self.assertTrue(spans, f"{path.name} page {number} is empty")
                for bbox, text in spans:
                    x0, y0, x1, y1 = bbox
                    if x0 < inner[0] or y0 < inner[1] or x1 > inner[2] or y1 > inner[3]:
                        problems.append(f"{path.name} p{number}: outside margins {tuple(round(v, 1) for v in bbox)} {text[:60]!r}")
                ordered = sorted(spans, key=lambda s: s[0][1])
                for i, (a, text_a) in enumerate(ordered):
                    for b, text_b in ordered[i + 1:]:
                        if b[1] >= a[3] - OVERLAP_TOLERANCE:
                            break
                        dx = min(a[2], b[2]) - max(a[0], b[0])
                        dy = min(a[3], b[3]) - max(a[1], b[1])
                        if dx > OVERLAP_TOLERANCE and dy > OVERLAP_TOLERANCE:
                            problems.append(f"{path.name} p{number}: {text_a[:40]!r} overlaps {text_b[:40]!r}")
                texts.append(page.get_text())
        self.assertEqual(problems, [], "\n".join(problems[:30]))
        return "\n".join(texts)

    def _assert_rows_complete(self, text: str) -> None:
        from deepfake_lens.result_text import display_path

        flat = _norm(text)
        self.assertGreaterEqual(len(self.items), 14)  # every hostile row kind is present
        for item in self.items:
            self.assertIn(_norm(display_path(item.path)), flat, f"row path cut: {item.path}")
            if item.sha256:
                self.assertRegex(item.sha256, r"^[0-9a-f]{64}$")
                self.assertIn(item.sha256, flat, f"row SHA-256 cut: {item.path}")
        self.assertNotIn("…(이후 내용 생략", text)

    def test_forensic_pdf_keeps_every_line_on_the_page(self) -> None:
        from deepfake_lens.reports import signed_report_body, write_forensic_pdf_report
        from deepfake_lens.result_text import IN_SAMPLE_CAVEAT, TEXT_LEGAL_LIMITATION
        from deepfake_lens.signing import signed_body_sha256

        signed = signed_report_body(self.summary, self.items, thresholds=self.thresholds, report_format="pdf", key=b"")
        path = self.out / "forensic.pdf"
        write_forensic_pdf_report(path, self.summary, self.items, thresholds=self.thresholds, coverage={"weights_available": 0}, signed_report=signed)
        text = self._assert_layout(path)
        self._assert_rows_complete(text)
        flat = _norm(text)
        s = self.summary
        for expected in (
            f"판단 불가 {s.undetermined}건(검사 실패 {s.checks_failed}건), 미지원/오류 {s.unsupported_or_failed}건",
            "분석 엔진: Deepfake Lens v",
            IN_SAMPLE_CAVEAT,
            TEXT_LEGAL_LIMITATION,
            "사법절차 적격성 고지: 본 감정서는 신경망 가중치 미탑재 상태의 로컬 휴리스틱 분석에 따른 스크리닝 결과입니다 (뉴럴 엔진 미실행).",
            "조작·생성 근거 있음",
            "(직인생략)",
        ):
            self.assertIn(_norm(expected), flat, expected)
        from deepfake_lens.result_text import evidence_counts_text

        for item in self.items:
            if item.result is not None:
                # the 근거 종류 cell, never painted over ("어휘 0" under "[결정]")
                self.assertIn(_norm(evidence_counts_text(item.result)), flat, item.path)
        # The signed body's digest: all 64 characters, unbroken on one line.
        digest = signed_body_sha256(signed)
        self.assertIn(f"본문 SHA-256(참고, 서명 아님): {digest}", text)

    def test_evidence_statement_pdf_keeps_the_notice_box_and_the_full_digest(self) -> None:
        from deepfake_lens.core import _thresholds_json
        from deepfake_lens.evidence_statement import (
            STATEMENT_NOTICE_TITLE,
            build_evidence_statement,
            signed_statement_body,
            write_evidence_statement_pdf,
        )
        from deepfake_lens.result_text import IN_SAMPLE_CAVEAT
        from deepfake_lens.signing import signed_body_sha256

        statement = build_evidence_statement(self.items, thresholds=_thresholds_json(self.thresholds), scan_root=self.folder)
        signed = signed_statement_body(statement, key=b"")
        path = self.out / "statement.pdf"
        write_evidence_statement_pdf(path, statement, signed=signed)
        text = self._assert_layout(path)
        flat = _norm(text)
        hashed = sum(1 for entry in statement.entries if entry.sha256)
        for expected in (
            STATEMENT_NOTICE_TITLE,
            f"원본 해시 산출: {hashed}/{len(statement.entries)}건.",
            "본 문서의 결론은 자동 분석 결과로 유죄·불법성의 직접 증거가 아니며, 정밀 감정은 별도로 수행되어야 합니다.",
            IN_SAMPLE_CAVEAT,
            statement.reference_note,
            statement.court,
        ):
            self.assertTrue(expected)
            self.assertIn(_norm(expected), flat, expected)
        for entry in statement.entries:
            self.assertIn(_norm(entry.purpose_of_proof), flat, f"purpose cut: {entry.exhibit_no}")
            self.assertIn(_norm(entry.document_name), flat, f"name cut: {entry.exhibit_no}")
        digest = signed_body_sha256(signed)
        self.assertIn(f"본문 SHA-256(참고, 서명 아님): {digest}", text)


@unittest.skipUnless(HAVE_PYMUPDF, "pymupdf — the venv_api / extras run covers this")
class PdfLayoutUnitTest(unittest.TestCase):
    """G2: the measured layout helper itself."""

    def _layout(self) -> Any:
        from deepfake_lens.pdf_backend import import_pymupdf
        from deepfake_lens.pdf_layout import PdfLayout

        layout = PdfLayout(import_pymupdf())
        layout.new_page()
        return layout

    def test_wrap_keeps_every_character_and_respects_the_width(self) -> None:
        layout = self._layout()
        text = "감정 결과: 총 38개 — " + "evil.zip::" + "x" * 120 + " 판단 불가 29건(검사 실패 3건)\n둘째 줄"
        for width in (40.0, 120.0, 300.0):
            lines = layout.wrap(text, width, 7.5)
            self.assertEqual(_norm("".join(lines)), _norm(text))
            for line in lines:
                self.assertLessEqual(layout.text_width(line, 7.5), width + 1e-6, line)

    def test_columns_tile_the_content_width(self) -> None:
        layout = self._layout()
        columns = layout.columns((1, 2, 3))
        self.assertAlmostEqual(columns[0][0], layout.left)
        self.assertAlmostEqual(columns[-1][1], layout.right)
        for (a0, a1), (b0, b1) in zip(columns, columns[1:]):
            self.assertLess(a1, b0)
            self.assertLess(a0, a1)

    def test_draw_lines_grows_the_box_instead_of_dropping_text(self) -> None:
        """insert_textbox writes nothing on overflow; draw_lines retries with a taller box."""
        layout = self._layout()
        lines = ["첫째 줄", "둘째 줄", "셋째 줄"]
        layout._descent = lambda size: -size * 2  # sabotage the estimate: the first attempt overflows
        used = layout.draw_lines(layout.left, layout.right, 100.0, lines, 9.0, (0, 0, 0))
        self.assertGreater(used, 0)
        text = layout.page.get_text()
        for line in lines:
            self.assertIn(line, text)

    def test_tall_row_continues_on_the_next_page(self) -> None:
        from deepfake_lens.pdf_layout import Cell

        layout = self._layout()
        columns = layout.columns((1, 4))
        body = "\n".join(f"{n}번째 줄 — 입증취지" for n in range(200))
        layout.draw_row(columns, [Cell(0, "갑 제1호증"), Cell(1, body, 7.0)])
        self.assertGreater(layout.doc.page_count, 1)
        text = "".join(page.get_text() for page in layout.doc)
        for n in (0, 99, 199):
            self.assertIn(f"{n}번째 줄 — 입증취지", text)


if __name__ == "__main__":
    unittest.main()


# R23-4 (round 23): names in scripts the embedded font has no glyph for, in pairs that differ in one character.
GLYPHLESS_NAMES = (
    "note עברית.txt", "note עבריה.txt",  # Hebrew
    "سجل.txt", "سجن.txt",  # Arabic
    "ไทย.txt", "ไทม.txt",  # Thai
    "हिन्दी.txt", "हिन्दु.txt",  # Devanagari
    "emoji😀.txt", "emoji😁.txt",  # emoji (outside the BMP)
)
DRAWN_NAMES = ("한글.txt", "日本語.txt", "ascii.txt")


@unittest.skipUnless(HAVE_PYMUPDF, "pymupdf — the venv_api / extras run covers this")
class PdfGlyphlessCharactersTest(unittest.TestCase):
    """R23-4 (round 23): the embedded CJK font (Droid Sans Fallback) has no glyph for Hebrew, Arabic, Thai, Devanagari or
    emoji; PyMuPDF drew an empty glyph, so the two Hebrew names both printed (text extraction) as ``note \\x00\\x00\\x00\\x00\\x00.txt``,
    the two emoji names as ``emoji\\x00.txt`` and the Arabic and Thai names alike, in the forensic PDF and the evidence
    statement of the CLI and the web — the same evidence-file names were right in HTML and JSON. A character without a glyph
    is now printed as its escape (``\\u05e2``, ``\\U0001f600``), the escapes display_name uses, so every name stays distinct
    and readable."""

    def _layout(self) -> Any:
        from deepfake_lens.pdf_backend import import_pymupdf
        from deepfake_lens.pdf_layout import PdfLayout

        return PdfLayout(import_pymupdf())

    @staticmethod
    def _expected(layout: Any, name: str) -> str:
        from deepfake_lens.result_text import display_name

        return layout.drawable(display_name(name))

    def test_drawable_escapes_only_what_the_font_cannot_draw(self) -> None:
        layout = self._layout()
        self.assertEqual(layout.drawable("note עברית.txt"), "note \\u05e2\\u05d1\\u05e8\\u05d9\\u05ea.txt")
        self.assertEqual(layout.drawable("emoji😀.txt"), "emoji\\U0001f600.txt")
        for name in DRAWN_NAMES:
            self.assertEqual(layout.drawable(name), name)
        self.assertEqual(layout.drawable("줄 하나\n줄 둘"), "줄 하나\n줄 둘")
        shown = [self._expected(layout, name) for name in GLYPHLESS_NAMES]
        self.assertEqual(len(set(shown)), len(GLYPHLESS_NAMES))
        for text in shown:
            self.assertTrue(all(char == " " or layout._has_glyph(char) for char in text), text)
        # injective with display_name: a literal "\u05e2" in a name is shown with its backslash doubled
        self.assertNotEqual(self._expected(layout, "\\u05e2.txt"), self._expected(layout, "ע.txt"))
        # wrap measures and returns the drawable form (every character kept)
        lines = layout.wrap("note עברית.txt " * 6, 80.0, 7.5)
        self.assertEqual("".join(lines).replace(" ", ""), layout.drawable("note עברית.txt " * 6).replace(" ", ""))

    def _pdf_text(self, path_or_bytes: Path | bytes) -> str:
        from deepfake_lens.pdf_backend import import_pymupdf

        pymupdf = import_pymupdf()
        doc = pymupdf.open(stream=path_or_bytes, filetype="pdf") if isinstance(path_or_bytes, bytes) else pymupdf.open(str(path_or_bytes))
        try:
            return "".join(page.get_text() for page in doc)
        finally:
            doc.close()

    def _assert_names(self, label: str, text: str, names: tuple[str, ...]) -> None:
        layout = self._layout()
        flat = "".join(text.split())  # a wrapped name may break at its space
        with self.subTest(pdf=label):
            self.assertNotIn("\x00", text)
            self.assertNotIn("\ufffd", text)
            for name in names:
                self.assertIn("".join(self._expected(layout, name).split()), flat, name)

    def test_case_fields_stay_distinct_in_every_pdf(self) -> None:
        """R24-7 (round 24): the evidence statement's case fields (and the forensic PDF's office, contact and exhibit
        number) reached PdfLayout as typed — ``--plaintiff "ע corp"`` and ``--plaintiff "\\u05e2 corp"`` (a literal
        backslash) both printed 「원고(고소인)    \\u05e2 corp」: R23-4's injectivity held for file names (display_name)
        only. The fields now go through display_name too (a literal backslash doubled)."""
        import contextlib
        import json
        import os
        from unittest import mock

        from deepfake_lens import webapp_api
        from deepfake_lens.cli import main as cli_main

        layout = self._layout()
        pairs = ("ע corp", "\\u05e2 corp")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            folder = base / "case"
            folder.mkdir()
            sample = folder / "a.txt"
            sample.write_text("사람이 쓴 짧은 메모입니다. " * 4, encoding="utf-8")
            texts: dict[str, list[str]] = {}
            env = mock.patch.dict(os.environ, {"HOME": str(base / "home")})
            with env, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                for index, value in enumerate(pairs):
                    statement = base / f"statement{index}.pdf"
                    self.assertEqual(cli_main([
                        "evidence-statement", str(sample), "--pdf-out", str(statement), "--plaintiff", value, "--defendant", value,
                        "--case-no", value, "--case-name", value, "--court", value, "--law-firm", value, "--contact", value,
                        "--center", value,
                    ]), 0)
                    texts.setdefault("cli evidence-statement", []).append(self._pdf_text(statement))
                    forensic = base / f"forensic{index}.pdf"
                    self.assertEqual(cli_main([
                        "scan", str(folder), "--forensic-pdf-out", str(forensic), "--exhibit-no", value, "--law-firm", value, "--contact", value,
                    ]), 0)
                    texts.setdefault("cli forensic", []).append(self._pdf_text(forensic))
                    scan_statement = base / f"scan-statement{index}.pdf"  # R25-3: scan's evidence statement too
                    self.assertEqual(cli_main([
                        "scan", str(folder), "--evidence-statement-pdf-out", str(scan_statement), "--law-firm", value, "--contact", value,
                    ]), 0)
                    texts.setdefault("cli scan evidence-statement", []).append(self._pdf_text(scan_statement))
            out = io.StringIO()
            with env, contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli_main(["scan", str(folder), "--format", "json"]), 0)
            rows = json.loads(out.getvalue())["items"]
            with mock.patch.object(webapp_api, "_READ_ROOTS", type(webapp_api._READ_ROOTS)()):
                webapp_api.configure_read_roots(folder)
                for value in pairs:
                    for fmt, fields in (("evidence", ("plaintiff", "defendant", "case_no", "case_name", "court", "law_firm", "contact", "center")),
                                        ("pdf", ("exhibit_no", "law_firm", "contact"))):
                        body = json.dumps({"items": rows, "scan_root": str(folder), "format": fmt, **{field: value for field in fields}}).encode("utf-8")
                        raw = webapp_api._report_payload(body, default_folder=folder)
                        assert isinstance(raw, bytes), raw
                        texts.setdefault(f"web {fmt}", []).append(self._pdf_text(raw))
        hebrew, literal = (layout.drawable(display) for display in (self._expected(layout, value) for value in pairs))
        self.assertEqual(hebrew, "\\u05e2 corp")
        self.assertEqual(literal, "\\\\u05e2 corp")
        for label, (first, second) in texts.items():
            with self.subTest(pdf=label):
                flat_first, flat_second = "".join(first.split()), "".join(second.split())
                self.assertIn("\\u05e2corp", flat_first)
                self.assertNotIn("\\\\u05e2corp", flat_first)
                self.assertIn("\\\\u05e2corp", flat_second)  # the literal backslash doubled: the two PDFs differ
                self.assertNotEqual(flat_first.count("\\\\u05e2"), flat_second.count("\\\\u05e2"))
                # R25-3 (round 25): every print of the literal value has its backslash doubled — the evidence statement's
                # 「작성자 및 일자」 cell drew the law firm as typed, so it printed both values as "\u05e2 corp"
                self.assertEqual(re.findall(r"(?<!\\)\\u05e2corp", flat_second), [], "a literal value printed with one backslash")

    def test_pipes_print_as_typed_and_nfd_stays_distinct_in_the_case_fields(self) -> None:
        """R25-3 (round 25): the PDFs printed a case field's "|" as the Markdown escape "\\|" (a PDF has no table) — it now
        prints as typed, and a literal "\\|" still prints with its backslash doubled. An NFD value (macOS) stays escapes,
        distinct from the NFC value (normalizing would print both alike — documented in result_text.pdf_field_text)."""
        import contextlib
        import os
        import unicodedata
        from unittest import mock

        from deepfake_lens.cli import main as cli_main
        from deepfake_lens.result_text import pdf_field_text

        nfc = "한국 법무법인"
        nfd = unicodedata.normalize("NFD", nfc)
        self.assertEqual(pdf_field_text("x|y"), "x|y")
        self.assertEqual(pdf_field_text("x\\|y"), "x\\\\|y")
        self.assertNotEqual(pdf_field_text(nfd), pdf_field_text(nfc))
        self.assertEqual(pdf_field_text(nfc), nfc)
        from deepfake_lens.reports import SCAN_ROOT_LABEL, scan_root_line

        self.assertEqual(scan_root_line({"scan_root": "case\\u05e2"}), f"{SCAN_ROOT_LABEL}: case\\\\u05e2")  # the folder name too
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            sample = base / "a.txt"
            sample.write_text("사람이 쓴 짧은 메모입니다. " * 4, encoding="utf-8")
            texts = []
            with mock.patch.dict(os.environ, {"HOME": str(base / "home")}), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                for index, firm in enumerate(("x|y 법인", nfd, nfc)):
                    out = base / f"s{index}.pdf"
                    self.assertEqual(cli_main(["evidence-statement", str(sample), "--pdf-out", str(out), "--law-firm", firm,
                                               "--plaintiff", firm]), 0)
                    texts.append("".join(self._pdf_text(out).split()))
        self.assertIn("x|y법인", texts[0])
        self.assertNotIn("x\\|y", texts[0])
        self.assertEqual(texts[0].count("x|y법인"), 4)  # plaintiff, header, the 작성자 cell, signoff
        self.assertNotIn("한국법무법인", texts[1])
        self.assertIn("\\u1112\\u1161\\u11ab", texts[1])
        self.assertEqual(texts[2].count("한국법무법인"), 4)

    def test_every_pdf_path_prints_distinct_readable_names(self) -> None:
        import contextlib
        import json
        import os
        from unittest import mock

        from deepfake_lens import webapp_api
        from deepfake_lens.cli import main as cli_main

        names = (*GLYPHLESS_NAMES, *DRAWN_NAMES)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            folder = base / "case"
            folder.mkdir()
            for index, name in enumerate(names):
                (folder / name).write_text(f"사람이 쓴 짧은 메모 {index}입니다. " * 4, encoding="utf-8")
            out = base / "out"
            out.mkdir()
            texts: dict[str, str] = {}
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"HOME": str(base / "home")}), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli_main([
                    "scan", str(folder), "--json-out", str(out / "scan.json"), "--pdf-out", str(out / "scan.pdf"),
                    "--forensic-pdf-out", str(out / "forensic.pdf"), "--evidence-statement-pdf-out", str(out / "statement.pdf"),
                ]), 0)
                for index, name in enumerate(GLYPHLESS_NAMES[:2]):
                    self.assertEqual(cli_main(["evidence-statement", str(folder / name), "--pdf-out", str(out / f"single{index}.pdf")]), 0)
            for label in ("scan", "forensic", "statement"):
                texts[f"cli {label}"] = self._pdf_text(out / f"{label}.pdf")
            for index, name in enumerate(GLYPHLESS_NAMES[:2]):
                self._assert_names(f"cli evidence-statement {index}", self._pdf_text(out / f"single{index}.pdf"), (name,))
            rows = json.loads((out / "scan.json").read_text(encoding="utf-8"))["items"]
            roots = mock.patch.object(webapp_api, "_READ_ROOTS", type(webapp_api._READ_ROOTS)())
            with roots:
                webapp_api.configure_read_roots(folder)
                for fmt in ("pdf", "evidence"):
                    body = json.dumps({"items": rows, "scan_root": str(folder), "format": fmt}).encode("utf-8")
                    raw = webapp_api._report_payload(body, default_folder=folder)
                    self.assertIsInstance(raw, bytes, raw)
                    assert isinstance(raw, bytes)
                    texts[f"web {fmt}"] = self._pdf_text(raw)
        for label, text in texts.items():
            self._assert_names(label, text, names)

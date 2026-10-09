"""Round-4 rendering findings: Korean labels and full archive-member names.

B2  HTML report: threshold provenance line and column headers in Korean.
B3  Coverage / row statuses rendered with the Korean labels (실행/미실행/
    실패, 건너뜀…) in HTML, forensic PDF, evidence statement and CLI table;
    every forensic-PDF label containing Hangul drawn with the CJK font.
B4  legal-report text: evidence qualifiers, statuses, check names in Korean.
S1  Archive members shown as "<container>::<member>" everywhere, and the
    container row's cross-reference names member rows that exist.
S2  Forensic PDF symlink row: the evidence statement's "why no hash" text.
S6  Evidence statement provenance carries the in-sample caveat.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

from deepfake_lens.analysis_api import AnalysisOptions, scan_folder_run
from deepfake_lens.result_types import BatchScanSummary, ScanItem
from deepfake_lens.result_text import HASH_UNAVAILABLE_SYMLINK, IN_SAMPLE_CAVEAT

HAVE_PIL = importlib.util.find_spec("PIL") is not None and importlib.util.find_spec("numpy") is not None
HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None
MEMBER = "evil.zip::inner/a1111.png"


def write_case(folder: Path) -> Path:
    """A generated PNG, a zip holding it under inner/, a symlink, a text file."""
    from PIL import Image, PngImagePlugin

    folder.mkdir(parents=True, exist_ok=True)
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "a cat\nNegative prompt: x\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, Size: 256x256, Model: sd15")
    import numpy as np

    rng = np.random.default_rng(3)
    Image.fromarray(rng.integers(0, 255, (256, 256, 3), dtype=np.uint8)).save(folder / "a1111.png", pnginfo=info)
    with zipfile.ZipFile(folder / "evil.zip", "w") as archive:
        archive.writestr("inner/a1111.png", (folder / "a1111.png").read_bytes())
        archive.writestr("notes.txt", "메모입니다.")
    (folder / "memo.txt").write_text("오늘 회의 메모입니다.", encoding="utf-8")
    if hasattr(os, "symlink"):
        with contextlib.suppress(OSError):
            (folder / "link.png").symlink_to("a1111.png")
    return folder


def html_text(path: Path) -> str:
    """Visible text of an HTML file (scripts and styles dropped)."""
    from html.parser import HTMLParser

    class _Text(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.skip = 0
            self.parts: list[str] = []

        def handle_starttag(self, tag: str, attrs: Any) -> None:
            if tag in ("script", "style"):
                self.skip += 1

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style"):
                self.skip -= 1

        def handle_data(self, data: str) -> None:
            if not self.skip and data.strip():
                self.parts.append(data.strip())

    parser = _Text()
    parser.feed(path.read_text(encoding="utf-8"))
    return "\n".join(parser.parts)


@unittest.skipUnless(HAVE_PIL, "Pillow + numpy needed for the fixture")
class RenderedLabelsTest(unittest.TestCase):
    _tmp: tempfile.TemporaryDirectory[str]
    folder: Path
    summary: BatchScanSummary
    items: list[ScanItem]
    thresholds: Any
    paths: set[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.folder = write_case(Path(cls._tmp.name).resolve() / "case")
        run = scan_folder_run(cls.folder, AnalysisOptions(recursive=True))
        cls.summary, cls.items, cls.thresholds = run.summary, run.items, run.thresholds
        cls.paths = {item.path for item in cls.items}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_fixture_has_member_container_and_symlink_rows(self) -> None:
        self.assertIn(MEMBER, self.paths)
        self.assertIn("evil.zip", self.paths)
        if (self.folder / "link.png").is_symlink():
            self.assertIn("skipped", {item.status for item in self.items if item.path == "link.png"})

    def test_html_report_is_korean_and_names_members_in_full(self) -> None:
        from deepfake_lens.reports import write_html_report

        for redact in (False, True):
            with self.subTest(redact_paths=redact), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "r.html"
                write_html_report(out, self.summary, self.items, redact_paths=redact, thresholds=self.thresholds)
                raw = out.read_text(encoding="utf-8")
                text = html_text(out)
                self.assertNotIn("Decision thresholds", text)  # B2
                self.assertIn("판정 임계값", text)
                self.assertIn(IN_SAMPLE_CAVEAT, text)  # packaged thresholds are in-sample
                self.assertIn("<th>히트맵</th>", raw)
                self.assertNotIn(">heatmap<", raw)
                self.assertIn(MEMBER, text)  # S1 (also when redacted)
                for raw_status in ("skipped", "unsupported", "failed", "duplicate"):
                    self.assertNotIn(f"<td>{raw_status}</td>", raw)  # B3
                if (self.folder / "link.png").is_symlink():
                    self.assertIn("<td>건너뜀</td>", raw)

    def test_evidence_statement_members_reference_and_provenance(self) -> None:
        from deepfake_lens.core import _thresholds_json
        from deepfake_lens.evidence_statement import build_evidence_statement

        statement = build_evidence_statement(self.items, thresholds=_thresholds_json(self.thresholds), scan_root=self.folder)
        names = {entry.document_name for entry in statement.entries}
        self.assertIn(f"디지털 증거 파일 ({MEMBER}) 및 AI 스크리닝 데이터", names)  # S1
        exhibits = {entry.exhibit_no: entry for entry in statement.entries}
        container = next(entry for entry in statement.entries if entry.file_path == "evil.zip")
        self.assertNotIn("::경로", container.purpose_of_proof)
        # Every "갑 제N호증(label)" reference points at an existing row with that label.
        refs = re.findall(r"(갑 제\d+호증)\(([^()]+)\)", container.purpose_of_proof)
        self.assertTrue(refs, container.purpose_of_proof)
        for exhibit_no, label in refs:
            self.assertIn(exhibit_no, exhibits)
            self.assertIn(f"({label})", exhibits[exhibit_no].document_name)
        self.assertIn(MEMBER, [label for _, label in refs])
        self.assertIn(IN_SAMPLE_CAVEAT, statement.provenance_note)  # S6
        self.assertIn(IN_SAMPLE_CAVEAT, statement.to_markdown())
        if (self.folder / "link.png").is_symlink():
            link = next(entry for entry in statement.entries if entry.file_path == "link.png")
            self.assertIn(HASH_UNAVAILABLE_SYMLINK, link.purpose_of_proof)
            self.assertIn("상태: 건너뜀", link.purpose_of_proof)  # B3

    def test_cli_table_is_korean(self) -> None:
        from deepfake_lens.cli_render import _print_table

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_table(self.summary, self.items, include_low=True, thresholds=self.thresholds)
        text = out.getvalue()
        self.assertIn("유형", text)
        self.assertNotRegex(text, r"\bkind\b|\bfile\b")
        self.assertIn(MEMBER, text)
        self.assertIn("이미지", text)
        for raw_status in (" skipped ", " ran ", " failed "):
            self.assertNotIn(raw_status, text)

    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf not installed")
    def test_forensic_pdf_labels_fonts_members_and_symlink_hash(self) -> None:
        from deepfake_lens.pdf_backend import import_pymupdf
        from deepfake_lens.reports import write_forensic_pdf_report

        pymupdf = import_pymupdf()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "f.pdf"
            write_forensic_pdf_report(out, self.summary, self.items, thresholds=self.thresholds, redact_paths=True)
            doc = pymupdf.open(str(out))
            text = "\n".join(page.get_text() for page in doc)
            spans = [
                span
                for page in doc
                for block in page.get_text("dict")["blocks"]
                for line in block.get("lines", [])
                for span in line["spans"]
            ]
            doc.close()
        self.assertIn("문서 번호", text)  # B3: drawn with the CJK font, extractable
        self.assertIn(MEMBER, text)  # S1
        # the provenance line wraps inside its box: compare without whitespace
        self.assertIn(re.sub(r"\s+", "", IN_SAMPLE_CAVEAT), re.sub(r"\s+", "", text))
        self.assertNotRegex(text, r"\b(?:skipped|unsupported|duplicate)\b")
        if (self.folder / "link.png").is_symlink():
            # G2: cell text now wraps inside its measured column (it ran off
            # the page before), so the phrase is compared without whitespace.
            self.assertIn(re.sub(r"\s+", "", HASH_UNAVAILABLE_SYMLINK), re.sub(r"\s+", "", text))  # S2
            self.assertIn("건너뜀", text)
        latin = [span for span in spans if "helv" in span["font"].lower() or "helvetica" in span["font"].lower()]
        self.assertTrue(all(span["text"].isascii() for span in latin), [span["text"] for span in latin if not span["text"].isascii()])


class LegalReportTextTest(unittest.TestCase):
    """B4: the legal-report text renders codes as Korean labels."""

    def test_codes_are_korean(self) -> None:
        from deepfake_lens.enhanced_forensics import legal_report_text
        from deepfake_lens.result_types import register_model_display_name

        register_model_display_name("raw-model-id", "한국어 모델 이름")
        report = {
            "report_id": "LR-1",
            "file": {"path": "a.png", "sha256": "ab" * 32, "size_bytes": 1234},
            "conclusion": {"verdict_code": "manipulation_evidence", "verdict_label": "조작·생성 근거 있음", "grade_label": "감정 근거로 사용 가능"},
            "evidence": [
                {"kind": "deterministic", "direction": "synthetic", "strength": "strong", "title": "생성 도구 메타데이터", "detail": "A1111"},
                {"kind": "statistical", "direction": "authentic", "strength": "moderate", "title": "모델", "detail": "-"},
                {"kind": "lexical", "direction": "neutral", "strength": "weak", "title": "어휘", "detail": "-"},
            ],
            "coverage": [
                {"check": "metadata", "status": "ran", "reason": ""},
                {"check": "archive_member", "status": "skipped", "reason": "심볼릭 링크 멤버"},
                {"check": "model:raw-model-id", "status": "failed", "reason": "RuntimeError: boom"},
            ],
            "limitations": [],
            "legal_notes": [],
            "signature_note": "서명 없음",
        }
        text = legal_report_text(report)
        for expected in (
            "[결정적/합성/강]", "[통계적/원본/중]", "[어휘적/중립/약]",
            "- 메타데이터: 실행", "- 압축 구성 파일: 미실행 — 심볼릭 링크 멤버", "- 외부 모델(한국어 모델 이름): 실패 — RuntimeError: boom",
            "1234 바이트",
        ):
            self.assertIn(expected, text)
        for raw in ("deterministic", "synthetic", "strong", "skipped", ": ran", "archive_member", "raw-model-id", "bytes", "manipulation_evidence"):
            self.assertNotIn(raw, text)


class AnalystAndReviewLabelsTest(unittest.TestCase):
    """G16 (round 5): "분석자: system" and the GUI review options in English."""

    def test_default_analyst_is_shown_as_automatic_system(self) -> None:
        from deepfake_lens.enhanced_forensics import analyst_label, legal_report_text

        self.assertEqual(analyst_label("system"), "시스템(자동)")
        self.assertEqual(analyst_label(""), "시스템(자동)")
        self.assertEqual(analyst_label("김감정"), "김감정")
        text = legal_report_text({"analyst_id": "system", "file": {}, "conclusion": {}})
        self.assertIn("분석자: 시스템(자동)", text)
        self.assertNotIn("분석자: system", text)

    def test_gui_review_options_are_korean_only(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "gui.js").read_text(encoding="utf-8")
        options = re.findall(r'<option value="(unreviewed|synthetic|authentic|inconclusive)">([^<]*)</option>', source)
        self.assertEqual(
            options,
            [("unreviewed", "미검토"), ("synthetic", "인공합성 의심"), ("authentic", "원본 정상"), ("inconclusive", "판단 보류")],
        )
        for english in ("(Pending)", "(Synthetic)", "(Authentic)", "(Inconclusive)"):
            self.assertNotIn(english, source)


class AnalysisResultTextTest(unittest.TestCase):
    """G1 (round 5): the analysis_result text rendering uses labels only."""

    def test_codes_render_as_korean_labels(self) -> None:
        from unittest import mock

        from deepfake_lens import result_types
        from deepfake_lens.cli_standalone import format_analysis_result

        payload = {
            "path": "a.png",
            "verdict_code": "manipulation_evidence",
            "grade": "reference",
            "evidence": [{"title": "생성 도구 메타데이터", "detail": "d", "kind": "deterministic", "direction": "synthetic", "strength": "strong"}],
            "coverage": [
                {"check": "metadata", "status": "ran"},
                {"check": "model:Swin-large AI-vs-human image detector (umm-maybe)", "status": "skipped", "reason": "모델 실행 불가"},
            ],
        }
        # The display-name registry is empty in a fresh process (a report
        # re-rendered from JSON): the packaged profiles still name the model.
        with mock.patch.dict(result_types._MODEL_DISPLAY_NAMES, {}, clear=True), mock.patch.object(result_types, "_PACKAGED_NAMES_LOADED", False):
            text = format_analysis_result(payload)
        for expected in (
            "[결론] 조작·생성 근거 있음 · 등급: 참고",
            "[결정적/합성/강] 생성 도구 메타데이터: d",
            "- 메타데이터: 실행",
            "- 외부 모델(Swin-large 생성 이미지 탐지기(umm-maybe)): 미실행 — 모델 실행 불가",
        ):
            self.assertIn(expected, text)
        for raw in ("manipulation_evidence", "deterministic", "synthetic", "strong", ": ran", "skipped", "reference", "model:"):
            self.assertNotIn(raw, text)

    def test_row_without_result_has_korean_status_and_labels(self) -> None:
        from deepfake_lens.cli_standalone import analysis_result_payload, format_analysis_result

        payload = analysis_result_payload(ScanItem("x.bin", "x.bin", "unknown", "unsupported", 3, result=None), command="forensic")
        self.assertEqual(payload["verdict_label"], "판단 불가")
        self.assertEqual(payload["grade_label"], "참고")
        self.assertIn("판단 불가 — 미지원", payload["verdict"])
        self.assertNotIn("unsupported", format_analysis_result(payload))

    def test_layer_diagnostic_heading_is_the_korean_label(self) -> None:
        from deepfake_lens.layer_diagnostic import format_layer_diagnostic, to_layer_diagnostic

        diag = to_layer_diagnostic("explain_score", {"score": 50, "available": True}, layer_label="점수 설명")
        self.assertEqual(diag["layer_label"], "점수 설명")
        text = format_layer_diagnostic(diag)
        self.assertTrue(text.splitlines()[0].endswith("] 점수 설명"), text)
        self.assertIn("  available=True", text)
        self.assertNotIn("explain_score", text)


if __name__ == "__main__":
    unittest.main()

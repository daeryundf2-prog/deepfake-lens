"""Tests for court evidence statement (증거설명서) module and litigation export."""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from deepfake_lens.cli import main
from deepfake_lens.core import (
    BatchScanSummary,
    ClassificationResult,
    EvidenceSignal,
    RiskBand,
    ScanItem,
    SourceConfidence,
    SourceGuess,
)
from deepfake_lens.evidence_statement import (
    build_evidence_statement,
    write_evidence_statement_markdown,
    write_evidence_statement_pdf,
)

HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None
HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None


class EvidenceStatementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

        sig1 = EvidenceSignal(title="안면 윤곽선 경계면 주파수 단절", detail="라플라시안 주파수 잔차 이상", weight=30)
        res1 = ClassificationResult(
            score=85,
            band=RiskBand.HIGH,
            band_label="높음",
            verdict="안면부 합성 가능성 매우 높음",
            signals=[sig1],
            limitations=[],
            source_guess=SourceGuess(label="FaceSwap / ReActor", confidence=SourceConfidence.HIGH),
            next_checks=[],
        )
        # Create dummy file to test SHA-256 hash calculation
        self.sample1 = self.root / "suspect_video.mp4"
        self.sample1.write_bytes(b"dummy video data for hash testing")

        self.item1 = ScanItem(
            path=str(self.sample1),
            name="suspect_video.mp4",
            kind="video",
            status="analyzed",
            size_bytes=self.sample1.stat().st_size,
            result=res1,
        )

        sig2 = EvidenceSignal(title="센서 노이즈 질감 불일치", detail="얼굴 vs 주변 노이즈 편차", weight=25)
        res2 = ClassificationResult(
            score=55,
            band=RiskBand.MEDIUM,
            band_label="주의",
            verdict="미세 불일치 감지",
            signals=[sig2],
            limitations=[],
            source_guess=SourceGuess(label="SimSwap", confidence=SourceConfidence.MEDIUM),
            next_checks=[],
        )
        self.sample2 = self.root / "victim_photo.png"
        self.sample2.write_bytes(b"dummy image data for hash testing")

        self.item2 = ScanItem(
            path=str(self.sample2),
            name="victim_photo.png",
            kind="image",
            status="analyzed",
            size_bytes=self.sample2.stat().st_size,
            result=res2,
        )

        self.items = [self.item1, self.item2]

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_build_evidence_statement_structure_and_statutes(self) -> None:
        statement = build_evidence_statement(
            self.items,
            case_no="2026고단12345",
            case_name="성폭력처벌법위반(허위영상물편집등)",
            plaintiff="(의뢰사 상호명 입력) 귀하",
            defendant="(피고/피의자 성명 입력)",
        )
        self.assertEqual(len(statement.entries), 2)
        self.assertEqual(statement.entries[0].exhibit_no, "갑 제1호증")
        self.assertEqual(statement.entries[1].exhibit_no, "갑 제2호증")
        self.assertIn("법무법인(유한) 대륜", statement.law_firm)
        self.assertEqual(statement.contact, "02-780-1128")

        # Verify statutory mapping
        statutes = statement.entries[0].statutes
        self.assertTrue(any("성폭력범죄의 처벌 등에 관한 특례법 제14조의2" in st for st in statutes))
        self.assertTrue(any("정보통신망" in st for st in statutes))

        # Verify SHA-256 hash was calculated
        self.assertEqual(len(statement.entries[0].sha256), 64)

    def test_markdown_generation(self) -> None:
        statement = build_evidence_statement(self.items)
        md = statement.to_markdown()
        self.assertIn("# 증  거  설  명  서", md)
        self.assertIn("| 호증 | 서증(증거)의 명칭 |", md)
        self.assertIn("갑 제1호증", md)
        self.assertIn("02-780-1128", md)

        md_path = self.root / "statement.md"
        write_evidence_statement_markdown(md_path, statement)
        self.assertTrue(md_path.is_file())
        self.assertEqual(md_path.read_text(encoding="utf-8"), md)

    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf required for PDF generation")
    def test_pdf_generation(self) -> None:
        statement = build_evidence_statement(self.items)
        pdf_path = self.root / "statement.pdf"
        write_evidence_statement_pdf(pdf_path, statement)

        self.assertTrue(pdf_path.is_file())
        content = pdf_path.read_bytes()
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertGreater(len(content), 1000)

    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf required for PDF generation")
    def test_pdf_purpose_column_renders_content(self) -> None:
        """Regression: fixed-height rows silently dropped the purpose column."""
        import pymupdf

        statement = build_evidence_statement(self.items)
        pdf_path = self.root / "statement_purpose.pdf"
        write_evidence_statement_pdf(pdf_path, statement)

        doc = pymupdf.open(str(pdf_path))
        full_text = "\n".join(page.get_text() for page in doc)
        doc.close()
        # Neutral screening language — the tool must never assert the
        # evidence "proves" an illegal synthetic production.
        self.assertIn("소명함", full_text)
        self.assertNotIn("불법 합성물임을 입증함", full_text)
        self.assertIn("제14조의2", full_text)
        self.assertIn("갑 제1호증", full_text)

    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf required for PDF generation")
    def test_cli_evidence_statement_command(self) -> None:
        # Create a mock scan JSON
        scan_json = self.root / "scan_output.json"
        summary = BatchScanSummary(
            total=2, analyzed=2, high=1, medium=1, unknown=0, low=0,
            unsupported_or_failed=0, capped=False,
        )
        scan_payload = {
            "summary": summary.to_json(),
            "items": [self.item1.to_json(), self.item2.to_json()],
        }
        scan_json.write_text(json.dumps(scan_payload, ensure_ascii=False), encoding="utf-8")

        pdf_out = self.root / "cli_statement.pdf"
        md_out = self.root / "cli_statement.md"

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([
                "evidence-statement",
                str(scan_json),
                "--case-no", "2026형제9999",
                "--pdf-out", str(pdf_out),
                "--md-out", str(md_out),
                "--format", "json",
            ])
        self.assertEqual(code, 0)
        output = json.loads(buf.getvalue())
        self.assertEqual(output["case_no"], "2026형제9999")
        self.assertEqual(len(output["entries"]), 2)
        self.assertTrue(md_out.is_file())

    def test_cli_scan_with_evidence_statement_out(self) -> None:
        folder = self.root / "scan_target"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "dummy.txt").write_text("Hello world testing text scan", encoding="utf-8")

        stmt_out = self.root / "scan_statement.md"
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([
                "scan",
                str(folder),
                "--evidence-statement-out", str(stmt_out),
            ])
        self.assertEqual(code, 0)
        self.assertTrue(stmt_out.is_file())
        self.assertIn("증  거  설  명  서", stmt_out.read_text(encoding="utf-8"))

    @unittest.skipUnless(HAVE_FASTAPI and HAVE_PYMUPDF, "fastapi and pymupdf required")
    def test_api_report_evidence_statement_format(self) -> None:
        from fastapi.testclient import TestClient
        from deepfake_lens.api_server import CLIENT_HEADER, create_app

        app = create_app()
        client = TestClient(app)

        payload = {
            "items": [self.item1.to_json(), self.item2.to_json()],
            "format": "evidence",
            "case_no": "2026가합55555",
        }
        resp = client.post(
            "/api/report?format=evidence",
            json=payload,
            headers={"host": "localhost", CLIENT_HEADER: "gui"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("content-type"), "application/pdf")
        self.assertIn("deepfake-lens-evidence-statement.pdf", resp.headers.get("content-disposition", ""))
        self.assertTrue(resp.content.startswith(b"%PDF-"))

    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf not installed")
    def test_pdf_pagination_stress_60_entries(self) -> None:
        """60 mixed entries (long names, empty purposes, unhashed paths) must
        all survive pagination — every exhibit number and every filename must
        appear in the extracted text, and the disclosure/signoff blocks must
        not be clipped off the last page."""
        import pymupdf

        items = []
        for i in range(60):
            name = (
                f"evidence_{i:02d}_" + "매우긴파일명_" * (8 if i % 7 == 0 else 1) + ".png"
                if i % 5 else f"evidence_{i:02d}.png"
            )
            fpath = self.root / name
            if i % 3:
                fpath.write_bytes(b"x" * (i + 1))
                path = str(fpath)
            else:
                path = f"/nonexistent/{name}"  # unhashable entry
            band = [RiskBand.HIGH, RiskBand.MEDIUM, RiskBand.LOW, RiskBand.UNKNOWN][i % 4]
            res = ClassificationResult(
                score=10 * (i % 10),
                band=band,
                band_label=str(band.value),
                verdict="" if i % 6 == 0 else f"판정 근거 {i} — " + "긴목적문" * (30 if i % 9 == 0 else 2),
                signals=[], limitations=[],
                source_guess=SourceGuess.unknown(""),
                next_checks=[],
            )
            items.append(ScanItem(path=path, name=name, kind="image",
                                  status="analyzed", size_bytes=i + 1, result=res))

        stmt = build_evidence_statement(items, case_no="2024가단9999")
        self.assertEqual(len(stmt.entries), 60)
        out = self.root / "stress.pdf"
        write_evidence_statement_pdf(out, stmt)
        doc = pymupdf.open(str(out))
        try:
            text = "".join(p.get_text() for p in doc)
            self.assertGreater(len(doc), 1)  # pagination actually happened
            flat = "".join(text.split())
            for i, entry in enumerate(stmt.entries):
                self.assertIn(entry.exhibit_no, text)
                if "생략" not in entry.document_name:
                    self.assertIn("".join(entry.document_name.split())[:20], flat)
            self.assertIn("무결성", text)
            self.assertIn("2024가단9999", text)
        finally:
            doc.close()

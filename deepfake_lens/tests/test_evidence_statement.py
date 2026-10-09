"""Tests for court evidence statement (증거설명서) module and litigation export."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

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
from deepfake_lens.result_types import (
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceStrength,
    Verdict,
)
from deepfake_lens.evidence_statement import (
    EVIDENCE_STATEMENT_REPORT_TYPE,
    SIGNATURE_SECTION_TITLE,
    build_evidence_statement,
    signed_statement_body,
    write_evidence_statement_json,
    write_evidence_statement_markdown,
    write_evidence_statement_pdf,
)
from deepfake_lens.signing import REPORT_KEY_ENV, signed_body_sha256, verify_report

HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None
HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None


class _StatementFixture(unittest.TestCase):
    """Two scan items (a manipulation verdict and a legacy medium row) with real files."""

    def setUp(self) -> None:
        from unittest import mock

        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        # N17: hermetic — no operator config file supplies an office identity.
        config = mock.patch.dict(os.environ, {"DEEPFAKE_LENS_CONFIG": str(self.root / "no-config.json")})
        config.start()
        self.addCleanup(config.stop)

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
            # G6: statutes are listed only for a manipulation verdict backed
            # by deterministic evidence (a high band alone no longer counts).
            verdict_code=Verdict.MANIPULATION_EVIDENCE,
            evidence=[EvidenceItem(
                "C2PA 서명: 생성형 AI 출처 선언", "trainedAlgorithmicMedia",
                EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "c2pa",
            )],
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


class EvidenceStatementTest(_StatementFixture):
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
        # N17: no built-in office identity (was "법무법인(유한) 대륜" / "02-780-1128").
        self.assertEqual(statement.law_firm, "")
        self.assertEqual(statement.contact, "")

        # Verify statutory mapping
        statutes = statement.entries[0].statutes
        self.assertTrue(any("성폭력범죄의 처벌 등에 관한 특례법 제14조의2" in st for st in statutes))
        self.assertTrue(any("정보통신망" in st for st in statutes))

        # Verify SHA-256 hash was calculated
        self.assertEqual(len(statement.entries[0].sha256), 64)

    def test_markdown_generation(self) -> None:
        # N17: the office identity is the caller's (was the built-in "02-780-1128").
        statement = build_evidence_statement(self.items, law_firm="법무법인 예시", contact="02-0000-0000")
        md = statement.to_markdown()
        self.assertIn("# 증  거  설  명  서", md)
        self.assertIn("| 호증 | 서증(증거)의 명칭 |", md)
        self.assertIn("갑 제1호증", md)
        self.assertIn("02-0000-0000", md)
        self.assertIn("법무법인 예시 디지털포렌식 감정센터", md)

        md_path = self.root / "statement.md"
        write_evidence_statement_markdown(md_path, statement)
        self.assertTrue(md_path.is_file())
        # G30: the written file is the statement plus its signature section
        # (was: byte-equal to to_markdown(), i.e. never signed).
        written = md_path.read_text(encoding="utf-8")
        self.assertTrue(written.startswith(md))
        self.assertIn(SIGNATURE_SECTION_TITLE, written)

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

        # N11: item2's legacy "medium" band is not in the scan-result item
        # contract and /api/report now refuses it (400); the posted copy
        # carries the band the contract derives for an undetermined verdict.
        from dataclasses import replace as dc_replace

        assert self.item2.result is not None
        item2 = dc_replace(self.item2, result=dc_replace(self.item2.result, band=RiskBand.UNKNOWN))
        payload = {
            "items": [self.item1.to_json(), item2.to_json()],
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


KEY = b"evidence-statement-test-key"


class EvidenceStatementSigningTest(_StatementFixture):
    """G30: the 증거설명서 is signed over its whole body like the other reports."""

    def _env(self, key: bytes | None) -> Any:
        from unittest import mock

        env = {k: v for k, v in os.environ.items() if k != REPORT_KEY_ENV}
        if key is not None:
            env[REPORT_KEY_ENV] = key.decode("utf-8")
        return mock.patch.dict(os.environ, env, clear=True)

    def test_signed_body_verifies_and_every_field_is_covered(self) -> None:
        statement = build_evidence_statement(self.items, case_no="2026고합123")
        signed = signed_statement_body(statement, KEY)
        self.assertEqual(signed["report_type"], EVIDENCE_STATEMENT_REPORT_TYPE)
        self.assertTrue(verify_report(signed, KEY).verified)
        for field, value in (("case_no", "2026고합124"), ("defendant", "다른 사람"), ("signature_note", "x"), ("report_type", "scan")):
            with self.subTest(field=field):
                tampered = json.loads(json.dumps(signed))
                tampered[field] = value
                self.assertEqual(verify_report(tampered, KEY).status, "tampered")
        tampered = json.loads(json.dumps(signed))
        entry = tampered["entries"][0]
        entry["purpose_of_proof"] = entry["purpose_of_proof"].replace("자동", "수동", 1)
        self.assertEqual(verify_report(tampered, KEY).status, "tampered")
        tampered = json.loads(json.dumps(signed))
        tampered["entries"][1]["sha256"] = "0" * 64
        self.assertEqual(verify_report(tampered, KEY).status, "tampered")

    def test_without_a_key_it_says_unsigned(self) -> None:
        statement = build_evidence_statement(self.items)
        with self._env(None):
            signed = signed_statement_body(statement)
            md_path = self.root / "unsigned.md"
            write_evidence_statement_markdown(md_path, statement)
        self.assertIsNone(signed["signature"])
        self.assertIn("서명 없음", str(signed["signature_note"]))
        self.assertEqual(verify_report(signed, KEY).status, "unsigned")
        self.assertIn("서명 없음", md_path.read_text(encoding="utf-8"))

    def test_markdown_and_json_carry_the_same_signed_body(self) -> None:
        statement = build_evidence_statement(self.items)
        signed = signed_statement_body(statement, KEY)
        md_path, json_path = self.root / "s.md", self.root / "s.json"
        write_evidence_statement_markdown(md_path, statement, signed=signed)
        write_evidence_statement_json(json_path, statement, signed=signed)
        text = md_path.read_text(encoding="utf-8")
        self.assertIn(str(signed["signature"]), text)
        self.assertIn(signed_body_sha256(signed), text)
        self.assertTrue(verify_report(json_path, KEY).verified)

    def test_cli_evidence_statement_signs_json_out_and_stdout(self) -> None:
        scan_json = self.root / "scan.json"
        scan_json.write_text(json.dumps({"items": [self.item1.to_json(), self.item2.to_json()]}, ensure_ascii=False), encoding="utf-8")
        json_out, md_out = self.root / "stmt.json", self.root / "stmt.md"
        buf = io.StringIO()
        with self._env(KEY), redirect_stdout(buf):
            code = main(["evidence-statement", str(scan_json), "--json-out", str(json_out), "--md-out", str(md_out), "--format", "json"])
        self.assertEqual(code, 0)
        printed = json.loads(buf.getvalue())
        self.assertTrue(verify_report(printed, KEY).verified)
        self.assertTrue(verify_report(json_out, KEY).verified)
        self.assertEqual(json.loads(json_out.read_text(encoding="utf-8")), printed)
        self.assertIn(signed_body_sha256(printed), md_out.read_text(encoding="utf-8"))
        key_file = self.root / "key.txt"
        key_file.write_bytes(b"another-key")
        with self._env(None), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["evidence-statement", str(scan_json), "--json-out", str(json_out), "--key-file", str(key_file)]), 0)
        self.assertTrue(verify_report(json_out, b"another-key").verified)

    def test_cli_scan_writes_signed_statement_json(self) -> None:
        folder = self.root / "case"
        folder.mkdir()
        (folder / "memo.txt").write_text("사건 메모 본문입니다.", encoding="utf-8")
        out = self.root / "statement.json"
        with self._env(KEY), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["scan", str(folder), "--no-default-engine", "--evidence-statement-out", str(out)]), 0)
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["report_type"], EVIDENCE_STATEMENT_REPORT_TYPE)
        self.assertTrue(verify_report(payload, KEY).verified)

    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf required for PDF generation")
    def test_pdf_prints_the_signature(self) -> None:
        import pymupdf

        statement = build_evidence_statement(self.items)
        pdf_path = self.root / "signed.pdf"
        signed = write_evidence_statement_pdf(pdf_path, statement, key=KEY)
        doc = pymupdf.open(str(pdf_path))
        text = "".join(page.get_text() for page in doc)
        doc.close()
        self.assertIn(str(signed["signature"]), "".join(text.split()))
        self.assertTrue(verify_report(signed, KEY).verified)


class EvidenceStatementHashSourceTest(unittest.TestCase):
    """D5: the statement records the scan's ``item.sha256``; a fallback hash
    resolves relative paths against the scan root, never the cwd."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.case = self.base / "case"
        self.case.mkdir()
        (self.case / "note.txt").write_text("사건 메모 원문입니다.\n", encoding="utf-8")
        (self.case / "photo.bin").write_bytes(b"\x00\x01 opaque evidence bytes")
        # A different file with the same relative name in the working dir:
        # hashing ``item.path`` against the cwd would record this one.
        self.decoy_dir = self.base / "cwd"
        self.decoy_dir.mkdir()
        (self.decoy_dir / "note.txt").write_text("다른 파일\n", encoding="utf-8")
        self.old_cwd = os.getcwd()

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    @staticmethod
    def _sha(path: Path) -> str:
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _assert_statement_hashes(self, items: list[ScanItem], *, scan_root: Path | str | None) -> None:
        statement = build_evidence_statement(items, scan_root=scan_root)
        by_path = {entry.file_path: entry.sha256 for entry in statement.entries}
        for name in ("note.txt", "photo.bin"):
            self.assertEqual(by_path[name], self._sha(self.case / name), name)
        self.assertNotEqual(by_path["note.txt"], self._sha(self.decoy_dir / "note.txt"))

    def test_absolute_folder_scan_records_item_sha256(self) -> None:
        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder

        os.chdir(self.decoy_dir)
        _, items = scan_folder(self.case.resolve(), AnalysisOptions())
        self.assertTrue(all(item.sha256 for item in items))
        self._assert_statement_hashes(items, scan_root=None)

    def test_relative_folder_scan_records_item_sha256(self) -> None:
        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder

        os.chdir(self.base)
        _, items = scan_folder(Path("case"), AnalysisOptions())
        os.chdir(self.decoy_dir)  # the statement is built from elsewhere
        self._assert_statement_hashes(items, scan_root=None)

    def test_item_sha256_wins_over_rehashing(self) -> None:
        item = ScanItem("note.txt", "note.txt", "text", "analyzed", 1, sha256="ab" * 32)
        statement = build_evidence_statement([item], scan_root=self.case)
        self.assertEqual(statement.entries[0].sha256, "ab" * 32)

    def test_fallback_resolves_against_scan_root_not_cwd(self) -> None:
        os.chdir(self.decoy_dir)
        item = ScanItem("note.txt", "note.txt", "text", "analyzed", 1)
        with_root = build_evidence_statement([item], scan_root=self.case)
        self.assertEqual(with_root.entries[0].sha256, self._sha(self.case / "note.txt"))
        without_root = build_evidence_statement([item])
        self.assertEqual(without_root.entries[0].sha256, "")
        self.assertIn("해시 불가", without_root.entries[0].purpose_of_proof)

    def _cli_statement_hashes(self, argv: list[str], out: Path) -> dict[str, str]:
        """Run the CLI with a scanner whose rows lost ``sha256`` (an old
        cache row / JSON) from the decoy cwd; return file_path -> sha256."""
        import dataclasses
        from unittest import mock

        from deepfake_lens import cli
        from deepfake_lens.analysis_api import ScanRun, scan_folder_run as real_scan_folder_run

        def scan_without_digests(*args: object, **kwargs: object) -> ScanRun:
            run = real_scan_folder_run(*args, **kwargs)  # type: ignore[arg-type]
            stripped = [dataclasses.replace(item, sha256=None) for item in run.items]
            return dataclasses.replace(run, items=stripped)

        os.chdir(self.decoy_dir)
        # N15: the CLI calls scan_folder_run (scan_folder is the 2-tuple API).
        with mock.patch.object(cli, "scan_folder_run", scan_without_digests), mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(cli.main(argv), 0)
        body = json.loads(out.read_text(encoding="utf-8"))
        return {entry["file_path"]: entry["sha256"] for entry in body["entries"]}

    def _assert_cli_hashes(self, by_path: dict[str, str]) -> None:
        for name in ("note.txt", "photo.bin"):
            self.assertEqual(by_path[name], self._sha(self.case / name), name)
        self.assertNotEqual(by_path["note.txt"], self._sha(self.decoy_dir / "note.txt"))

    def test_cli_scan_statement_resolves_against_scan_folder(self) -> None:
        """D5: ``scan <folder> --evidence-statement-out`` passes the folder as scan_root."""
        out = self.base / "scan-stmt.json"
        by_path = self._cli_statement_hashes(
            ["scan", str(self.case.resolve()), "--pixel", "off", "--format", "json", "--evidence-statement-out", str(out)], out
        )
        self._assert_cli_hashes(by_path)

    def test_cli_evidence_statement_folder_resolves_against_folder(self) -> None:
        """D5: ``evidence-statement <folder>`` passes the folder as scan_root."""
        out = self.base / "stmt.json"
        by_path = self._cli_statement_hashes(
            ["evidence-statement", str(self.case.resolve()), "--format", "json", "--json-out", str(out)], out
        )
        self._assert_cli_hashes(by_path)

    def test_archive_member_without_digest_is_not_hashed(self) -> None:
        item = ScanItem("bundle.zip::note.txt", "bundle.zip::note.txt", "text", "analyzed", 1)
        statement = build_evidence_statement([item], scan_root=self.case)
        self.assertEqual(statement.entries[0].sha256, "")


@unittest.skipUnless(hasattr(os, "symlink"), "symlinks not available")
class EvidenceStatementSymlinkTest(unittest.TestCase):
    """N2: a symbolic link is never followed when the statement hashes a row,
    and rows without a verdict print a Korean status line."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.case = base / "case"
        self.case.mkdir()
        (self.case / "real.txt").write_text("사건 메모 원문입니다.\n", encoding="utf-8")
        self.outside = base / "outside-secret.txt"
        self.outside.write_text("폴더 밖 파일 — 해시되면 안 됩니다\n", encoding="utf-8")
        try:
            (self.case / "in_link.txt").symlink_to("real.txt")
            (self.case / "out_link.txt").symlink_to(self.outside)
            (self.case / "linked_dir").symlink_to(self.case, target_is_directory=True)
        except OSError as exc:  # pragma: no cover - Windows without privilege
            self.skipTest(f"cannot create symlinks: {exc}")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _sha(path: Path) -> str:
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _entries(self, items: list[ScanItem]) -> dict[str, Any]:
        statement = build_evidence_statement(items, scan_root=self.case)
        return {entry.file_path: entry for entry in statement.entries}

    def test_scanned_symlink_rows_are_not_hashed_and_read_as_skipped(self) -> None:
        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder

        _, items = scan_folder(self.case, AnalysisOptions())
        entries = self._entries(items)
        digests = {self._sha(self.case / "real.txt"), self._sha(self.outside)}
        for name in ("in_link.txt", "out_link.txt"):
            with self.subTest(row=name):
                entry = entries[name]
                self.assertEqual(entry.sha256, "")
                self.assertNotIn(entry.sha256, digests)
                purpose = entry.purpose_of_proof
                self.assertIn("해시 불가(심볼릭 링크 — 링크를 따라가지 않음)", purpose)
                self.assertTrue(purpose.startswith("상태: 건너뜀 — 심볼릭 링크"), purpose)
                self.assertNotIn("자동 분석 결론: skipped", purpose)
                self.assertNotIn("skipped", purpose)
                self.assertEqual(entry.verdict_label, "건너뜀")
                for digest in digests:
                    self.assertNotIn(digest, purpose)
        self.assertEqual(entries["real.txt"].sha256, self._sha(self.case / "real.txt"))

    def test_rows_without_digest_never_follow_a_link(self) -> None:
        """A sha256-less row (old JSON) pointing at a link — or through a
        linked folder — is refused, never hashed through the link."""
        rows = [
            ScanItem("in_link.txt", "in_link.txt", "text", "analyzed", 1),
            ScanItem("out_link.txt", "out_link.txt", "text", "analyzed", 1),
            ScanItem("linked_dir/real.txt", "real.txt", "text", "analyzed", 1),
        ]
        entries = self._entries(rows)
        for path in ("in_link.txt", "out_link.txt", "linked_dir/real.txt"):
            with self.subTest(row=path):
                self.assertEqual(entries[path].sha256, "")
                self.assertIn("해시 불가(심볼릭 링크 — 링크를 따라가지 않음)", entries[path].purpose_of_proof)
        # An absolute path to the outside link (no scan root) is refused too.
        absolute = build_evidence_statement([ScanItem(str(self.case / "out_link.txt"), "out_link.txt", "text", "analyzed", 1)])
        self.assertEqual(absolute.entries[0].sha256, "")

    def test_non_verdict_rows_print_a_korean_status_line(self) -> None:
        rows = [
            ScanItem("a.xyz", "a.xyz", "unsupported", "unsupported", 3, error="지원 형식이 아닙니다."),
            ScanItem("b.jpg", "b.jpg", "image", "failed", 0, error="분석 오류: RuntimeError: 디코더 실패"),
            ScanItem("c.txt", "c.txt", "duplicate", "duplicate", 3, error="중복 내용(동일 해시)"),
        ]
        entries = self._entries(rows)
        expected = {"a.xyz": "상태: 미지원 — 지원 형식이 아닙니다.", "b.jpg": "상태: 실패 — 분석 오류: RuntimeError: 디코더 실패", "c.txt": "상태: 중복 — 중복 내용(동일 해시)"}
        for path, head in expected.items():
            with self.subTest(row=path):
                purpose = entries[path].purpose_of_proof
                self.assertEqual(purpose.splitlines()[0], head)
                self.assertNotIn("[자동 분석 결론:", purpose)
        markdown = build_evidence_statement(rows, scan_root=self.case).to_markdown()
        for raw in ("unsupported", "failed", "duplicate", "skipped"):
            self.assertNotIn(f"결론: {raw}", markdown)


class PdfDependencyMissingTest(unittest.TestCase):
    """R6: --evidence-statement-pdf-out (and evidence-statement --pdf-out)
    without pymupdf -> a Korean message naming the package, exit 2, no
    traceback, checked before the scan runs; pymupdf/fitz imports are
    blocked so the test holds where pymupdf is installed too."""

    BENCHMARK = Path(__file__).resolve().parents[2] / "fixtures" / "benchmark"

    def setUp(self) -> None:
        import sys
        from unittest import mock

        patcher = mock.patch.dict(sys.modules, {"pymupdf": None, "fitz": None})
        patcher.start()
        self.addCleanup(patcher.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name)

    def _run(self, argv: list[str]) -> tuple[int, str]:
        from contextlib import redirect_stderr
        from unittest import mock

        err = io.StringIO()
        # N15: the CLI scans through scan_folder_run (scan_folder is the 2-tuple API).
        with mock.patch("deepfake_lens.cli.scan_folder_run", side_effect=AssertionError("scan must not start")), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = main(argv)
        return code, err.getvalue()

    def _assert_korean_refusal(self, code: int, stderr: str) -> None:
        self.assertEqual(code, 2)
        self.assertIn("pymupdf", stderr)
        self.assertIn("PDF 증거설명서를 만들려면 pymupdf 패키지가 필요합니다", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertNotIn("is required", stderr)

    def test_scan_evidence_statement_pdf_out(self) -> None:
        pdf = self.out / "statement.pdf"
        code, stderr = self._run(["scan", str(self.BENCHMARK), "--evidence-statement-pdf-out", str(pdf)])
        self._assert_korean_refusal(code, stderr)
        self.assertFalse(pdf.exists())

    def test_scan_evidence_statement_out_with_pdf_suffix(self) -> None:
        code, stderr = self._run(["scan", str(self.BENCHMARK), "--evidence-statement-out", str(self.out / "s.pdf")])
        self._assert_korean_refusal(code, stderr)

    def test_evidence_statement_command_pdf_out(self) -> None:
        code, stderr = self._run(["evidence-statement", str(self.BENCHMARK), "--pdf-out", str(self.out / "s.pdf")])
        self._assert_korean_refusal(code, stderr)

    def test_writer_raises_korean_dependency_error(self) -> None:
        from deepfake_lens.evidence_statement import (
            PDF_DEPENDENCY_MESSAGE,
            PdfDependencyMissing,
            pdf_backend_available,
            write_evidence_statement_pdf,
        )

        self.assertFalse(pdf_backend_available())
        statement = build_evidence_statement([])
        with self.assertRaises(PdfDependencyMissing) as ctx:
            write_evidence_statement_pdf(self.out / "s.pdf", statement)
        self.assertEqual(str(ctx.exception), PDF_DEPENDENCY_MESSAGE)
        self.assertIsInstance(ctx.exception, RuntimeError)  # web /api/report catches RuntimeError

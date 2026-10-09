"""Result contract v2 end-to-end (phase 0, WP-A: G5/G6/G12/G24).

Covers the evidence mapping table, the derived legacy fields, the archive
roll-up, and that verdict/evidence kinds/coverage survive into every
renderer (CLI table, CSV, HTML, minimal PDF, evidence statement).
"""

from __future__ import annotations

import contextlib
import csv
import importlib.util
import io
import random
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from deepfake_lens.core import (
    _archive_container_item,
    analyze_file,
    analyze_text,
    build_classification_result,
    scan_directory,
    summarize,
)
from deepfake_lens.evidence_rules import c2pa_evidence, image_metadata_evidence, model_evidence
from deepfake_lens.result_text import TEXT_LEGAL_LIMITATION
from deepfake_lens.result_types import (
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceStrength,
    ExternalModelAnalysis,
    Grade,
    RiskBand,
    ScanItem,
    SourceGuess,
    Verdict,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
C2PA_FIXTURE = REPO_ROOT / "fixtures" / "c2pa-test" / "signed-c2pa.png"
BENCH_DIR = REPO_ROOT / "fixtures" / "benchmark"
HAVE_C2PA = importlib.util.find_spec("c2pa") is not None


def _png(path: Path, width: int, height: int, pixel_at, text_chunks: dict[str, str] | None = None) -> None:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + b"".join(bytes(pixel_at(x, y)) for x in range(width)) for y in range(height))
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    texts = b"".join(chunk(b"tEXt", k.encode("latin-1") + b"\x00" + v.encode("latin-1")) for k, v in (text_chunks or {}).items())
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + ihdr + texts + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def _valid(**overrides):
    base = {
        "present": True, "status": "valid", "state": "Trusted", "trusted": True,
        "signature": {"common_name": "Test CA"},
        "success_codes": ["claimSignature.validated", "assertion.dataHash.match"],
        "failure_codes": [],
        "digital_source_types": [],
    }
    base.update(overrides)
    return base


class C2paEvidenceMappingTest(unittest.TestCase):
    def test_trusted_ai_declaration_is_strong_synthetic(self) -> None:
        [item] = c2pa_evidence(_valid(digital_source_types=["trainedAlgorithmicMedia"]))
        self.assertEqual((item.kind, item.direction, item.strength),
                         (EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG))

    def test_trusted_capture_with_hash_match_is_strong_authentic(self) -> None:
        [item] = c2pa_evidence(_valid(digital_source_types=["http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture"]))
        self.assertEqual((item.direction, item.strength), (EvidenceDirection.AUTHENTIC, EvidenceStrength.STRONG))

    def test_hash_mismatch_never_authentic(self) -> None:
        [item] = c2pa_evidence(_valid(digital_source_types=["digitalCapture"], failure_codes=["assertion.dataHash.mismatch"]))
        self.assertNotEqual(item.direction, EvidenceDirection.AUTHENTIC)

    def test_untrusted_signer_with_ai_declaration_is_moderate(self) -> None:
        [item] = c2pa_evidence(_valid(trusted=False, status="invalid", digital_source_types=["trainedAlgorithmicMedia"]))
        self.assertEqual((item.direction, item.strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE))

    def test_untrusted_manifest_is_neutral_weak(self) -> None:
        [item] = c2pa_evidence(_valid(trusted=False, status="invalid"))
        self.assertEqual((item.direction, item.strength), (EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK))

    def test_absent_manifest_gives_no_evidence(self) -> None:
        self.assertEqual(c2pa_evidence({"present": False, "status": "absent"}), [])
        self.assertEqual(c2pa_evidence(None), [])

    @unittest.skipUnless(HAVE_C2PA and C2PA_FIXTURE.is_file(), "c2pa-python and fixture required")
    def test_real_fixture_records_coverage_and_untrusted_evidence(self) -> None:
        item = analyze_file(C2PA_FIXTURE)
        result = item.result
        assert result is not None
        c2pa_entries = [entry for entry in result.coverage if entry.check == "c2pa"]
        self.assertEqual([entry.status for entry in c2pa_entries], [CoverageStatus.RAN])
        self.assertTrue(any(e.layer == "c2pa" and e.direction == EvidenceDirection.NEUTRAL for e in result.evidence))
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)


class MetadataEvidenceMappingTest(unittest.TestCase):
    def test_structured_a1111_parameters_are_strong(self) -> None:
        items = image_metadata_evidence(
            {"png.parameters": "x\nNegative prompt: y\nSteps: 20, Sampler: Euler, CFG scale: 7, Seed: 1"}, (512, 512)
        )
        self.assertEqual(items[0].strength, EvidenceStrength.STRONG)
        self.assertTrue(any(i.title == "생성 모델에 흔한 정사각 해상도" and i.direction == EvidenceDirection.NEUTRAL for i in items))

    def test_header_byte_string_is_only_moderate(self) -> None:
        [item] = image_metadata_evidence({"header.text": "Generated with FLUX.1 by Black Forest Labs"}, None)
        self.assertEqual((item.direction, item.strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE))

    def test_tool_word_in_free_text_field_is_not_strong(self) -> None:
        items = image_metadata_evidence({"png.Description": "fashion runway show backstage"}, (800, 600))
        self.assertFalse(any(i.strength == EvidenceStrength.STRONG for i in items))

    def test_missing_metadata_is_neutral(self) -> None:
        [item] = image_metadata_evidence({}, None)
        self.assertEqual(item.direction, EvidenceDirection.NEUTRAL)


class ModelEvidenceMappingTest(unittest.TestCase):
    def test_uncalibrated_score_is_raw_not_probability(self) -> None:
        item = model_evidence(ExternalModelAnalysis(True, 93, "high", "aide", "ok"))
        assert item is not None
        self.assertEqual(item.kind, EvidenceKind.STATISTICAL)
        self.assertIsNone(item.probability)
        self.assertIsNone(item.calibration_id)
        self.assertEqual(item.raw_score, 93)
        self.assertEqual(item.direction, EvidenceDirection.SYNTHETIC)

    def test_low_uncalibrated_score_is_neutral(self) -> None:
        item = model_evidence(ExternalModelAnalysis(True, 12, "low", "aide", "ok"))
        assert item is not None
        self.assertEqual(item.direction, EvidenceDirection.NEUTRAL)

    def test_calibrated_profile_carries_probability_and_ci(self) -> None:
        item = model_evidence(ExternalModelAnalysis(
            True, 90, "high", "aide", "ok", probability=0.9, probability_ci=(0.8, 0.95),
            calibration_id="cal-1", measured_on="corpus@test",
        ))
        assert item is not None
        self.assertEqual(item.probability, 0.9)
        self.assertTrue(item.is_calibrated)

    def test_unavailable_model_gives_no_evidence(self) -> None:
        self.assertIsNone(model_evidence(ExternalModelAnalysis(False, 0, "unavailable", "aide", "missing")))


class DerivedLegacyFieldsTest(unittest.TestCase):
    def _result(self, evidence, coverage=()):
        return build_classification_result(
            subject="이미지", evidence=list(evidence), coverage=list(coverage), source_guess=SourceGuess.unknown(),
            limitations=[], next_checks=[],
        )

    def test_uncalibrated_result_has_zero_score(self) -> None:
        result = self._result([EvidenceItem("m", "d", EvidenceKind.STATISTICAL, EvidenceDirection.SYNTHETIC,
                                            EvidenceStrength.WEAK, "model", raw_score=99)])
        self.assertEqual(result.score, 0)
        self.assertFalse(result.score_is_calibrated)
        self.assertEqual(result.band, RiskBand.UNKNOWN)

    def test_legacy_signal_weights_follow_strength_and_direction(self) -> None:
        result = self._result([
            EvidenceItem("syn", "d", EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "m"),
            EvidenceItem("auth", "d", EvidenceKind.DETERMINISTIC, EvidenceDirection.AUTHENTIC, EvidenceStrength.STRONG, "c2pa"),
        ])
        weights = {signal.title: signal.weight for signal in result.signals}
        self.assertGreater(weights["syn"], 0)
        self.assertEqual(weights["auth"], 0)
        self.assertEqual(result.band_label, "조작·생성 근거 있음")

    def test_failed_check_appears_in_limitations_and_verdict(self) -> None:
        result = self._result([], [CoverageEntry("c2pa", CoverageStatus.FAILED, "ValueError: bad box")])
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        self.assertIn("검사 실패", result.verdict)
        self.assertTrue(any("C2PA 출처 검증 실패: ValueError" in lim for lim in result.limitations))


class ArchiveRollUpTest(unittest.TestCase):
    def _member(self, verdict: Verdict) -> ScanItem:
        evidence = []
        if verdict == Verdict.MANIPULATION_EVIDENCE:
            evidence = [EvidenceItem("s", "d", EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "m")]
        elif verdict == Verdict.AUTHENTICITY_EVIDENCE:
            evidence = [EvidenceItem("a", "d", EvidenceKind.DETERMINISTIC, EvidenceDirection.AUTHENTIC, EvidenceStrength.STRONG, "c2pa")]
        result = build_classification_result(subject="이미지", evidence=evidence, coverage=[], source_guess=SourceGuess.unknown(),
                                             limitations=[], next_checks=[])
        return ScanItem("a.zip::x.png", "x.png", "image", "analyzed", 1, result)

    def _container(self, verdicts, **kwargs):
        members = [self._member(v) for v in verdicts]
        return _archive_container_item("a.zip", "a.zip", Path("a.zip"), fmt="zip", members=len(members), skipped=kwargs.pop("skipped", 0),
                                       warnings=kwargs.pop("warnings", []), member_items=members, **kwargs)

    def test_any_manipulated_member_rolls_up(self) -> None:
        item = self._container([Verdict.UNDETERMINED, Verdict.MANIPULATION_EVIDENCE])
        assert item.result is not None
        self.assertEqual(item.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)

    def test_all_authentic_rolls_up_only_without_skips(self) -> None:
        clean = self._container([Verdict.AUTHENTICITY_EVIDENCE] * 2)
        partial = self._container([Verdict.AUTHENTICITY_EVIDENCE] * 2, skipped=1)
        assert clean.result is not None and partial.result is not None
        self.assertEqual(clean.result.verdict_code, Verdict.AUTHENTICITY_EVIDENCE)
        self.assertEqual(partial.result.verdict_code, Verdict.UNDETERMINED)

    def test_extraction_error_is_failed_coverage(self) -> None:
        item = self._container([], extraction_error="BadZipFile: truncated")
        assert item.result is not None
        self.assertEqual(item.result.coverage[0].status, CoverageStatus.FAILED)
        self.assertEqual(item.result.band, RiskBand.UNKNOWN)


class NoMediumBandSweepTest(unittest.TestCase):
    """No analysis path yields a MEDIUM band (spec: 'medium 밴드 생성 경로 0')."""

    def test_varied_inputs_never_produce_medium(self) -> None:
        rng = random.Random(7)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 128 px: at the model range floor. (A checkerboard is covered by
            # test_core at 64 px; at 128 px the deep pixel pass takes ~13 s.)
            _png(root / "noise.png", 128, 128, lambda x, y: (rng.randrange(256), rng.randrange(256), rng.randrange(256)))
            _png(root / "gradient.png", 128, 128, lambda x, y: (x, y, (x + y) // 2))
            _png(root / "flat.png", 128, 128, lambda x, y: (128, 128, 128))
            (root / "ai.txt").write_text("As an AI language model, I cannot browse. 결론적으로 다양한 관점이 중요합니다.", encoding="utf-8")
            (root / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            (root / "voice.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            for path in BENCH_DIR.glob("*.png"):
                (root / path.name).write_bytes(path.read_bytes())
            for pixel_mode in ("off", "deep"):
                summary, items = scan_directory(root, pixel_mode=pixel_mode, deep_signals=True)
                self.assertEqual(summary.medium, 0)
                for item in items:
                    if item.result is None:
                        continue
                    self.assertNotEqual(item.result.band, RiskBand.MEDIUM, item.path)
                    self.assertIn(item.result.verdict_code, set(Verdict))
                    self.assertTrue(item.result.coverage, f"{item.path}: coverage missing")
                    if item.kind != "archive" and item.result.verdict_code != Verdict.MANIPULATION_EVIDENCE:
                        self.assertNotEqual(item.result.band, RiskBand.HIGH)


class RendererTest(unittest.TestCase):
    """Verdict, evidence kinds and coverage reach every output (QA-OUT-6)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        _png(root / "a1111.png", 64, 64, lambda x, y: (x * 3, y * 3, 90),
             {"parameters": "cat\nNegative prompt: dog\nSteps: 20, Sampler: Euler, CFG scale: 7, Seed: 5"})
        (root / "note.txt").write_text("As an AI language model, I can help. 결론적으로 균형 잡힌 접근이 중요합니다.", encoding="utf-8")
        cls.root = root
        cls.summary, cls.items = scan_directory(root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_summary_counts_verdicts(self) -> None:
        self.assertEqual(self.summary.manipulation_evidence, 1)
        self.assertEqual(self.summary.undetermined, 1)
        self.assertEqual(self.summary, summarize(self.items, capped=False))

    def test_text_result_is_reference_with_legal_sentence_first(self) -> None:
        text = next(item for item in self.items if item.kind == "text")
        assert text.result is not None
        self.assertEqual(text.result.grade, Grade.REFERENCE)
        self.assertEqual(text.result.limitations[0], TEXT_LEGAL_LIMITATION)
        self.assertTrue(text.result.verdict.startswith("참고"))

    def test_cli_table_shows_verdicts_and_kinds(self) -> None:
        from deepfake_lens.cli_render import _print_table

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_table(self.summary, self.items, include_low=True)
        text = out.getvalue()
        self.assertIn("조작·생성 근거 있음", text)
        self.assertIn("판단 불가", text)
        self.assertIn("[결정적 근거]", text)
        self.assertIn("결정 0·통계 0·어휘", text)
        self.assertIn(TEXT_LEGAL_LIMITATION, text)
        self.assertNotIn("medium=", text)

    def test_csv_has_v2_columns(self) -> None:
        from deepfake_lens.cli_render import _write_csv

        path = self.root / "out.csv"
        _write_csv(path, self.items)
        rows = list(csv.DictReader(line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#")))
        by_path = {row["path"]: row for row in rows}
        self.assertEqual(by_path["a1111.png"]["verdict_code"], "manipulation_evidence")
        self.assertEqual(by_path["note.txt"]["grade"], "reference")
        self.assertGreaterEqual(int(by_path["note.txt"]["evidence_lexical"]), 1)

    def test_csv_score_column_is_calibrated_or_blank_and_risk_is_the_conclusion(self) -> None:
        """R16: `score`/`risk` became `보정점수(미보정시 공란)`/`결론` (same positions)."""
        from dataclasses import replace

        from deepfake_lens.cli_render import CSV_CALIBRATED_SCORE_COLUMN, CSV_VERDICT_COLUMN, _write_csv

        path = self.root / "r16.csv"
        calibrated = [
            replace(item, result=replace(item.result, score=73, score_is_calibrated=True)) if item.path == "note.txt" and item.result else item
            for item in self.items
        ]
        _write_csv(path, calibrated)
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
        header = next(csv.reader(lines[:1]))
        self.assertEqual(header[3:5], [CSV_CALIBRATED_SCORE_COLUMN, CSV_VERDICT_COLUMN])
        self.assertEqual(header[3:5], ["보정점수(미보정시 공란)", "결론"])
        self.assertNotIn("score", header)
        self.assertNotIn("risk", header)
        by_path = {row["path"]: row for row in csv.DictReader(lines)}
        self.assertEqual(by_path["a1111.png"][CSV_CALIBRATED_SCORE_COLUMN], "")
        self.assertEqual(by_path["a1111.png"][CSV_VERDICT_COLUMN], "조작·생성 근거 있음")
        self.assertEqual(by_path["note.txt"][CSV_CALIBRATED_SCORE_COLUMN], "73")
        self.assertEqual(by_path["note.txt"][CSV_VERDICT_COLUMN], "판단 불가")

    def test_include_low_means_authenticity_rows(self) -> None:
        """R16: --include-low (alias --include-authentic) adds only "원본성 근거 있음" rows; help is Korean."""
        from dataclasses import replace

        from deepfake_lens.cli_parser import build_parser
        from deepfake_lens.cli_render import _print_table

        authentic = [
            replace(item, result=replace(item.result, verdict_code=Verdict.AUTHENTICITY_EVIDENCE)) if item.path == "note.txt" and item.result else item
            for item in self.items
        ]
        default, included = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(default):
            _print_table(self.summary, authentic, include_low=False)
        with contextlib.redirect_stdout(included):
            _print_table(self.summary, authentic, include_low=True)
        self.assertNotIn("note.txt", default.getvalue())
        self.assertIn("원본성 근거 있음 1건은 표에서 생략했습니다", default.getvalue())
        self.assertIn("a1111.png", default.getvalue())
        self.assertIn("note.txt", included.getvalue())
        parser, sub = build_parser()
        self.assertTrue(parser.parse_args(["scan", ".", "--include-authentic"]).include_low)
        self.assertTrue(parser.parse_args(["scan", ".", "--include-low"]).include_low)
        scan_help = next(a for a in sub["scan"]._actions if "--include-low" in a.option_strings)
        assert scan_help.help is not None
        self.assertIn("원본성 근거 있음", scan_help.help)
        self.assertNotIn("low-signal", scan_help.help)

    def test_gui_does_not_call_scores_review_priority(self) -> None:
        """R16: the GUI note no longer says scores are a review priority."""
        gui = (Path(__file__).resolve().parents[1] / "gui.html").read_text(encoding="utf-8")
        self.assertNotIn("검토 우선순위", gui)
        self.assertIn("결론과 근거를 확인하십시오. 숫자 점수는 보정된 경우에만 표시됩니다.", gui)

    def test_no_priority_banner_anywhere_user_facing(self) -> None:
        """R16: no banner, note or limitation calls a result a (screening/review) "우선순위" signal."""
        import re

        package = Path(__file__).resolve().parents[1]
        for name in ("gui.js", "gui.html", "audio.py", "cli_render.py", "fusion.py", "reports.py", "result_text.py", "evidence_statement.py", "core.py"):
            text = (package / name).read_text(encoding="utf-8")
            strings = re.findall(r"(?:'[^'\n]*'|\"[^\"\n]*\"|`[^`]*`|>[^<]+<)", text)
            offenders = [s for s in strings if "우선순위" in s]
            with self.subTest(file=name):
                self.assertEqual(offenders, [])
        gui_js = (package / "gui.js").read_text(encoding="utf-8")
        self.assertEqual(gui_js.count("이 결과는 결론과 근거로 읽으십시오; 점수는 보정된 경우에만 표시됩니다"), 2)  # banner note + quick-check note
        audio = (package / "audio.py").read_text(encoding="utf-8")
        self.assertIn("이 결과는 결론과 근거로 읽으십시오; 점수는 보정된 경우에만 표시됩니다", audio)

    def test_html_report_groups_evidence_and_coverage(self) -> None:
        from deepfake_lens.reports import write_html_report

        path = self.root / "r.html"
        write_html_report(path, self.summary, self.items)
        html = path.read_text(encoding="utf-8")
        for needle in ("조작·생성 근거 있음", "판단 불가", "결정적 근거", "어휘적 근거", "미실행", TEXT_LEGAL_LIMITATION):
            self.assertIn(needle, html)

    def test_minimal_pdf_states_verdict_codes(self) -> None:
        from deepfake_lens.reports import write_pdf_report

        path = self.root / "r.pdf"
        write_pdf_report(path, self.summary, self.items)
        raw = path.read_bytes()
        self.assertIn(b"MANIPULATION-EVIDENCE", raw)
        self.assertIn(b"UNDETERMINED", raw)
        self.assertIn(b"no evidentiary value", raw)

    def test_evidence_statement_follows_verdict(self) -> None:
        from deepfake_lens.evidence_statement import build_evidence_statement

        statement = build_evidence_statement(self.items)
        md = statement.to_markdown()
        self.assertIn(TEXT_LEGAL_LIMITATION, md)
        by_name = {Path(entry.file_path).name: entry for entry in statement.entries}
        self.assertIn("결정적 근거", by_name["a1111.png"].purpose_of_proof)
        self.assertEqual(by_name["note.txt"].statutes, [])
        self.assertIn("참고", by_name["note.txt"].purpose_of_proof)


class TextAlwaysReferenceTest(unittest.TestCase):
    def test_every_text_shape_is_reference(self) -> None:
        for text in ("", "짧다", "As an AI language model. " * 40, "결론적으로 요약하자면 다음과 같습니다.\n- a\n- b\n- c\n- d\n- e\n- f"):
            result = analyze_text(text)
            self.assertEqual(result.grade, Grade.REFERENCE)
            self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
            self.assertEqual(result.limitations[0], TEXT_LEGAL_LIMITATION)
            self.assertEqual(result.score, 0)
            self.assertTrue(all(item.kind == EvidenceKind.LEXICAL for item in result.evidence))


if __name__ == "__main__":
    unittest.main()

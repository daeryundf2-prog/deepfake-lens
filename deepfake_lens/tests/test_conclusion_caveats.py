"""P0R-12 (user decision 2026-10-17): every manipulation or authenticity conclusion says why it could be wrong, and a
conclusion resting on self-declared metadata says so in the result and the evidence statement."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deepfake_lens.core import SourceGuess, analyze_file, build_classification_result
from deepfake_lens.evidence_rules import (
    CAVEAT_C2PA_AUTHENTIC,
    CAVEAT_MEASURED,
    CAVEATS_TITLE,
    SELF_DECLARED_NOTE,
    conclusion_caveats,
)
from deepfake_lens.result_types import (
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceStrength,
    Verdict,
)

A1111 = "a cat\nNegative prompt: x\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1"


def _png(path: Path, text: dict[str, str]) -> Path:
    from PIL import Image, PngImagePlugin

    info = PngImagePlugin.PngInfo()
    for key, value in text.items():
        info.add_text(key, value)
    Image.new("RGB", (64, 48), (10, 120, 200)).save(path, pnginfo=info)
    return path


class SelfDeclaredConclusionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.item = analyze_file(_png(Path(self._tmp.name) / "gen.png", {"parameters": A1111}))
        assert self.item.result is not None
        self.result = self.item.result

    def test_the_result_says_the_basis_is_self_declared(self) -> None:
        self.assertEqual(self.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        self.assertIn(SELF_DECLARED_NOTE, self.result.verdict)
        self.assertTrue(any(SELF_DECLARED_NOTE in text for text in self.result.conclusion_caveats))
        self.assertEqual(self.result.to_json()["conclusion_caveats"], self.result.conclusion_caveats)

    def test_the_evidence_statement_and_the_html_report_carry_it(self) -> None:
        from deepfake_lens.core import summarize
        from deepfake_lens.evidence_statement import build_evidence_statement
        from deepfake_lens.reports import write_html_report

        [entry] = build_evidence_statement([self.item]).entries
        self.assertIn(SELF_DECLARED_NOTE, entry.purpose_of_proof)
        self.assertIn(CAVEATS_TITLE, entry.purpose_of_proof)
        out = Path(self._tmp.name) / "r.html"
        write_html_report(out, summarize([self.item], capped=False), [self.item])
        self.assertIn(CAVEATS_TITLE, out.read_text(encoding="utf-8"))

    def test_a_json_round_trip_keeps_the_caveats(self) -> None:
        from deepfake_lens.core import _scan_item_from_json

        again = _scan_item_from_json(self.item.to_json())
        assert again.result is not None
        self.assertEqual(again.result.conclusion_caveats, self.result.conclusion_caveats)


class CaveatRulesTest(unittest.TestCase):
    def _result(self, evidence: list[EvidenceItem]):
        return build_classification_result(
            subject="이미지", evidence=evidence, coverage=[CoverageEntry("metadata", CoverageStatus.RAN)],
            source_guess=SourceGuess.unknown(), limitations=[], next_checks=[],
        )

    def test_a_signed_camera_capture_names_the_signature_limits(self) -> None:
        result = self._result([EvidenceItem("C2PA 서명: 카메라 촬영 출처", "d", EvidenceKind.DETERMINISTIC,
                                            EvidenceDirection.AUTHENTIC, EvidenceStrength.STRONG, "c2pa")])
        self.assertEqual(result.verdict_code, Verdict.AUTHENTICITY_EVIDENCE)
        self.assertEqual(result.conclusion_caveats, [CAVEAT_C2PA_AUTHENTIC])

    def test_a_measured_detector_names_its_corpus_limit(self) -> None:
        item = EvidenceItem("모델", "d", EvidenceKind.STATISTICAL, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "model",
                            probability=0.9, calibration_id="c", measured_on="corpus")
        self.assertEqual(conclusion_caveats(Verdict.MANIPULATION_EVIDENCE, [item]), [CAVEAT_MEASURED])

    def test_undetermined_has_none_and_every_conclusion_has_some(self) -> None:
        self.assertEqual(self._result([]).conclusion_caveats, [])
        for verdict in (Verdict.MANIPULATION_EVIDENCE, Verdict.AUTHENTICITY_EVIDENCE):
            with self.subTest(verdict=verdict):
                self.assertTrue(conclusion_caveats(verdict, []))


class RecordEvidenceWordingTest(unittest.TestCase):
    """P0R-13: 「결정적 근거」 reads as "unforgeable" — it is 「재현 가능한 기록 근거」 everywhere a person reads it, and an
    evidence-grade 판단 불가 row shows no 「감정 근거로 사용 가능」 label."""

    def test_the_kind_label_and_the_csv_header(self) -> None:
        from deepfake_lens.result_types import EVIDENCE_KIND_LABELS

        self.assertEqual(EVIDENCE_KIND_LABELS[EvidenceKind.DETERMINISTIC], "재현 가능한 기록 근거")
        gui = (Path(__file__).resolve().parents[1] / "gui.js").read_text(encoding="utf-8")
        self.assertIn("결론,등급,재현 가능한 기록 근거,통계적 근거,어휘적 근거", gui)
        package = Path(__file__).resolve().parents[1]
        offenders = [p.name for p in [*package.glob("*.py"), package / "gui.js", package / "gui.html"] if "결정적 근거" in p.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [])

    def test_no_evidence_grade_label_next_to_undetermined(self) -> None:
        from deepfake_lens.result_text import shown_grade_label, verdict_heading

        undetermined = build_classification_result(
            subject="이미지", evidence=[], coverage=[], source_guess=SourceGuess.unknown(), limitations=[], next_checks=[],
        )
        self.assertEqual(verdict_heading(undetermined), "판단 불가")
        self.assertEqual(shown_grade_label(undetermined), "")
        self.assertEqual(undetermined.to_json()["grade_label"], "감정 근거로 사용 가능")  # the JSON field is unchanged
        concluded = build_classification_result(
            subject="이미지", evidence=[EvidenceItem("x", "d", EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "metadata")],
            coverage=[], source_guess=SourceGuess.unknown(), limitations=[], next_checks=[],
        )
        self.assertEqual(verdict_heading(concluded), "조작·생성 근거 있음 [감정 근거로 사용 가능]")


if __name__ == "__main__":
    unittest.main()

"""techniques-2026 items 1–2: C2PA 2.2–2.4 reader facts, trust anchors, IPTC vocabulary.

The SDK cases use fixtures/c2pa-2.4 (scripts/build_c2pa24_fixtures.py) and
trust the fixture CA through DEEPFAKE_LENS_C2PA_TRUST_ANCHORS; without the
variable the same signer is untrusted (the bundled C2PA trust list does not
hold it). The vocabulary and rule cases need no SDK.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deepfake_lens import c2pa as c2pa_module
from deepfake_lens.c2pa import TRUST_ANCHORS_ENV, manifest_facts, reader_settings, trust_basis, validate_c2pa_manifest
from deepfake_lens.core import analyze_file, build_classification_result
from deepfake_lens.digital_source_type import (
    CATEGORY_CAPTURE,
    CATEGORY_GENERATIVE,
    CATEGORY_NEUTRAL,
    CATEGORY_SCREEN,
    CATEGORY_UNKNOWN,
    IPTC_SOURCE_TYPES,
    classify_source_type,
)
from deepfake_lens.evidence_rules import EVIDENCE_CONFLICT_TITLE, c2pa_evidence, evidence_conflicts, xmp_evidence
from deepfake_lens.result_types import (
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceStrength,
    SourceGuess,
    Verdict,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures" / "c2pa-2.4"
TEST_CA = FIXTURES / "test-ca-cert.pem"
HAVE_SDK = importlib.util.find_spec("c2pa") is not None
IPTC = "http://cv.iptc.org/newscodes/digitalsourcetype/"


def _trusted(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "present": True, "status": "valid", "state": "Trusted", "trusted": True,
        "signature": {"common_name": "Test Signer"},
        "success_codes": ["claimSignature.validated", "assertion.dataHash.match"],
        "failure_codes": [], "digital_source_types": [],
    }
    base.update(overrides)
    return base


class IptcVocabularyTest(unittest.TestCase):
    def test_full_vocabulary_with_retired_values(self) -> None:
        codes = [entry.code for entry in IPTC_SOURCE_TYPES]
        self.assertEqual(len(codes), 20)
        self.assertEqual(len(set(codes)), 20)
        retired = {entry.code for entry in IPTC_SOURCE_TYPES if entry.retired}
        self.assertEqual(retired, {"minorHumanEdits", "digitalArt", "softwareImage"})

    def test_categories_distinguish_screen_and_composites(self) -> None:
        cases = {
            "screenCapture": CATEGORY_SCREEN,
            "compositeSynthetic": CATEGORY_GENERATIVE,
            "compositeWithTrainedAlgorithmicMedia": CATEGORY_GENERATIVE,
            "trainedAlgorithmicMedia": CATEGORY_GENERATIVE,
            "composite": CATEGORY_NEUTRAL,
            "compositeCapture": CATEGORY_NEUTRAL,
            "digitalCapture": CATEGORY_CAPTURE,
            "computationalCapture": CATEGORY_NEUTRAL,
            "minorHumanEdits": CATEGORY_NEUTRAL,
        }
        for code, category in cases.items():
            with self.subTest(code=code):
                self.assertEqual(classify_source_type(IPTC + code).category, category)
                self.assertEqual(classify_source_type(code).category, category)

    def test_c2pa_namespace_and_legacy_value(self) -> None:
        self.assertEqual(classify_source_type("http://c2pa.org/digitalsourcetype/trainedAlgorithmicData").category, CATEGORY_GENERATIVE)
        self.assertEqual(classify_source_type("c2pa.trainedAlgorithmicData").category, CATEGORY_GENERATIVE)
        self.assertEqual(classify_source_type("http://c2pa.org/digitalsourcetype/empty").category, CATEGORY_NEUTRAL)

    def test_other_namespace_is_never_mapped_by_last_segment(self) -> None:
        declared = classify_source_type("https://example.com/vocab/trainedAlgorithmicMedia")
        self.assertEqual(declared.category, CATEGORY_UNKNOWN)
        self.assertIn("알 수 없는 값", declared.display)

    def test_xmp_screen_capture_is_neutral_self_declaration(self) -> None:
        [item] = xmp_evidence({"xmp.DigitalSourceType": IPTC + "screenCapture"})
        self.assertEqual((item.direction, item.strength), (EvidenceDirection.NEUTRAL, EvidenceStrength.MODERATE))
        self.assertIn("자기 신고", item.title)

    def test_xmp_composite_with_ai_is_synthetic_and_named(self) -> None:
        [item] = xmp_evidence({"xmp.DigitalSourceType": IPTC + "compositeWithTrainedAlgorithmicMedia"})
        self.assertEqual((item.direction, item.strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG))
        self.assertIn("생성형 AI로 편집", item.detail)
        self.assertIn("자기 신고", item.detail)


class ManifestFactsTest(unittest.TestCase):
    MANIFEST = {
        "claim_generator_info": [{"name": "gen", "specVersion": "2.4"}],
        "claim_version": 2,
        "assertions": [
            {"label": "c2pa.actions.v2", "data": {"actions": [
                {"action": "c2pa.created", "digitalSourceType": IPTC + "trainedAlgorithmicMedia"},
                {"action": "c2pa.watermarked.bound"},
            ]}},
            {"label": "c2pa.soft-binding", "data": {"alg": "com.adobe.trustmark.P", "blocks": [{"scope": {}, "value": [1, 2, 255]}]}},
            {"label": "c2pa.ai-disclosure", "data": {"modelType": "c2pa.types.model.llm", "contentProfile": {"humanOversightLevel": "prompt_guided"}}},
            {"label": "c2pa.embedded-data", "data": {"dc:format": "text/plain"}},
        ],
        "ingredients": [{"title": "prompt", "relationship": "inputTo", "digitalSourceType": "http://c2pa.org/digitalsourcetype/trainedAlgorithmicData"}],
    }

    def test_facts(self) -> None:
        facts = manifest_facts(self.MANIFEST)
        self.assertEqual(facts["claim_spec_version"], "2.4")
        self.assertEqual(facts["watermark_actions"], ["c2pa.watermarked.bound"])
        self.assertEqual(facts["soft_bindings"], [{"alg": "com.adobe.trustmark.P", "values": ["0102ff"]}])
        self.assertEqual(facts["ai_disclosures"][0]["humanOversightLevel"], "prompt_guided")
        self.assertEqual(facts["embedded_data"], [{"label": "c2pa.embedded-data", "format": "text/plain"}])
        wheres = sorted((d["where"], d["value"].rsplit("/", 1)[-1]) for d in facts["source_type_declarations"])
        self.assertEqual(wheres, [("action", "trainedAlgorithmicMedia"), ("ingredient", "trainedAlgorithmicData")])

    def test_untrusted_input_is_bounded(self) -> None:
        manifest = {"assertions": [{"label": "c2pa.soft-binding", "data": {"alg": "x" * 5000, "blocks": [{"value": "v"}] * 100}}] * 100}
        facts = manifest_facts(manifest)
        self.assertLessEqual(len(facts["soft_bindings"]), c2pa_module.FACT_MAX_ITEMS)
        self.assertLessEqual(len(facts["soft_bindings"][0]["alg"]), c2pa_module.FACT_MAX_TEXT)


class C2pa2xEvidenceRuleTest(unittest.TestCase):
    def _decl(self, *pairs: tuple[str, str]) -> list[dict[str, str]]:
        return [{"value": value, "where": where, "action": "c2pa.created"} for where, value in pairs]

    def test_trusted_ai_disclosure_alone_is_strong_synthetic(self) -> None:
        items = c2pa_evidence(_trusted(ai_disclosures=[{"modelType": "c2pa.types.model.diffusion"}]))
        self.assertEqual((items[0].direction, items[0].strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG))
        self.assertIn("c2pa.ai-disclosure", items[0].detail)

    def test_untrusted_ai_disclosure_is_moderate(self) -> None:
        items = c2pa_evidence(_trusted(trusted=False, status="invalid", failure_codes=["signingCredential.untrusted"],
                                       ai_disclosures=[{"modelType": "x"}]))
        self.assertEqual((items[0].direction, items[0].strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE))

    def test_tampered_ai_disclosure_is_never_strong(self) -> None:
        items = c2pa_evidence(_trusted(failure_codes=["assertion.dataHash.mismatch"], ai_disclosures=[{"modelType": "x"}]))
        self.assertFalse(any(i.strength == EvidenceStrength.STRONG and i.direction == EvidenceDirection.SYNTHETIC for i in items))

    def test_ingredient_level_ai_is_moderate_not_deciding(self) -> None:
        items = c2pa_evidence(_trusted(source_type_declarations=self._decl(("ingredient", IPTC + "trainedAlgorithmicMedia"))))
        ingredient = [i for i in items if "ingredient" in i.title]
        self.assertEqual([(i.direction, i.strength) for i in ingredient], [(EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE)])
        self.assertNotEqual(items[0].direction, EvidenceDirection.SYNTHETIC)

    def test_bmff_hash_match_counts_as_content_binding(self) -> None:
        items = c2pa_evidence(_trusted(success_codes=["assertion.bmffHash.match"], digital_source_types=["digitalCapture"]))
        self.assertEqual(items[0].direction, EvidenceDirection.AUTHENTIC)

    def test_unknown_namespace_does_not_decide(self) -> None:
        items = c2pa_evidence(_trusted(source_type_declarations=self._decl(("action", "https://evil.example/trainedAlgorithmicMedia"))))
        self.assertNotEqual(items[0].direction, EvidenceDirection.SYNTHETIC)
        self.assertTrue(any("알 수 없는 값" in i.detail for i in items))

    def test_soft_binding_and_embedded_data_are_neutral_facts(self) -> None:
        items = c2pa_evidence(_trusted(soft_bindings=[{"alg": "com.adobe.trustmark.Q", "values": ["2*0101"]}],
                                       watermark_actions=["c2pa.watermarked.bound"], embedded_data=[{"label": "c2pa.embedded-data", "format": ""}]))
        facts = {i.title: i for i in items[1:]}
        self.assertEqual(facts["C2PA 소프트 바인딩·워터마크 선언"].direction, EvidenceDirection.NEUTRAL)
        self.assertIn("com.adobe.trustmark.Q", facts["C2PA 소프트 바인딩·워터마크 선언"].detail)
        self.assertEqual(facts["C2PA 내장 데이터(embedded data) assertion"].strength, EvidenceStrength.WEAK)


class EvidenceConflictTest(unittest.TestCase):
    """Coordinator addition: a trusted camera signature + strong synthetic self-declaration are both shown."""

    def test_conflict_is_named_and_both_items_kept(self) -> None:
        authentic = c2pa_evidence(_trusted(digital_source_types=["digitalCapture"]))[0]
        [synthetic] = xmp_evidence({"xmp.DigitalSourceType": IPTC + "trainedAlgorithmicMedia"})
        result = build_classification_result(
            subject="이미지", evidence=[authentic, synthetic], coverage=[CoverageEntry("c2pa", CoverageStatus.RAN)],
            source_guess=SourceGuess.unknown(), limitations=[], next_checks=[],
        )
        titles = [item.title for item in result.evidence]
        self.assertIn(authentic.title, titles)
        self.assertIn(synthetic.title, titles)
        self.assertIn(EVIDENCE_CONFLICT_TITLE, titles)
        # P0R-10: the decision honours the conflict — undetermined, not "manipulation" by rule 2
        from deepfake_lens.result_types import Verdict

        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)

    def test_a_rebuilt_result_names_the_conflict_once(self) -> None:
        """A document result is rebuilt from its own evidence (_apply_document_metadata) — one conflict item, not two."""
        authentic = c2pa_evidence(_trusted(digital_source_types=["digitalCapture"]))[0]
        [synthetic] = xmp_evidence({"xmp.DigitalSourceType": IPTC + "trainedAlgorithmicMedia"})
        first = build_classification_result(
            subject="글", evidence=[authentic, synthetic], coverage=[], source_guess=SourceGuess.unknown(), limitations=[], next_checks=[],
        )
        rebuilt = build_classification_result(
            subject="글", evidence=list(first.evidence), coverage=[], source_guess=SourceGuess.unknown(), limitations=[], next_checks=[],
        )
        self.assertEqual([item.title for item in rebuilt.evidence].count(EVIDENCE_CONFLICT_TITLE), 1)

    def test_no_conflict_item_without_both_sides(self) -> None:
        authentic = c2pa_evidence(_trusted(digital_source_types=["digitalCapture"]))[0]
        self.assertEqual(evidence_conflicts([authentic]), [])
        weak = EvidenceItem("x", "y", EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE, "metadata")
        self.assertEqual(evidence_conflicts([authentic, weak]), [])


class OfflineTrustSettingsTest(unittest.TestCase):
    def test_reader_runs_offline_against_the_bundled_list(self) -> None:
        settings = reader_settings()
        self.assertEqual(settings["verify"], {"remote_manifest_fetch": False, "ocsp_fetch": False, "verify_trust": True})
        self.assertIn("-----BEGIN CERTIFICATE-----", settings["trust"]["trust_anchors"])
        basis = trust_basis()
        meta = json.loads(c2pa_module.TRUST_BUNDLE_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual((basis["id"], basis["sha256"]), (meta["id"], meta["sha256"]))

    def test_changed_trust_list_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            shutil.copyfile(c2pa_module.TRUST_BUNDLE_MANIFEST, folder / "c2pa-trust.json")
            (folder / "c2pa-trust-list.pem").write_text("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n", encoding="ascii")
            with mock.patch.object(c2pa_module, "TRUST_BUNDLE_MANIFEST", folder / "c2pa-trust.json"):
                with self.assertRaises(c2pa_module.C2paTrustError):
                    reader_settings()
                if HAVE_SDK:
                    summary = validate_c2pa_manifest(FIXTURES / "ai-generated.png")
                    assert summary is not None
                    self.assertEqual((summary["status"], summary["error_kind"]), ("unavailable", "trust"))
                    item = analyze_file(FIXTURES / "ai-generated.png")
                    assert item.result is not None
                    [entry] = [e for e in item.result.coverage if e.check == "c2pa"]
                    self.assertEqual(entry.status, CoverageStatus.FAILED)
                    self.assertIn("신뢰 목록 무결성 불일치", entry.reason)

    def test_unreadable_operator_anchors_fail_closed(self) -> None:
        with mock.patch.dict(os.environ, {TRUST_ANCHORS_ENV: str(REPO / "no-such-anchors.pem")}):
            with self.assertRaises(c2pa_module.C2paTrustError):
                reader_settings()

    def test_text_embedding_is_skipped_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            path.write_text("사람이 쓴 글입니다. " * 10, encoding="utf-8")
            item = analyze_file(path)
            assert item.result is not None
            [entry] = [e for e in item.result.coverage if e.check == "c2pa"]
            self.assertEqual(entry.status, CoverageStatus.SKIPPED)
            self.assertIn("텍스트·HTML 임베딩", entry.reason)


@unittest.skipUnless(HAVE_SDK and (FIXTURES / "ai-generated.png").is_file(), "c2pa-python and fixtures/c2pa-2.4 required")
class C2pa24FixtureTest(unittest.TestCase):
    def _scan(self, name: str, *, trust_test_ca: bool = True):
        env = {TRUST_ANCHORS_ENV: str(TEST_CA)} if trust_test_ca else {}
        with mock.patch.dict(os.environ, env):
            if not trust_test_ca:
                os.environ.pop(TRUST_ANCHORS_ENV, None)
            item = analyze_file(FIXTURES / name)
        assert item.result is not None
        return item.result

    def test_trusted_generative_manifest_is_manipulation_evidence(self) -> None:
        result = self._scan("ai-generated.png")
        self.assertEqual(result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        [main] = [e for e in result.evidence if e.title == "C2PA 서명: 생성형 AI 출처 선언"]
        self.assertEqual((main.kind, main.strength), (EvidenceKind.DETERMINISTIC, EvidenceStrength.STRONG))
        self.assertIn("C2PA 2.4", main.detail)
        self.assertIn("c2pa.ai-disclosure", main.detail)
        self.assertTrue(any(e.title == "C2PA 소프트 바인딩·워터마크 선언" and "c2pa.watermarked.bound" in e.detail for e in result.evidence))
        [entry] = [e for e in result.coverage if e.check == "c2pa"]
        self.assertEqual(entry.status, CoverageStatus.RAN)
        self.assertIn("검증기 C2PA 2.4", entry.reason)
        self.assertIn("C2PA-TRUST-LIST", entry.reason)

    def test_ai_disclosure_alone_is_manipulation_evidence(self) -> None:
        self.assertEqual(self._scan("ai-disclosure-only.png").verdict_code, Verdict.MANIPULATION_EVIDENCE)

    def test_composite_with_ai_is_manipulation_evidence(self) -> None:
        self.assertEqual(self._scan("composite-ai.png").verdict_code, Verdict.MANIPULATION_EVIDENCE)

    def test_trusted_camera_capture_is_authenticity_evidence(self) -> None:
        result = self._scan("camera-capture.png")
        self.assertEqual(result.verdict_code, Verdict.AUTHENTICITY_EVIDENCE)
        self.assertTrue(any(e.title == "C2PA 서명: 카메라 촬영 출처" and e.strength == EvidenceStrength.STRONG for e in result.evidence))

    def test_trusted_screen_capture_is_not_authenticity(self) -> None:
        result = self._scan("screen-capture.png")
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        self.assertTrue(any(e.title == "C2PA: 화면 캡처 출처 선언" for e in result.evidence))

    def test_expired_signer_is_moderate_with_reason(self) -> None:
        result = self._scan("expired-signer.png")
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        [main] = [e for e in result.evidence if e.layer == "c2pa" and e.direction == EvidenceDirection.SYNTHETIC]
        self.assertEqual(main.strength, EvidenceStrength.MODERATE)
        self.assertIn("signingCredential.expired", main.detail)

    def test_untrusted_chain_is_moderate_with_reason(self) -> None:
        result = self._scan("other-ca.png")
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        [main] = [e for e in result.evidence if e.layer == "c2pa" and e.direction == EvidenceDirection.SYNTHETIC]
        self.assertEqual(main.strength, EvidenceStrength.MODERATE)
        self.assertIn("signingCredential.untrusted", main.detail)

    def test_without_operator_anchor_the_test_ca_is_untrusted(self) -> None:
        result = self._scan("ai-generated.png", trust_test_ca=False)
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        self.assertIn("signingCredential.untrusted", " ".join(e.detail for e in result.evidence))

    def test_validator_spec_version_is_recorded(self) -> None:
        with mock.patch.dict(os.environ, {TRUST_ANCHORS_ENV: str(TEST_CA)}):
            summary = validate_c2pa_manifest(FIXTURES / "ai-generated.png")
        assert summary is not None
        self.assertTrue(str(summary["spec_version"]).startswith("2."))
        self.assertEqual(summary["soft_bindings"][0]["alg"], "com.adobe.trustmark.Q")
        self.assertEqual(summary["ai_disclosures"][0]["modelName"], "DeepfakeLensTestGenerator")


if __name__ == "__main__":
    unittest.main()

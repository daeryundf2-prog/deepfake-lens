"""Exhaustive tests for decision.decide (phase 0, WP-A, G5/G6).

Each rule is exercised at its boundaries: the kind, direction and strength
that make it fire, and the neighbours that must not.
"""

from __future__ import annotations

import unittest

from deepfake_lens.decision import decide
from deepfake_lens.result_types import (
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceStrength,
    Grade,
    RiskBand,
    Verdict,
    band_for_verdict,
)

D, S, L = EvidenceKind.DETERMINISTIC, EvidenceKind.STATISTICAL, EvidenceKind.LEXICAL
SYN, AUTH, NEU = EvidenceDirection.SYNTHETIC, EvidenceDirection.AUTHENTIC, EvidenceDirection.NEUTRAL
STRONG, MOD, WEAK = EvidenceStrength.STRONG, EvidenceStrength.MODERATE, EvidenceStrength.WEAK
EV, REF = Grade.EVIDENCE, Grade.REFERENCE
MANIP, AUTHV, UNDET = Verdict.MANIPULATION_EVIDENCE, Verdict.AUTHENTICITY_EVIDENCE, Verdict.UNDETERMINED


def item(kind, direction, strength, **kwargs) -> EvidenceItem:
    return EvidenceItem("t", "d", kind, direction, strength, "test", **kwargs)


def calibrated(p: float | None, *, cal: str | None = "cal-1", measured: str | None = "corpus-1@test",
               direction=SYN) -> EvidenceItem:
    return item(S, direction, MOD, probability=p, calibration_id=cal, measured_on=measured, probability_ci=(0.1, 0.9))


# G8 (round 5): rule 4 uses the loaded profile's threshold for the item's
# calibration id; the former 0.5 fallback (DEFAULT_PROBABILITY_THRESHOLD) is
# gone. The tests that relied on it now pass this profile threshold.
PROFILE_THRESHOLD = 0.5
CAL_THRESHOLDS = {"cal-1": PROFILE_THRESHOLD}

RAN = CoverageEntry("metadata", CoverageStatus.RAN)
SKIP = CoverageEntry("external_model", CoverageStatus.SKIPPED, "모델 프로필 미지정")
FAIL = CoverageEntry("external_model", CoverageStatus.FAILED, "RuntimeError: boom")

DET_SYN_STRONG = item(D, SYN, STRONG)
DET_AUTH_STRONG = item(D, AUTH, STRONG)


class Rule1ReferenceGradeTest(unittest.TestCase):
    def test_reference_overrides_strong_deterministic_synthetic(self) -> None:
        self.assertEqual(decide([DET_SYN_STRONG], [RAN], REF), UNDET)

    def test_reference_overrides_calibrated_statistical(self) -> None:
        self.assertEqual(decide([calibrated(0.99)], [RAN], REF), UNDET)

    def test_reference_overrides_authenticity(self) -> None:
        self.assertEqual(decide([DET_AUTH_STRONG], [RAN], REF), UNDET)

    def test_reference_with_no_evidence(self) -> None:
        self.assertEqual(decide([], [], REF), UNDET)


class Rule2DeterministicSyntheticTest(unittest.TestCase):
    def test_strong_deterministic_synthetic_concludes(self) -> None:
        self.assertEqual(decide([DET_SYN_STRONG], [RAN], EV), MANIP)

    def test_survives_a_failed_check(self) -> None:
        self.assertEqual(decide([DET_SYN_STRONG], [RAN, FAIL], EV), MANIP)

    def test_moderate_is_not_enough(self) -> None:
        self.assertEqual(decide([item(D, SYN, MOD)], [RAN], EV), UNDET)

    def test_weak_is_not_enough(self) -> None:
        self.assertEqual(decide([item(D, SYN, WEAK)], [RAN], EV), UNDET)

    def test_beats_strong_authenticity(self) -> None:
        self.assertEqual(decide([DET_AUTH_STRONG, DET_SYN_STRONG], [RAN], EV), MANIP)

    def test_strong_statistical_without_calibration_does_not_conclude(self) -> None:
        self.assertEqual(decide([item(S, SYN, STRONG)], [RAN], EV), UNDET)

    def test_strong_lexical_does_not_conclude(self) -> None:
        self.assertEqual(decide([item(L, SYN, STRONG)], [RAN], EV), UNDET)

    def test_strong_deterministic_neutral_does_not_conclude(self) -> None:
        self.assertEqual(decide([item(D, NEU, STRONG)], [RAN], EV), UNDET)


class Rule3FailClosedTest(unittest.TestCase):
    def test_failure_blocks_calibrated_statistical(self) -> None:
        self.assertEqual(decide([calibrated(0.99)], [FAIL], EV), UNDET)

    def test_failure_blocks_authenticity(self) -> None:
        self.assertEqual(decide([DET_AUTH_STRONG], [RAN, FAIL], EV), UNDET)

    def test_skipped_is_not_failed(self) -> None:
        self.assertEqual(decide([DET_AUTH_STRONG], [RAN, SKIP], EV), AUTHV)

    def test_failure_alone_is_undetermined(self) -> None:
        self.assertEqual(decide([], [FAIL], EV), UNDET)


class Rule4CalibratedStatisticalTest(unittest.TestCase):
    def test_probability_equal_to_threshold_concludes(self) -> None:
        # G8: the threshold is the profile's (was the implicit 0.5 default).
        self.assertEqual(decide([calibrated(PROFILE_THRESHOLD)], [RAN], EV, CAL_THRESHOLDS), MANIP)

    def test_probability_just_below_threshold_does_not(self) -> None:
        # G8: the threshold is the profile's (was the implicit 0.5 default).
        self.assertEqual(decide([calibrated(PROFILE_THRESHOLD - 1e-9)], [RAN], EV, CAL_THRESHOLDS), UNDET)

    def test_missing_calibration_id_does_not(self) -> None:
        self.assertEqual(decide([calibrated(0.99, cal=None)], [RAN], EV), UNDET)

    def test_missing_measured_on_does_not(self) -> None:
        self.assertEqual(decide([calibrated(0.99, measured=None)], [RAN], EV), UNDET)

    def test_missing_probability_does_not(self) -> None:
        self.assertEqual(decide([calibrated(None)], [RAN], EV), UNDET)

    def test_profile_threshold_is_respected(self) -> None:
        thresholds = {"cal-1": 0.8}
        self.assertEqual(decide([calibrated(0.7)], [RAN], EV, thresholds), UNDET)
        self.assertEqual(decide([calibrated(0.8)], [RAN], EV, thresholds), MANIP)

    def test_threshold_for_other_calibration_does_not_apply(self) -> None:
        # G8 (round 5): encoded the defect — a calibration id without a
        # profile threshold fell back to 0.5 and concluded (was MANIP).
        self.assertEqual(decide([calibrated(0.6)], [RAN], EV, {"cal-other": 0.95}), UNDET)

    def test_no_thresholds_at_all_never_fires_rule_4(self) -> None:
        """G8: without a profile threshold for the calibration id, rule 4 is skipped."""
        for probability in (0.5, 0.9, 1.0):
            with self.subTest(probability=probability):
                self.assertEqual(decide([calibrated(probability)], [RAN], EV), UNDET)
                self.assertEqual(decide([calibrated(probability)], [RAN], EV, {}), UNDET)

    def test_each_calibration_id_uses_its_own_threshold(self) -> None:
        """G8: thresholds are keyed by calibration id."""
        thresholds = {"cal-a": 0.9, "cal-b": 0.3}
        self.assertEqual(decide([calibrated(0.5, cal="cal-a")], [RAN], EV, thresholds), UNDET)
        self.assertEqual(decide([calibrated(0.5, cal="cal-b")], [RAN], EV, thresholds), MANIP)

    def test_neutral_direction_does_not_conclude(self) -> None:
        self.assertEqual(decide([calibrated(0.99, direction=NEU)], [RAN], EV), UNDET)

    def test_calibrated_lexical_shape_is_ignored(self) -> None:
        lexical = item(L, SYN, MOD, probability=0.99, calibration_id="cal-1", measured_on="c")
        self.assertEqual(decide([lexical], [RAN], EV), UNDET)

    def test_calibrated_statistical_beats_authenticity(self) -> None:
        # G8: with its profile threshold (was the implicit 0.5 default).
        self.assertEqual(decide([DET_AUTH_STRONG, calibrated(0.9)], [RAN], EV, CAL_THRESHOLDS), MANIP)


class Rule5AuthenticityTest(unittest.TestCase):
    def test_strong_deterministic_authentic_concludes(self) -> None:
        self.assertEqual(decide([DET_AUTH_STRONG], [RAN], EV), AUTHV)

    def test_moderate_authentic_is_not_enough(self) -> None:
        self.assertEqual(decide([item(D, AUTH, MOD)], [RAN], EV), UNDET)

    def test_lexical_synthetic_does_not_block(self) -> None:
        self.assertEqual(decide([DET_AUTH_STRONG, item(L, SYN, WEAK)], [RAN], EV), AUTHV)

    def test_uncalibrated_statistical_synthetic_blocks(self) -> None:
        self.assertEqual(decide([DET_AUTH_STRONG, item(S, SYN, WEAK)], [RAN], EV), UNDET)

    def test_moderate_deterministic_synthetic_blocks(self) -> None:
        self.assertEqual(decide([DET_AUTH_STRONG, item(D, SYN, MOD)], [RAN], EV), UNDET)

    def test_calibrated_synthetic_below_threshold_still_blocks(self) -> None:
        self.assertEqual(decide([DET_AUTH_STRONG, calibrated(0.1)], [RAN], EV), UNDET)

    def test_neutral_items_do_not_block(self) -> None:
        self.assertEqual(decide([DET_AUTH_STRONG, item(D, NEU, WEAK), item(S, NEU, WEAK)], [RAN], EV), AUTHV)

    def test_statistical_authentic_is_not_enough(self) -> None:
        self.assertEqual(decide([item(S, AUTH, STRONG)], [RAN], EV), UNDET)


class Rule6DefaultTest(unittest.TestCase):
    def test_no_evidence(self) -> None:
        self.assertEqual(decide([], [RAN], EV), UNDET)

    def test_only_neutral(self) -> None:
        self.assertEqual(decide([item(D, NEU, WEAK)], [RAN], EV), UNDET)

    def test_only_lexical(self) -> None:
        self.assertEqual(decide([item(L, SYN, WEAK)] * 30, [RAN], EV), UNDET)


class PurityTest(unittest.TestCase):
    def test_inputs_not_mutated_and_result_repeatable(self) -> None:
        evidence = [DET_AUTH_STRONG, item(L, SYN, WEAK)]
        coverage = [RAN, SKIP]
        snapshot = (list(evidence), list(coverage))
        first = decide(evidence, coverage, EV)
        self.assertEqual(first, decide(evidence, coverage, EV))
        self.assertEqual((evidence, coverage), snapshot)

    def test_accepts_iterators(self) -> None:
        self.assertEqual(decide(iter([DET_SYN_STRONG]), iter([RAN]), EV), MANIP)

    def test_order_independent(self) -> None:
        items = [item(D, SYN, MOD), DET_AUTH_STRONG, item(L, SYN, WEAK)]
        self.assertEqual(decide(items, [RAN], EV), decide(list(reversed(items)), [RAN], EV))


class BandDerivationTest(unittest.TestCase):
    def test_band_never_medium(self) -> None:
        bands = {band_for_verdict(v) for v in Verdict}
        self.assertNotIn(RiskBand.MEDIUM, bands)
        self.assertEqual(band_for_verdict(MANIP), RiskBand.HIGH)
        self.assertEqual(band_for_verdict(AUTHV), RiskBand.LOW)
        self.assertEqual(band_for_verdict(UNDET), RiskBand.UNKNOWN)

    def test_coverage_entry_requires_reason_when_not_ran(self) -> None:
        with self.assertRaises(ValueError):
            CoverageEntry("c2pa", CoverageStatus.FAILED, "")
        with self.assertRaises(ValueError):
            CoverageEntry("c2pa", CoverageStatus.SKIPPED, "  ")
        self.assertEqual(CoverageEntry("face_manipulation", CoverageStatus.SKIPPED, "얼굴 미검출").describe(), "얼굴 검사 미실행: 얼굴 미검출")


if __name__ == "__main__":
    unittest.main()


class ProfileThresholdWiringTest(unittest.TestCase):
    """G8 (round 5): rule 4 gets the loaded profiles' ``threshold`` per calibration id.

    No caller used to pass ``probability_thresholds``, so rule 4 always cut
    at the 0.5 default whatever the profile said.
    """

    def _profile(self, folder, name: str, **fields: object):
        import json

        path = folder / f"{name}-runtime.json"
        path.write_text(json.dumps({"name": name, "runtime": "onnx", "modality": "image", **fields}), encoding="utf-8")
        return path

    def test_collects_calibration_id_to_threshold_over_profiles_and_sets(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from deepfake_lens.model_adapter import profile_probability_thresholds

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            a = self._profile(folder, "a", calibration_id="cal-a", threshold=80)
            self._profile(folder, "b", calibration_id="cal-b", threshold=35.5)
            self._profile(folder, "no-threshold", calibration_id="cal-c")
            self._profile(folder, "no-calibration", threshold=67)
            self._profile(folder, "bad", calibration_id="cal-d", threshold="high")
            self._profile(folder, "range", calibration_id="cal-e", threshold=140)
            (folder / "set.json").write_text(json.dumps({"type": "deepfake-lens-profile-set-v1", "profiles": ["a-runtime.json"]}), encoding="utf-8")
            self.assertEqual(profile_probability_thresholds(folder), {"cal-a": 0.8, "cal-b": 0.355})
            self.assertEqual(profile_probability_thresholds([a]), {"cal-a": 0.8})
            self.assertEqual(profile_probability_thresholds(folder / "set.json"), {"cal-a": 0.8})
            self.assertEqual(profile_probability_thresholds(None), {})
            self.assertEqual(profile_probability_thresholds(folder / "missing-runtime.json"), {})

    def test_packaged_profiles_contribute_no_threshold(self) -> None:
        """Phase 0: no packaged profile is calibrated, so rule 4 cannot fire."""
        from pathlib import Path

        from deepfake_lens.model_adapter import profile_probability_thresholds

        models = Path(__file__).resolve().parents[1] / "models"
        self.assertEqual(profile_probability_thresholds(models), {})

    def test_scan_uses_the_profile_threshold(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest import mock

        from deepfake_lens.core import analyze_file
        from deepfake_lens.result_types import ExternalModelAnalysis

        try:
            from deepfake_lens.tests.qa.test_qa_out import write_photo_like_png
        except ImportError as exc:  # pragma: no cover - numpy missing
            self.skipTest(f"photo fixture unavailable: {exc}")

        calibrated_output = ExternalModelAnalysis(
            available=True, score=90, confidence="high", model="cal-model", detail="보정된 모델",
            probability=0.9, probability_ci=(0.85, 0.94), calibration_id="cal-x", measured_on="corpus-x@test",
        )
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            try:
                photo = write_photo_like_png(folder / "photo.png", seed=3)
            except ImportError as exc:  # pragma: no cover - numpy missing
                self.skipTest(f"numpy unavailable: {exc}")
            cases: tuple[tuple[dict[str, object], Verdict], ...] = (
                ({"calibration_id": "cal-x", "threshold": 80}, MANIP),   # 0.9 >= 0.80
                ({"calibration_id": "cal-x", "threshold": 95}, UNDET),   # 0.9 <  0.95
                ({"calibration_id": "cal-x"}, UNDET),                    # no threshold: rule 4 skipped
                ({"calibration_id": "cal-other", "threshold": 10}, UNDET),  # another calibration id
            )
            for index, (fields, expected) in enumerate(cases):
                profile = self._profile(folder, f"p{index}", **fields)
                with self.subTest(fields=fields), mock.patch("deepfake_lens.core.analyze_external_model", return_value=calibrated_output):
                    row = analyze_file(photo, model_path=profile)
                    assert row.result is not None
                    self.assertTrue(any(e.calibration_id == "cal-x" for e in row.result.evidence), row.result.evidence)
                    self.assertEqual(row.result.verdict_code, expected)


class CalibratedDirectionFromProfileThresholdTest(unittest.TestCase):
    """N1 (=G8): a calibrated statistical item's direction comes from the
    profile threshold, not a fixed 0.5 cut.

    model_evidence used to set direction=synthetic only for p >= 0.5, so a
    profile with a measured threshold below 0.5 (p=0.40, threshold 0.30)
    could never reach rule 4 and stayed "판단 불가".
    """

    @staticmethod
    def _model(p: float):
        from deepfake_lens.result_types import ExternalModelAnalysis

        return ExternalModelAnalysis(
            available=True, score=int(round(p * 100)), confidence="medium", model="cal-model", detail="보정된 모델",
            probability=p, probability_ci=(p - 0.05, p + 0.05), calibration_id="cal-x", measured_on="corpus-x@test",
        )

    def _result(self, evidence, thresholds):
        from deepfake_lens.core import build_classification_result
        from deepfake_lens.result_types import SourceGuess

        return build_classification_result(
            subject="이미지", evidence=evidence, coverage=[RAN], source_guess=SourceGuess.unknown(),
            limitations=[], next_checks=[], probability_thresholds=thresholds,
        )

    def test_model_evidence_direction_follows_threshold(self) -> None:
        from deepfake_lens.evidence_rules import model_evidence

        cases = (({"cal-x": 0.30}, SYN), ({"cal-x": 0.45}, NEU), ({"cal-x": 0.40}, SYN), (None, NEU), ({"cal-y": 0.1}, NEU))
        for thresholds, expected in cases:
            with self.subTest(thresholds=thresholds):
                item_ = model_evidence(self._model(0.40), thresholds)
                assert item_ is not None
                self.assertEqual(item_.direction, expected)
                self.assertEqual(item_.probability, 0.40)

    def test_build_classification_result_end_to_end(self) -> None:
        from deepfake_lens.evidence_rules import model_evidence

        for thresholds, expected in (({"cal-x": 0.30}, MANIP), ({"cal-x": 0.45}, UNDET)):
            with self.subTest(thresholds=thresholds):
                model_item = model_evidence(self._model(0.40), thresholds)
                assert model_item is not None
                result = self._result([model_item], thresholds)
                self.assertEqual(result.verdict_code, expected)
                self.assertEqual(result.probability, 0.40)

    def test_builder_rederives_direction_of_prebuilt_items(self) -> None:
        """An item built with the old 0.5 rule (neutral at p=0.40) still decides by the profile threshold."""
        neutral = calibrated(0.40, cal="cal-x", direction=NEU)
        synthetic = calibrated(0.40, cal="cal-x", direction=SYN)
        for prebuilt in (neutral, synthetic):
            with self.subTest(direction=prebuilt.direction):
                low = self._result([prebuilt], {"cal-x": 0.30})
                high = self._result([prebuilt], {"cal-x": 0.45})
                self.assertEqual(low.verdict_code, MANIP)
                self.assertEqual(high.verdict_code, UNDET)
                self.assertEqual([e.direction for e in low.evidence], [SYN])
                self.assertEqual([e.direction for e in high.evidence], [NEU])

    def test_below_threshold_calibrated_item_does_not_block_authenticity(self) -> None:
        """Below its threshold the item is neutral, so rule 5 is not contradicted."""
        result = self._result([DET_AUTH_STRONG, calibrated(0.40, cal="cal-x")], {"cal-x": 0.45})
        self.assertEqual(result.verdict_code, AUTHV)

    def test_scan_folder_uses_sub_half_profile_threshold(self) -> None:
        """Scan level: a fake calibrated profile with threshold 30 and a runtime returning p=0.40."""
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder

        try:
            from deepfake_lens.tests.qa.test_qa_out import write_photo_like_png
        except ImportError as exc:  # pragma: no cover - numpy missing
            self.skipTest(f"photo fixture unavailable: {exc}")
        for threshold, expected in ((30, MANIP), (45, UNDET)):
            with self.subTest(threshold=threshold), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                folder = base / "case"
                models = base / "models"
                folder.mkdir()
                models.mkdir()
                try:
                    write_photo_like_png(folder / "photo.png", seed=3)
                except ImportError as exc:  # pragma: no cover - numpy missing
                    self.skipTest(f"numpy unavailable: {exc}")
                profile = models / "cal-runtime.json"
                profile.write_text(json.dumps({
                    "name": "cal", "runtime": "onnx", "modality": "image",
                    "calibration_id": "cal-x", "threshold": threshold,
                }), encoding="utf-8")
                options = AnalysisOptions(models_dir=models, model_path=profile)
                with mock.patch("deepfake_lens.core.analyze_external_model", return_value=self._model(0.40)):
                    _summary, items = scan_folder(folder, options)[:2]
                [row] = items
                assert row.result is not None
                self.assertEqual(row.result.verdict_code, expected)
                model_items = [e for e in row.result.evidence if e.calibration_id == "cal-x"]
                self.assertEqual(len(model_items), 1)
                self.assertEqual(model_items[0].direction, SYN if expected == MANIP else NEU)

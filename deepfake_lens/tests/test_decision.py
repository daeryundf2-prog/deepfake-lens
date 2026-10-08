"""Exhaustive tests for decision.decide (phase 0, WP-A, G5/G6).

Each rule is exercised at its boundaries: the kind, direction and strength
that make it fire, and the neighbours that must not.
"""

from __future__ import annotations

import unittest

from deepfake_lens.decision import DEFAULT_PROBABILITY_THRESHOLD, decide
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
        self.assertEqual(decide([calibrated(DEFAULT_PROBABILITY_THRESHOLD)], [RAN], EV), MANIP)

    def test_probability_just_below_threshold_does_not(self) -> None:
        self.assertEqual(decide([calibrated(DEFAULT_PROBABILITY_THRESHOLD - 1e-9)], [RAN], EV), UNDET)

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

    def test_threshold_for_other_calibration_falls_back_to_default(self) -> None:
        self.assertEqual(decide([calibrated(0.6)], [RAN], EV, {"cal-other": 0.95}), MANIP)

    def test_neutral_direction_does_not_conclude(self) -> None:
        self.assertEqual(decide([calibrated(0.99, direction=NEU)], [RAN], EV), UNDET)

    def test_calibrated_lexical_shape_is_ignored(self) -> None:
        lexical = item(L, SYN, MOD, probability=0.99, calibration_id="cal-1", measured_on="c")
        self.assertEqual(decide([lexical], [RAN], EV), UNDET)

    def test_calibrated_statistical_beats_authenticity(self) -> None:
        self.assertEqual(decide([DET_AUTH_STRONG, calibrated(0.9)], [RAN], EV), MANIP)


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

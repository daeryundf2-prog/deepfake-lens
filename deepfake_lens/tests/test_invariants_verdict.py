"""P0R-2 (user decision 2026-10-17, risk-based exit): property-based tests of the VERDICT invariants (HANDOFF §8.1).

Hypothesis generates evidence / coverage / grade / threshold combinations for the pure decision rule
(:func:`decision.decide_with_rule`) and check failures for the real scan path (:func:`analysis_api.scan_file_run`):

(i)   no check exception escapes 판단 불가 — a failed check leaves the file undetermined unless a strong
      reproducible-record (deterministic) synthetic item decides by rule 2, and no exception leaves the scan;
(ii)  lexical items never change a conclusion; uncalibrated statistical items can only withhold one (they may block
      rule 5, never produce a conclusion); reference-grade results never conclude;
(iii) every conclusion has its supporting item in the result — a strong deterministic item of the conclusion's
      direction, or a calibrated, measured statistical item at or above its profile threshold.

Fixed-seed fuzzing of fixtures (the earlier invariant tests) only saw the shapes the fixtures held (R26-1); these
search the space. The examples are bounded (settings below) so the suite stays fast and deterministic
(``derandomize=True``: the same examples every run, so a gate failure reproduces).
"""

from __future__ import annotations

import logging
import tempfile
import unittest
import wave
from pathlib import Path

from deepfake_lens.tests.png_fixture import write_png
from unittest import mock

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from deepfake_lens.decision import decide_with_rule
from deepfake_lens.result_types import (
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceStrength,
    Grade,
    Verdict,
)

PROPERTY_SETTINGS = settings(max_examples=300, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow])
PIPELINE_SETTINGS = settings(max_examples=25, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow])

CALIBRATIONS = ("cal-a", "cal-b", "cal-c")

probabilities = st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0, allow_nan=False))


@st.composite
def evidence_items(draw: st.DrawFn) -> EvidenceItem:
    kind = draw(st.sampled_from(list(EvidenceKind)))
    statistical = kind == EvidenceKind.STATISTICAL
    return EvidenceItem(
        title=draw(st.sampled_from(["메타데이터", "C2PA", "모델", "키워드", "근거"])),
        detail="d",
        kind=kind,
        direction=draw(st.sampled_from(list(EvidenceDirection))),
        strength=draw(st.sampled_from(list(EvidenceStrength))),
        layer=draw(st.sampled_from(["metadata", "c2pa", "model", "text"])),
        probability=draw(probabilities) if statistical else None,
        calibration_id=draw(st.one_of(st.none(), st.sampled_from(CALIBRATIONS))) if statistical else None,
        measured_on=draw(st.one_of(st.none(), st.just("corpus-v1"))) if statistical else None,
    )


coverage_entries = st.builds(
    CoverageEntry,
    st.sampled_from(["metadata", "c2pa", "pixel", "external_model", "audio_analysis"]),
    st.sampled_from(list(CoverageStatus)),
    st.just("사유"),
)
thresholds = st.one_of(
    st.none(),
    st.dictionaries(st.sampled_from(CALIBRATIONS), st.floats(min_value=0.0, max_value=1.0, allow_nan=False), max_size=3),
)
grades = st.sampled_from(list(Grade))


def _is_uncalibrated_statistical(item: EvidenceItem) -> bool:
    return item.kind == EvidenceKind.STATISTICAL and not item.is_calibrated


def _supports(item: EvidenceItem, verdict: Verdict, profile: dict[str, float] | None) -> bool:
    if verdict == Verdict.MANIPULATION_EVIDENCE:
        if item.kind == EvidenceKind.DETERMINISTIC and item.direction == EvidenceDirection.SYNTHETIC and item.strength == EvidenceStrength.STRONG:
            return True
        return (
            item.kind == EvidenceKind.STATISTICAL and item.direction == EvidenceDirection.SYNTHETIC and item.is_calibrated
            and profile is not None and item.calibration_id in profile and item.probability is not None
            and item.probability >= float(profile[item.calibration_id])
        )
    if verdict == Verdict.AUTHENTICITY_EVIDENCE:
        return item.kind == EvidenceKind.DETERMINISTIC and item.direction == EvidenceDirection.AUTHENTIC and item.strength == EvidenceStrength.STRONG
    return False


class DecisionRuleInvariantTest(unittest.TestCase):
    """The pure decision rule over generated evidence, coverage, grade and thresholds."""

    @PROPERTY_SETTINGS
    @given(st.lists(evidence_items(), max_size=8), st.lists(coverage_entries, max_size=5), grades, thresholds)
    def test_a_conclusion_always_has_its_supporting_item(self, items, coverage, grade, profile) -> None:
        verdict, _rule = decide_with_rule(items, coverage, grade, profile)
        if verdict != Verdict.UNDETERMINED:
            self.assertTrue(any(_supports(item, verdict, profile) for item in items), (verdict, items, profile))

    @PROPERTY_SETTINGS
    @given(st.lists(evidence_items(), max_size=8), st.lists(coverage_entries, max_size=5), thresholds)
    def test_reference_grade_never_concludes(self, items, coverage, profile) -> None:
        self.assertEqual(decide_with_rule(items, coverage, Grade.REFERENCE, profile)[0], Verdict.UNDETERMINED)

    @PROPERTY_SETTINGS
    @given(st.lists(evidence_items(), max_size=8), st.lists(coverage_entries, max_size=5), grades, thresholds)
    def test_lexical_items_never_change_the_verdict(self, items, coverage, grade, profile) -> None:
        without = [item for item in items if item.kind != EvidenceKind.LEXICAL]
        self.assertEqual(decide_with_rule(items, coverage, grade, profile)[0], decide_with_rule(without, coverage, grade, profile)[0])

    @PROPERTY_SETTINGS
    @given(st.lists(evidence_items(), max_size=8), st.lists(coverage_entries, max_size=5), grades, thresholds)
    def test_uncalibrated_statistical_items_can_only_withhold_a_conclusion(self, items, coverage, grade, profile) -> None:
        without = [item for item in items if not _is_uncalibrated_statistical(item)]
        with_them = decide_with_rule(items, coverage, grade, profile)[0]
        self.assertIn(with_them, {decide_with_rule(without, coverage, grade, profile)[0], Verdict.UNDETERMINED})

    @PROPERTY_SETTINGS
    @given(st.lists(evidence_items(), max_size=8), st.lists(coverage_entries, max_size=5), grades, thresholds)
    def test_strong_record_evidence_in_both_directions_never_concludes(self, items, coverage, grade, profile) -> None:
        """P0R-10: a strong deterministic synthetic and a strong deterministic authentic item together are a conflict."""
        both = [*items,
                EvidenceItem("생성", "d", EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "metadata"),
                EvidenceItem("서명", "d", EvidenceKind.DETERMINISTIC, EvidenceDirection.AUTHENTIC, EvidenceStrength.STRONG, "c2pa")]
        verdict, _rule = decide_with_rule(both, coverage, grade, profile)
        self.assertNotEqual(verdict, Verdict.AUTHENTICITY_EVIDENCE)
        if verdict == Verdict.MANIPULATION_EVIDENCE:  # only a measured statistical item can still decide (rule 4)
            self.assertEqual(_rule, 4)

    @PROPERTY_SETTINGS
    @given(st.lists(evidence_items(), max_size=8), st.lists(coverage_entries, min_size=1, max_size=5), grades, thresholds)
    def test_a_failed_check_leaves_the_file_undetermined_unless_rule_2_decides(self, items, coverage, grade, profile) -> None:
        coverage = [*coverage, CoverageEntry("pixel", CoverageStatus.FAILED, "ValueError: x")]
        verdict, rule = decide_with_rule(items, coverage, grade, profile)
        if verdict != Verdict.UNDETERMINED:
            self.assertEqual((verdict, rule), (Verdict.MANIPULATION_EVIDENCE, 2))
            self.assertFalse(any(  # P0R-10: rule 2 never fires against strong authenticity
                i.kind == EvidenceKind.DETERMINISTIC and i.direction == EvidenceDirection.AUTHENTIC and i.strength == EvidenceStrength.STRONG
                for i in items))


# --- the scan path --------------------------------------------------------------------------------------------------

EXCEPTIONS = (ValueError, RuntimeError, KeyError, ZeroDivisionError, TypeError, IndexError, AttributeError, OverflowError, MemoryError)


def _png(path: Path, text: dict[str, str]) -> Path:
    # Pillow-free (the API venv may lack Pillow): PNG with tEXt chunks
    return write_png(path, text)


def _wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x01" * 8000)


class ScanPathExceptionInvariantTest(unittest.TestCase):
    """A check that raises (any check, any exception type) never yields a conclusion it does not support."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        _png(root / "a1111.png", {"parameters": "a cat\nNegative prompt: x\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1"})
        _png(root / "plain.png", {})
        _wav(root / "tone.wav")
        (root / "note.txt").write_text("평범한 메모입니다. " * 20, encoding="utf-8")
        cls.files = sorted(root.iterdir())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _scan(self, path: Path, failing: str | None, exc_type: type[BaseException]):
        from deepfake_lens import checks, core
        from deepfake_lens.analysis_api import AnalysisOptions, scan_file_run

        original = checks.run_check
        seen: list[str] = []

        def run_check(name, fn, **kwargs):
            seen.append(name)
            if name == failing:
                def boom():
                    raise exc_type("주입된 실패")
                return original(name, boom, **kwargs)
            return original(name, fn, **kwargs)

        patches = [mock.patch.object(core, "run_check", run_check)]
        try:
            from deepfake_lens import media_marks
        except ImportError:  # pragma: no cover - optional module
            media_marks = None
        if media_marks is not None and hasattr(media_marks, "run_check"):
            patches.append(mock.patch.object(media_marks, "run_check", run_check))
        quiet = logging.getLogger("deepfake_lens")  # the injected failures' tracebacks are expected
        level = quiet.level
        quiet.setLevel(logging.CRITICAL)
        for patch in patches:
            patch.start()
        try:
            run = scan_file_run(path, AnalysisOptions(pixel_mode="off"))
        finally:
            for patch in patches:
                patch.stop()
            quiet.setLevel(level)
        return run, seen

    def _check_names(self, path: Path) -> list[str]:
        _run, seen = self._scan(path, None, ValueError)
        return sorted(set(seen))

    @PIPELINE_SETTINGS
    @given(st.data())
    def test_an_injected_check_failure_never_concludes_without_rule_2(self, data) -> None:
        path = data.draw(st.sampled_from(self.files), label="file")
        names = self._check_names(path)
        self.assertTrue(names, path)
        failing = data.draw(st.sampled_from(names), label="failing check")
        exc_type = data.draw(st.sampled_from(EXCEPTIONS), label="exception")
        run, _ = self._scan(path, failing, exc_type)  # (i): never raises out of the scan
        for item in run.items:
            result = item.result
            if result is None:
                self.assertIn(item.status, {"failed", "skipped", "unknown"}, item)
                continue
            failed = [entry for entry in result.coverage if entry.status == CoverageStatus.FAILED]
            if failing in {entry.check for entry in result.coverage}:
                self.assertTrue(failed, (failing, result.coverage))
            if failed and result.verdict_code != Verdict.UNDETERMINED:
                self.assertEqual(result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
                self.assertTrue(any(_supports(e, Verdict.MANIPULATION_EVIDENCE, None) for e in result.evidence), result.evidence)
            if result.verdict_code != Verdict.UNDETERMINED:  # (iii) on the real result JSON
                as_json = result.to_json()["evidence"]
                self.assertTrue(any(
                    e["kind"] == "deterministic" and e["strength"] == "strong" for e in as_json
                ) or any(e.get("calibration_id") for e in as_json), as_json)


if __name__ == "__main__":
    unittest.main()

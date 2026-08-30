"""Regression tests for the Phase 1 math fixes.

Each test pins behavior that was previously provably wrong:
- audio fluency compared onsets/second against BPM without unit conversion
- multimodal added a consistency bonus even when every modality was clean
- fusion double-counted the source-derived signal and was not idempotent
"""

from __future__ import annotations

import unittest

from deepfake_lens.audio import AudioFeatures, _fluency_analysis
from deepfake_lens.core import (
    ClassificationResult,
    EvidenceSignal,
    RiskBand,
    SourceConfidence,
    SourceGuess,
)
from deepfake_lens.fusion import (
    DEFAULT_FUSION_PROFILE,
    apply_fusion_to_result,
    component_scores,
    fused_score,
)
from deepfake_lens.multimodal import analyze_multimodal


def _has_numpy_scipy() -> bool:
    try:
        import numpy  # noqa: F401
        import scipy.linalg  # noqa: F401

        return True
    except ImportError:
        return False


def _audio_features(**overrides) -> AudioFeatures:
    base = dict(
        sample_rate=16000,
        duration_seconds=12.0,
        rms_energy=0.05,
        zero_crossing_rate=0.05,
        spectral_centroid=1500.0,
        spectral_bandwidth=2000.0,
        spectral_rolloff=4000.0,
        spectral_flatness=0.2,
        pitch_mean=150.0,
        pitch_std=20.0,
        formant_frequencies=[],
        mfcc_means=[0.0] * 13,
        mfcc_stds=[5.0] * 13,
        tempo=60.0,
        onset_rate=1.0,
    )
    base.update(overrides)
    return AudioFeatures(**base)


class AudioFluencyUnitTest(unittest.TestCase):
    """onsets/second must be compared with beats/second (tempo/60), not BPM."""

    def test_onsets_matching_beat_rate_fires(self) -> None:
        features = _audio_features(tempo=60.0, onset_rate=1.0, duration_seconds=12.0)
        signal = _fluency_analysis(features)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.title, "균일한 리듬 패턴")

    def test_half_beat_onset_rate_does_not_fire(self) -> None:
        features = _audio_features(tempo=120.0, onset_rate=1.0, duration_seconds=12.0)
        self.assertIsNone(_fluency_analysis(features))

    def test_short_clip_does_not_fire(self) -> None:
        features = _audio_features(tempo=60.0, onset_rate=1.0, duration_seconds=5.0)
        self.assertIsNone(_fluency_analysis(features))


@unittest.skipUnless(_has_numpy_scipy(), "numpy/scipy not installed")
class FormantEstimationTest(unittest.TestCase):
    """The LPC normal equations must be solved with the autocorrelation RHS."""

    def _signal(self) -> "list[float]":
        import numpy as np

        sr = 16000
        t = np.linspace(0, 1.0, sr, endpoint=False)
        y = 0.4 * np.sin(2 * np.pi * 500.0 * t)
        y += 0.3 * np.sin(2 * np.pi * 1500.0 * t)
        y += 0.2 * np.sin(2 * np.pi * 2500.0 * t)
        return y

    def test_synthetic_tones_yield_formants(self) -> None:
        from deepfake_lens.audio import _estimate_formants

        formants = _estimate_formants(self._signal(), 16000)
        self.assertTrue(formants)
        self.assertEqual(formants, sorted(formants))
        self.assertTrue(all(90 < f < 5000 for f in formants))
        self.assertAlmostEqual(formants[0], 500.0, delta=120.0)

    def test_silence_returns_empty(self) -> None:
        import numpy as np

        from deepfake_lens.audio import _estimate_formants

        self.assertEqual(_estimate_formants(np.zeros(16000, dtype=float), 16000), [])


class MultimodalScoreTest(unittest.TestCase):
    """Consistency must not raise suspicion on its own."""

    def test_clean_single_modality_scores_zero(self) -> None:
        result = analyze_multimodal(image_score=0)
        self.assertEqual(result.score, 0)

    def test_clean_multiple_modalities_stay_clean(self) -> None:
        result = analyze_multimodal(image_score=5, text_score=5, audio_score=5)
        self.assertEqual(result.score, 5)

    def test_cross_modal_disagreement_is_scored(self) -> None:
        # mean 50 + the weight-20 cross-modal disagreement signal
        result = analyze_multimodal(image_score=90, text_score=10)
        self.assertEqual(result.score, 70)
        self.assertTrue(any(s.source_modality == "cross-modal" for s in result.signals))

    def test_consistent_high_scores_still_high(self) -> None:
        result = analyze_multimodal(image_score=80, text_score=75, audio_score=78)
        self.assertEqual(result.band, "high")


def _classification_result(signals: list[EvidenceSignal], confidence: SourceConfidence) -> ClassificationResult:
    return ClassificationResult(
        score=0,
        band=RiskBand.UNKNOWN,
        band_label="판단 어려움",
        verdict="test",
        signals=signals,
        limitations=[],
        source_guess=SourceGuess("테스트 출처", confidence, ["test"]),
        next_checks=[],
    )


class FusionDoubleCountTest(unittest.TestCase):
    """The source-derived signal must not count twice, and fusion must be
    idempotent."""

    def test_source_signal_not_double_counted(self) -> None:
        result = _classification_result(
            [EvidenceSignal("생성 도구 메타데이터", "A1111 파라미터 발견", 67)],
            SourceConfidence.HIGH,
        )
        components = component_scores(result)
        self.assertEqual(components["metadata"], 0)
        self.assertEqual(components["source"], 100)

    def test_other_metadata_signals_still_count(self) -> None:
        result = _classification_result(
            [EvidenceSignal("생성 모델에 흔한 정사각 해상도", "1024x1024", 9)],
            SourceConfidence.UNKNOWN,
        )
        components = component_scores(result)
        self.assertEqual(components["metadata"], 9)
        self.assertEqual(components["source"], 0)

    def test_apply_fusion_is_idempotent(self) -> None:
        result = _classification_result(
            [EvidenceSignal("생성 도구 메타데이터", "ComfyUI 워크플로 흔적", 67)],
            SourceConfidence.HIGH,
        )
        once = apply_fusion_to_result(result, DEFAULT_FUSION_PROFILE)
        twice = apply_fusion_to_result(once, DEFAULT_FUSION_PROFILE)
        self.assertEqual(once.score, twice.score)
        self.assertEqual(once.band, twice.band)
        fusion_signals = [s for s in twice.signals if s.title == "융합 점수"]
        self.assertEqual(len(fusion_signals), 1)

    def test_fused_score_stays_in_range(self) -> None:
        components = {"metadata": 100, "pixel": 100, "external_model": 100, "source": 100}
        self.assertEqual(fused_score(components, DEFAULT_FUSION_PROFILE), 100)


if __name__ == "__main__":
    unittest.main()

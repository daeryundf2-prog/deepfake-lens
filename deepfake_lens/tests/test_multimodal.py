"""Tests for the multimodal analysis module."""

from __future__ import annotations

import unittest

from deepfake_lens.multimodal import (
    AvSyncAnalysis,
    MultimodalAnalysis,
    MultimodalEvidenceSignal,
    analyze_av_sync,
    analyze_multimodal,
    av_sync_from_envelopes,
)


def _has_numpy() -> bool:
    try:
        import numpy  # noqa: F401

        return True
    except ImportError:
        return False


class MultimodalAnalysisTest(unittest.TestCase):
    """Test cases for multimodal analysis functions."""

    def test_analyze_multimodal_returns_result(self) -> None:
        """analyze_multimodal should return a MultimodalAnalysis."""
        result = analyze_multimodal(image_score=80, text_score=70)
        self.assertIsInstance(result, MultimodalAnalysis)

    def test_empty_modalities_returns_zero(self) -> None:
        """No modalities should return zero score."""
        result = analyze_multimodal()
        self.assertEqual(result.score, 0)
        self.assertEqual(result.modalities_used, [])

    def test_single_modality_returns_neutral(self) -> None:
        """Single modality should return neutral consistency."""
        result = analyze_multimodal(image_score=50)
        self.assertEqual(len(result.modalities_used), 1)
        self.assertEqual(result.consistency_score, 0.5)

    def test_consistent_high_scores(self) -> None:
        """Consistent high scores should increase score."""
        result = analyze_multimodal(image_score=80, text_score=75, audio_score=78)
        self.assertGreater(result.score, 75)
        self.assertGreater(result.consistency_score, 0.8)

    def test_consistent_low_scores(self) -> None:
        """Consistent low scores should keep score low."""
        result = analyze_multimodal(image_score=20, text_score=25, audio_score=22)
        self.assertLess(result.score, 35)
        self.assertGreater(result.consistency_score, 0.8)

    def test_inconsistent_scores_detected(self) -> None:
        """Inconsistent scores should be detected."""
        result = analyze_multimodal(image_score=90, text_score=10)
        self.assertGreater(len(result.signals), 0)
        # Should have inconsistency signal
        inconsistency_signals = [s for s in result.signals if "불일치" in s.title]
        self.assertGreater(len(inconsistency_signals), 0)

    def test_source_guess_consistency(self) -> None:
        """Matching source guesses should increase consistency."""
        result = analyze_multimodal(
            image_score=70,
            text_score=65,
            image_source_guess="DALL-E",
            text_source_guess="DALL-E",
        )
        self.assertGreater(result.consistency_score, 0.8)

    def test_source_guess_inconsistency(self) -> None:
        """Different source guesses should be detected."""
        result = analyze_multimodal(
            image_score=70,
            text_score=65,
            image_source_guess="DALL-E",
            text_source_guess="Midjourney",
        )
        inconsistency_signals = [s for s in result.signals if "출처 불일치" in s.title]
        self.assertGreater(len(inconsistency_signals), 0)

    def test_ai_probability_range(self) -> None:
        """AI probability should be between 0 and 1."""
        result = analyze_multimodal(image_score=80, text_score=70)
        self.assertGreaterEqual(result.overall_ai_probability, 0.0)
        self.assertLessEqual(result.overall_ai_probability, 1.0)

    def test_high_score_returns_high_band(self) -> None:
        """High scores should return high band."""
        result = analyze_multimodal(image_score=90, text_score=85, audio_score=88)
        self.assertEqual(result.band, "high")
        self.assertEqual(result.band_label, "높음")

    def test_low_score_returns_low_band(self) -> None:
        """Low scores should return low band."""
        result = analyze_multimodal(image_score=10, text_score=15, audio_score=12)
        self.assertEqual(result.band, "low")
        self.assertEqual(result.band_label, "낮음")

    def test_to_json_returns_dict(self) -> None:
        """to_json should return a dictionary."""
        result = analyze_multimodal(image_score=50, text_score=60)
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        self.assertIn("band", data)
        self.assertIn("verdict", data)
        self.assertIn("modalities_used", data)
        self.assertIn("consistency_score", data)

    def test_multimodal_evidence_signal(self) -> None:
        """MultimodalEvidenceSignal should be a valid dataclass."""
        signal = MultimodalEvidenceSignal(
            title="Test",
            detail="Test detail",
            weight=10,
            source_modality="image",
        )
        self.assertEqual(signal.title, "Test")
        self.assertEqual(signal.source_modality, "image")


class AvSyncTest(unittest.TestCase):
    """Synthetic-envelope tests for the A/V sync check."""

    @staticmethod
    def _burst_envelope(*, seconds=10.0, rate=25.0, seed=0):
        import numpy as np

        rng = np.random.default_rng(seed)
        n = int(seconds * rate)
        env = rng.normal(0, 0.05, size=n)
        # Speech-like bursts every ~1.5 s
        for center in range(int(rate), n - int(rate), int(1.5 * rate)):
            width = int(0.2 * rate)
            lo, hi = center - width, center + width
            env[lo:hi] += np.hanning(hi - lo)
        return env.tolist()

    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_aligned_envelopes_report_small_offset(self) -> None:
        audio = self._burst_envelope()
        motion = self._burst_envelope()
        result = av_sync_from_envelopes(
            audio, motion, audio_rate=25.0, motion_rate=25.0
        )
        self.assertIsNotNone(result.offset_seconds)
        self.assertAlmostEqual(result.offset_seconds, 0.0, delta=0.1)
        self.assertGreater(result.peak_correlation, 0.5)
        self.assertEqual(result.score, 0)
        self.assertFalse(result.signals)

    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_delayed_audio_flags_desync(self) -> None:
        import numpy as np

        motion = self._burst_envelope()
        delay = int(0.5 * 25)  # audio arrives 0.5 s late
        audio = np.concatenate([np.zeros(delay), np.asarray(motion)[:-delay]]).tolist()
        result = av_sync_from_envelopes(
            audio, motion, audio_rate=25.0, motion_rate=25.0
        )
        self.assertAlmostEqual(result.offset_seconds, 0.5, delta=0.1)
        self.assertGreater(result.peak_correlation, 0.15)
        self.assertTrue(any("싱크 오프셋" in s.title for s in result.signals))
        self.assertGreater(result.score, 0)

    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_uncorrelated_envelopes_do_not_score(self) -> None:
        import numpy as np

        rng = np.random.default_rng(1)
        audio = np.abs(rng.normal(0, 1, 250)).tolist()
        motion = np.abs(rng.normal(0, 1, 250)).tolist()
        result = av_sync_from_envelopes(
            audio, motion, audio_rate=25.0, motion_rate=25.0
        )
        self.assertEqual(result.score, 0)
        self.assertFalse(result.signals)
        self.assertTrue(
            any("상관이 낮" in line or "싱크" in line for line in result.limitations)
            or result.peak_correlation < 0.15
        )

    def test_short_or_flat_envelopes_degrade_gracefully(self) -> None:
        result = av_sync_from_envelopes(
            [0.0] * 500, [1.0] * 500, audio_rate=25.0, motion_rate=25.0
        )
        self.assertEqual(result.band, "unknown")
        self.assertIsNone(result.offset_seconds)

    def test_missing_file_returns_error(self) -> None:
        result = analyze_av_sync("/nonexistent/video.mp4")
        self.assertEqual(result.band, "unknown")
        self.assertIn("존재하지 않습니다", result.verdict)

    def test_avsync_analysis_to_json(self) -> None:
        result = analyze_av_sync("/nonexistent/video.mp4")
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("offset_seconds", data)
        self.assertIn("peak_correlation", data)
        self.assertIn("method", data)

    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_avsync_feeds_multimodal_as_cross_modal(self) -> None:
        import numpy as np

        motion = self._burst_envelope()
        delay = int(0.5 * 25)
        audio = np.concatenate([np.zeros(delay), np.asarray(motion)[:-delay]]).tolist()
        sync = av_sync_from_envelopes(
            audio, motion, audio_rate=25.0, motion_rate=25.0
        )
        combined = analyze_multimodal(image_score=20, text_score=20, av_sync=sync)
        self.assertIn("av-sync", combined.modalities_used)
        self.assertGreater(combined.score, 20)
        self.assertTrue(
            any(s.source_modality == "cross-modal" and "싱크" in s.title for s in combined.signals)
        )


if __name__ == "__main__":
    unittest.main()

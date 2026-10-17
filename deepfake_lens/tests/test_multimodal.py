"""Tests for the multimodal analysis module."""

from __future__ import annotations

import unittest
from typing import Any

from deepfake_lens.multimodal import (
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
        """D1: overall_ai_probability (= score/100) is gone; the raw score stays in 0-100."""
        result = analyze_multimodal(image_score=80, text_score=70)
        self.assertFalse(hasattr(result, "overall_ai_probability"))
        self.assertLessEqual(result.score, 100)

    def test_high_score_returns_high_band(self) -> None:
        """D1 (was: high band): high raw inputs give a reference diagnostic, no band."""
        result = analyze_multimodal(image_score=90, text_score=85, audio_score=88)
        self.assertEqual(result.reference_band, "reference")
        self.assertFalse(hasattr(result, "band"))
        self.assertNotIn("강합니다", result.reference_note)

    def test_low_score_returns_low_band(self) -> None:
        """D1 (was: low band): low raw inputs never produce a "낮음" conclusion."""
        result = analyze_multimodal(image_score=10, text_score=15, audio_score=12)
        self.assertEqual(result.reference_band, "reference")
        self.assertNotIn("적습니다", result.reference_note)

    def test_to_json_returns_dict(self) -> None:
        """to_json should return a dictionary."""
        result = analyze_multimodal(image_score=50, text_score=60)
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        self.assertIn("reference_band", data)
        self.assertNotIn("band", data)  # D1: layer modules report reference_band/reference_note, never a band
        self.assertIn("reference_note", data)
        self.assertNotIn("verdict", data)
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
        self.assertEqual(result.reference_band, "unavailable")  # D1: layer modules report reference_band/reference_note, never a band
        self.assertIsNone(result.offset_seconds)

    def test_missing_file_returns_error(self) -> None:
        result = analyze_av_sync("/nonexistent/video.mp4")
        self.assertEqual(result.reference_band, "unavailable")  # D1: layer modules report reference_band/reference_note, never a band
        self.assertIn("존재하지 않습니다", result.reference_note)

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


def _have(*modules: str) -> bool:
    import importlib.util

    return all(importlib.util.find_spec(name) is not None for name in modules)


@unittest.skipUnless(_have("cv2", "librosa", "numpy"), "opencv + librosa required")
class AvSyncAudioTrackTest(unittest.TestCase):
    """R25-9 (round 25): analyze_av_sync opened the video with ``librosa.load``; librosa 1.0 decodes with soundfile only,
    which cannot open a video container, so a video with an audio track and one without both ended with 「오디오 스트림을
    읽을 수 없어 A/V 싱크를 측정할 수 없습니다.」 and the measurement never ran. The audio track is now decoded by ffmpeg
    into a temporary WAV, and the coverage (``av_sync_motion`` / ``av_sync_audio``) and limitations name what happened."""

    def _make(self, folder, name: str, *, audio: bool):
        import shutil
        import subprocess

        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg 없음")
        target = folder / name
        inputs = ["-f", "lavfi", "-i", "testsrc=size=96x96:rate=15:duration=3"]
        if audio:
            inputs = ["-f", "lavfi", "-i", "sine=frequency=440:duration=3", *inputs, "-shortest"]
        proc = subprocess.run(["ffmpeg", "-loglevel", "error", *inputs, "-y", str(target)], capture_output=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        return target

    @staticmethod
    def _coverage(result) -> dict[str, tuple[str, str]]:
        return {entry["check"]: (entry["status"], entry["reason"]) for entry in result.coverage}

    def test_a_video_with_an_audio_track_is_measured(self) -> None:
        import os
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            video = self._make(Path(tmp), "c:with audio 한글.mp4", audio=True)
            result = analyze_av_sync(video)
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                relative = analyze_av_sync(video.name)  # R24-2 / R25-4: a relative name with ":" reaches ffmpeg as a file
            finally:
                os.chdir(previous)
            self.assertEqual(sorted(Path(tmp).iterdir()), [video])  # no temporary WAV left next to it
        self.assertEqual(self._coverage(result), {"av_sync_motion": ("ran", ""), "av_sync_audio": ("ran", "")})
        self.assertGreater(result.audio_seconds, 2.5)
        self.assertNotIn("오디오 스트림을 읽을 수 없어", result.reference_note)
        self.assertEqual(self._coverage(relative), self._coverage(result))
        self.assertEqual(relative.to_json()["coverage"], result.to_json()["coverage"])

    def test_a_video_without_an_audio_track_says_so(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            result = analyze_av_sync(self._make(Path(tmp), "silent.mp4", audio=False))
        from deepfake_lens.multimodal import AV_SYNC_NO_AUDIO_TRACK

        self.assertEqual(self._coverage(result), {"av_sync_motion": ("ran", ""), "av_sync_audio": ("skipped", AV_SYNC_NO_AUDIO_TRACK)})
        self.assertIsNone(result.offset_seconds)
        self.assertIn(AV_SYNC_NO_AUDIO_TRACK, result.reference_note)
        self.assertIn(AV_SYNC_NO_AUDIO_TRACK, " ".join(result.limitations))

    def test_without_ffmpeg_the_audio_check_is_a_missing_dependency(self) -> None:
        import shutil
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            video = self._make(Path(tmp), "a.mp4", audio=True)
            real_which = shutil.which
            with mock.patch("shutil.which", side_effect=lambda name, *a, **k: None if name == "ffmpeg" else real_which(name, *a, **k)):
                result = analyze_av_sync(video)
        self.assertEqual(self._coverage(result)["av_sync_audio"], ("skipped", "의존성 부재: ffmpeg"))
        self.assertIn("의존성 부재: ffmpeg", result.reference_note)


@unittest.skipUnless(_have("cv2", "librosa", "numpy"), "opencv + librosa required")
class AvSyncBoundedMultiTrackTest(unittest.TestCase):
    """R26-9 (round 26): --av-sync decoded the whole audio track at 22 050 Hz (a two-hour video: ~317 MB temporary WAV,
    ~635 MB of samples) and read the first track only (a silent first track ended 「엔벨로프가 너무 짧거나 분산이 없어」
    while the speech was on track 2, and nothing said so). The decode is now 8 kHz mono of the first AV_SYNC_MAX_SECONDS
    only (the span said in the limitations when the video is longer), and every track up to AV_SYNC_MAX_AUDIO_TRACKS is
    measured — the best-correlated one reported, each named in the limitations."""

    def _make(self, folder: Any, name: str, tracks: int, seconds: int = 4) -> Any:
        import shutil
        import subprocess

        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg 없음")
        target = folder / name
        inputs = ["-f", "lavfi", "-i", f"testsrc=size=96x96:rate=15:duration={seconds}"]
        maps = ["-map", "0:v"]
        sources = [f"anullsrc=r=16000:cl=mono:d={seconds}", f"sine=frequency=440:duration={seconds}", f"sine=frequency=880:duration={seconds}"]
        for index in range(tracks):
            inputs += ["-f", "lavfi", "-i", sources[index % len(sources)]]
            maps += ["-map", f"{index + 1}:a"]
        proc = subprocess.run(["ffmpeg", "-loglevel", "error", *inputs, *maps, "-shortest", "-c:v", "mpeg4", "-c:a", "aac", "-y", str(target)],
                              capture_output=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        return target

    def test_every_track_is_measured_and_the_decode_is_bounded(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest import mock

        from deepfake_lens import audio, multimodal

        calls: list[list[str]] = []
        real = audio.run_child

        def spy(args: Any, **kwargs: Any) -> Any:
            calls.append([str(arg) for arg in args])
            return real(args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            two = self._make(Path(tmp), "two tracks.mkv", tracks=2)
            one = self._make(Path(tmp), "one track.mkv", tracks=1)
            with mock.patch.object(audio, "run_child", side_effect=spy):
                result = analyze_av_sync(two)
            single = analyze_av_sync(one)
            with mock.patch.object(multimodal, "AV_SYNC_MAX_SECONDS", 2):
                capped = analyze_av_sync(one)
            with mock.patch.object(multimodal, "AV_SYNC_MAX_AUDIO_TRACKS", 1):
                limited = analyze_av_sync(two)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["one track.mkv", "two tracks.mkv"])  # no WAV left
        decodes = [call for call in calls if "-ar" in call]
        self.assertTrue(decodes)
        for call in decodes:
            self.assertEqual(call[call.index("-ar") + 1], "8000")
            self.assertEqual(call[call.index("-ac") + 1], "1")
            self.assertIn("-t", call)  # never the whole track
        self.assertEqual(sorted({call[call.index("-map") + 1] for call in decodes}), ["0:a:0", "0:a:1", "0:a:2"])  # 2 tracks + the probe for a third
        coverage = {entry["check"]: entry for entry in result.coverage}
        self.assertEqual(coverage["av_sync_audio"]["status"], "ran")
        self.assertIn("음성 트랙 2개를 각각 측정해", coverage["av_sync_audio"]["reason"])
        text = " ".join(result.limitations)
        self.assertIn("음성 트랙 1:", text)
        self.assertIn("음성 트랙 2:", text)
        self.assertEqual({entry["check"]: (entry["status"], entry["reason"]) for entry in single.coverage}["av_sync_audio"], ("ran", ""))
        self.assertFalse(any("음성 트랙 1:" in line for line in single.limitations))
        self.assertIn(multimodal.AV_SYNC_SPAN_NOTE.format(seconds=2, rate=8000), capped.limitations)
        self.assertLessEqual(capped.audio_seconds, 2.1)
        self.assertNotIn(multimodal.AV_SYNC_SPAN_NOTE.format(seconds=multimodal.AV_SYNC_MAX_SECONDS, rate=8000), single.limitations)
        self.assertIn(multimodal.AV_SYNC_MORE_TRACKS.format(n=1), limited.limitations)


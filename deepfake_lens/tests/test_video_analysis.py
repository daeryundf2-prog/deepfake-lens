"""Tests for the video temporal analysis module."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from deepfake_lens.video_analysis import (
    VideoTemporalAnalysis,
    FrameAnalysis,
    analyze_video_temporal,
    _brightness_consistency,
    _contrast_consistency,
    _blur_pattern,
    _edge_density_changes,
    _fps_analysis,
    _resolution_analysis,
)


class VideoTemporalAnalysisTest(unittest.TestCase):
    """Test cases for video temporal analysis functions."""

    def test_nonexistent_file_returns_error(self) -> None:
        """Analysis of nonexistent file should return error analysis."""
        result = analyze_video_temporal(Path("/nonexistent/video.mp4"))
        self.assertEqual(result.score, 0)
        self.assertEqual(result.reference_band, "unavailable")  # D1: layer modules report reference_band/reference_note, never a band
        self.assertIn("존재하지 않습니다", result.reference_note)

    def test_unsupported_format_returns_error(self) -> None:
        """Analysis of unsupported format should return error analysis."""
        tmp_path = Path(tempfile.gettempdir()) / "test.txt"
        tmp_path.write_bytes(b"not video")
        result = analyze_video_temporal(tmp_path)
        self.assertEqual(result.score, 0)
        self.assertIn("지원하지 않는", result.reference_note)
        tmp_path.unlink(missing_ok=True)

    def test_analysis_returns_dataclass(self) -> None:
        """Analysis should return a VideoTemporalAnalysis dataclass."""
        result = analyze_video_temporal(Path("nonexistent.mp4"))
        self.assertIsInstance(result, VideoTemporalAnalysis)

    def test_to_json_returns_dict(self) -> None:
        """to_json should return a dictionary."""
        result = analyze_video_temporal(Path("nonexistent.mp4"))
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        self.assertIn("reference_band", data)
        self.assertNotIn("band", data)  # D1: layer modules report reference_band/reference_note, never a band
        self.assertIn("reference_note", data)
        self.assertNotIn("verdict", data)
        self.assertIn("frame_count", data)
        self.assertIn("duration_seconds", data)

    def test_brightness_consistency_stable(self) -> None:
        """Unnaturally stable brightness should generate signal."""
        frames = [
            FrameAnalysis(i, i * 0.033, 128.0, 30.0, 100.0, 0.1)
            for i in range(25)
        ]
        signal = _brightness_consistency(frames)
        self.assertIsNotNone(signal)
        self.assertIn("밝기", signal.title)

    def test_brightness_consistency_normal(self) -> None:
        """Normal brightness variation should not generate signal."""
        frames = [
            FrameAnalysis(i, i * 0.033, 120.0 + (i % 5) * 5, 30.0, 100.0, 0.1)
            for i in range(25)
        ]
        signal = _brightness_consistency(frames)
        self.assertIsNone(signal)

    def test_contrast_consistency_stable(self) -> None:
        """Unnaturally stable contrast should generate signal."""
        frames = [
            FrameAnalysis(i, i * 0.033, 128.0, 30.0, 100.0, 0.1)
            for i in range(25)
        ]
        signal = _contrast_consistency(frames)
        self.assertIsNotNone(signal)
        self.assertIn("대비", signal.title)

    def test_contrast_consistency_normal(self) -> None:
        """Normal contrast variation should not generate signal."""
        frames = [
            FrameAnalysis(i, i * 0.033, 128.0, 30.0 + (i % 5) * 3, 100.0, 0.1)
            for i in range(25)
        ]
        signal = _contrast_consistency(frames)
        self.assertIsNone(signal)

    def test_blur_pattern_stable(self) -> None:
        """Unnaturally stable blur should generate signal."""
        frames = [
            FrameAnalysis(i, i * 0.033, 128.0, 30.0, 150.0, 0.1)
            for i in range(25)
        ]
        signal = _blur_pattern(frames)
        self.assertIsNotNone(signal)
        self.assertIn("블러", signal.title)

    def test_blur_pattern_normal(self) -> None:
        """Normal blur variation should not generate signal."""
        frames = [
            FrameAnalysis(i, i * 0.033, 128.0, 30.0, 100.0 + (i % 5) * 20, 0.1)
            for i in range(25)
        ]
        signal = _blur_pattern(frames)
        self.assertIsNone(signal)

    def test_edge_density_stable(self) -> None:
        """Unnaturally stable edge density should generate signal."""
        frames = [
            FrameAnalysis(i, i * 0.033, 128.0, 30.0, 100.0, 0.15)
            for i in range(25)
        ]
        signal = _edge_density_changes(frames)
        self.assertIsNotNone(signal)
        self.assertIn("에지", signal.title)

    def test_edge_density_normal(self) -> None:
        """Normal edge density variation should not generate signal."""
        frames = [
            FrameAnalysis(i, i * 0.033, 128.0, 30.0, 100.0, 0.1 + (i % 5) * 0.01)
            for i in range(25)
        ]
        signal = _edge_density_changes(frames)
        self.assertIsNone(signal)

    def test_fps_analysis_unusual(self) -> None:
        """Unusual frame rate should generate signal."""
        signal = _fps_analysis(27.5, 10.0)
        self.assertIsNotNone(signal)
        self.assertIn("프레임 레이트", signal.title)

    def test_fps_analysis_normal(self) -> None:
        """Normal frame rate should not generate signal."""
        signal = _fps_analysis(30.0, 10.0)
        self.assertIsNone(signal)

    def test_resolution_analysis_unusual(self) -> None:
        """Unusual resolution should generate signal."""
        signal = _resolution_analysis(1234, 5678)
        self.assertIsNotNone(signal)
        self.assertIn("해상도", signal.title)

    def test_resolution_analysis_normal(self) -> None:
        """Normal resolution should not generate signal."""
        signal = _resolution_analysis(1920, 1080)
        self.assertIsNone(signal)


if __name__ == "__main__":
    unittest.main()


class AnalyzeFileVideoDispatchTest(unittest.TestCase):
    """analyze_file must route video extensions to the temporal analyzer
    instead of dropping them as 'unsupported'."""

    def test_mp4_dispatches_to_video_kind(self) -> None:
        from deepfake_lens.core import analyze_file

        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "clip.mp4"
            fake.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            item = analyze_file(fake)
        self.assertEqual(item.kind, "video")
        self.assertEqual(item.status, "analyzed")
        # A fake container can't be decoded: the analyzer degrades to an
        # error analysis (band unknown) rather than crashing or 'unsupported'.
        self.assertIsNotNone(item.result)
        self.assertIn(item.result.band.value, {"unknown", "low"})

    def test_video_result_adapter_contract(self) -> None:
        from deepfake_lens.core import _video_result
        from deepfake_lens.video_analysis import VideoEvidenceSignal

        analysis = VideoTemporalAnalysis(
            score=40, reference_band="reference",  # D1: no band/verdict on layer results
            reference_note="영상 시간축 휴리스틱 참고 원점수 40/100",
            signals=[VideoEvidenceSignal("밝기 불일치", "테스트", 20)],
            limitations=["테스트 한계"],
            frame_count=10, duration_seconds=10.0, fps=1.0,
            resolution=(1920, 1080), model_analysis=None,
        )
        result = _video_result(analysis)
        # G5/G17: the temporal heuristic's 40/"medium" used to pass through
        # as the result; it is now a reference signal and cannot decide.
        self.assertEqual(result.score, 0)
        self.assertEqual(result.band.value, "unknown")
        self.assertEqual(result.verdict_code.value, "undetermined")
        self.assertEqual(result.reference_signals[0].title, "밝기 불일치")
        self.assertEqual(result.reference_signals[0].weight, 20)
        self.assertTrue(result.next_checks)


class HfFlickerSignalTest(unittest.TestCase):
    """High-frequency temporal flicker (frame-independent generation cue)."""

    def _frames(self, blur_series, brightness=120.0):
        return [
            FrameAnalysis(i, i * 0.033, brightness, 30.0, b, 0.1)
            for i, b in enumerate(blur_series)
        ]

    def test_oscillating_hf_energy_flags_flicker(self) -> None:
        from deepfake_lens.video_analysis import _hf_flicker_consistency
        # HF energy alternating every frame while brightness stays flat —
        # the frame-independent-generation signature.
        frames = self._frames([100, 140, 100, 140, 100, 140, 100, 140, 100, 140])
        signal = _hf_flicker_consistency(frames)
        self.assertIsNotNone(signal)
        self.assertGreaterEqual(signal.weight, 30)

    def test_monotonic_motion_hf_is_not_flicker(self) -> None:
        from deepfake_lens.video_analysis import _hf_flicker_consistency
        # HF energy drifting smoothly upward (natural focus/motion change).
        frames = self._frames([100, 105, 112, 118, 125, 131, 138, 144, 151, 158])
        self.assertIsNone(_hf_flicker_consistency(frames))

    def test_flicker_with_matched_exposure_jitter_not_flagged(self) -> None:
        from deepfake_lens.video_analysis import _hf_flicker_consistency
        # Same oscillation but brightness jitters along — motion-driven,
        # not generator flicker.
        frames = [
            FrameAnalysis(i, i * 0.033, 110.0 + (i % 2) * 30, 30.0, b, 0.1)
            for i, b in enumerate([100, 140, 100, 140, 100, 140, 100, 140, 100, 140])
        ]
        self.assertIsNone(_hf_flicker_consistency(frames))

    def test_too_few_frames_returns_none(self) -> None:
        from deepfake_lens.video_analysis import _hf_flicker_consistency
        frames = self._frames([100, 140, 100, 140])
        self.assertIsNone(_hf_flicker_consistency(frames))


class AvAudioCoverageTest(unittest.TestCase):
    """R2: a video's audio-track analysis has its own ``av_audio`` coverage
    entry — ran / skipped "의존성 부재: librosa" (or ffmpeg, or no track) /
    failed "<예외 유형>: …" — and an audio failure never fails or hides
    inside ``video_analysis``.

    ffmpeg is faked (``shutil.which`` + ``subprocess.run`` writing a WAV to
    the requested output) so the tests run without it; the temporal
    analysis is a stub so they need neither OpenCV nor a real video.
    """

    def setUp(self) -> None:
        import builtins
        import subprocess
        from unittest import mock

        from deepfake_lens.tests.test_standalone_contract import write_wav
        from deepfake_lens.video_analysis import VideoTemporalAnalysis

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.video = Path(self._tmp.name) / "clip.mp4"
        self.video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        self._real_import = builtins.__import__

        def fake_run(argv, **_kwargs):
            write_wav(Path(argv[-1]))
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        stub = VideoTemporalAnalysis(
            score=0, reference_band="reference", reference_note="", signals=[], limitations=[],
            frame_count=30, duration_seconds=1.0, fps=30.0, resolution=(320, 240),
        )
        for patcher in (
            mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            mock.patch("subprocess.run", side_effect=fake_run),
            mock.patch("deepfake_lens.core.analyze_video_temporal", return_value=stub),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _av_entry(self):
        from deepfake_lens.core import analyze_file
        from deepfake_lens.result_types import CoverageStatus

        item = analyze_file(self.video)
        self.assertIsNotNone(item.result)
        assert item.result is not None
        by_check = {entry.check: entry for entry in item.result.coverage}
        self.assertEqual(by_check["video_analysis"].status, CoverageStatus.RAN)
        return item.result, by_check["av_audio"]

    @unittest.skipUnless(__import__("importlib").util.find_spec("librosa") is not None, "librosa not installed")
    def test_audio_track_ran(self) -> None:
        from deepfake_lens.result_types import CoverageStatus

        result, entry = self._av_entry()
        self.assertEqual(entry.status, CoverageStatus.RAN)
        self.assertIsNotNone(result.av_audio)

    def test_librosa_absent_is_skipped_dependency(self) -> None:
        from unittest import mock

        from deepfake_lens.result_types import CoverageStatus

        def no_librosa(name, *args, **kwargs):
            if name == "librosa" or name.startswith("librosa."):
                raise ModuleNotFoundError("No module named 'librosa'", name="librosa")
            return self._real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=no_librosa):
            result, entry = self._av_entry()
        self.assertEqual(entry.status, CoverageStatus.SKIPPED)
        self.assertEqual(entry.reason, "의존성 부재: librosa")
        self.assertIsNone(result.av_audio)
        self.assertIn("영상 음성 트랙 분석 미실행: 의존성 부재: librosa", result.limitations)

    def test_injected_exception_is_failed_with_class_name(self) -> None:
        from unittest import mock

        from deepfake_lens.result_types import CoverageStatus, Verdict

        with mock.patch("deepfake_lens.audio.analyze_audio", side_effect=RuntimeError("audio boom")):
            result, entry = self._av_entry()
        self.assertEqual(entry.status, CoverageStatus.FAILED)
        self.assertEqual(entry.reason, "RuntimeError: audio boom")
        self.assertNotIn("의존성 부재", entry.reason)
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)

    def test_missing_ffmpeg_and_missing_track_are_skipped(self) -> None:
        import subprocess
        from unittest import mock

        from deepfake_lens.result_types import CoverageStatus
        from deepfake_lens.video_analysis import AV_AUDIO_NO_TRACK_REASON

        with mock.patch("shutil.which", return_value=None):
            _, entry = self._av_entry()
        self.assertEqual((entry.status, entry.reason), (CoverageStatus.SKIPPED, "의존성 부재: ffmpeg"))
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1, b"", b"no audio")):
            _, entry = self._av_entry()
        self.assertEqual((entry.status, entry.reason), (CoverageStatus.SKIPPED, AV_AUDIO_NO_TRACK_REASON))

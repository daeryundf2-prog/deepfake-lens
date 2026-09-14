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
        self.assertEqual(result.band, "unknown")
        self.assertIn("존재하지 않습니다", result.verdict)

    def test_unsupported_format_returns_error(self) -> None:
        """Analysis of unsupported format should return error analysis."""
        tmp_path = Path(tempfile.gettempdir()) / "test.txt"
        tmp_path.write_bytes(b"not video")
        result = analyze_video_temporal(tmp_path)
        self.assertEqual(result.score, 0)
        self.assertIn("지원하지 않는", result.verdict)
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
        self.assertIn("band", data)
        self.assertIn("verdict", data)
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
            score=40, band="medium", band_label="주의",
            verdict="몇 가지 이상 신호가 보입니다.",
            signals=[VideoEvidenceSignal("밝기 불일치", "테스트", 20)],
            limitations=["테스트 한계"],
            frame_count=10, duration_seconds=10.0, fps=1.0,
            resolution=(1920, 1080), model_analysis=None,
        )
        result = _video_result(analysis)
        self.assertEqual(result.score, 40)
        self.assertEqual(result.signals[0].title, "밝기 불일치")
        self.assertTrue(result.next_checks)

"""Tests for the audio deepfake detection module."""

from __future__ import annotations

import unittest
import unittest
from pathlib import Path

from deepfake_lens.audio import (
    AudioAnalysis,
    AudioFeatures,
    analyze_audio,
    _pitch_analysis,
    _spectral_analysis,
    _fluency_analysis,
    _mfcc_analysis,
    _energy_analysis,
    _noise_analysis,
)


class AudioAnalysisTest(unittest.TestCase):
    """Test cases for audio analysis functions."""

    def test_nonexistent_file_returns_error(self) -> None:
        """Analysis of nonexistent file should return error analysis."""
        result = analyze_audio(Path("/nonexistent/audio.wav"))
        self.assertEqual(result.score, 0)
        self.assertEqual(result.band, "unknown")
        self.assertIn("존재하지 않습니다", result.verdict)

    def test_unsupported_format_returns_error(self) -> None:
        """Analysis of unsupported format should return error analysis."""
        tmp_path = Path("/tmp") / "test.txt"
        tmp_path.write_bytes(b"not audio")
        result = analyze_audio(tmp_path)
        self.assertEqual(result.score, 0)
        self.assertIn("지원하지 않는", result.verdict)
        tmp_path.unlink(missing_ok=True)
    def test_empty_file_returns_error(self) -> None:
        """Analysis of empty file should return error analysis."""
        tmp_path = Path("/tmp") / "empty.wav"
        tmp_path.write_bytes(b"")
        result = analyze_audio(tmp_path)
        self.assertEqual(result.score, 0)
        self.assertIn("비어 있습니다", result.verdict)
        tmp_path.unlink(missing_ok=True)

    def test_analysis_returns_dataclass(self) -> None:
        """Analysis should return an AudioAnalysis dataclass."""
        result = analyze_audio(Path("nonexistent.wav"))
        self.assertIsInstance(result, AudioAnalysis)

    def test_to_json_returns_dict(self) -> None:
        """to_json should return a dictionary."""
        result = analyze_audio(Path("nonexistent.wav"))
        data = result.to_json()
        self.assertIsInstance(data, dict)
        self.assertIn("score", data)
        self.assertIn("band", data)
        self.assertIn("verdict", data)

    def test_pitch_analysis_stable_pitch(self) -> None:
        """Unnaturally stable pitch should generate signal."""
        features = AudioFeatures(
            sample_rate=16000,
            duration_seconds=10.0,
            rms_energy=0.01,
            zero_crossing_rate=0.05,
            spectral_centroid=2000,
            spectral_bandwidth=1500,
            spectral_rolloff=4000,
            spectral_flatness=0.1,
            pitch_mean=200,
            pitch_std=1.5,  # Very stable
            formant_frequencies=[500, 1500, 2500],
            mfcc_means=[0.0] * 13,
            mfcc_stds=[5.0] * 13,
            tempo=120,
            onset_rate=8,
        )
        signal = _pitch_analysis(features)
        self.assertIsNotNone(signal)
        self.assertIn("피치", signal.title)

    def test_pitch_analysis_high_variability(self) -> None:
        """Unnaturally high pitch variability should generate signal."""
        features = AudioFeatures(
            sample_rate=16000,
            duration_seconds=10.0,
            rms_energy=0.01,
            zero_crossing_rate=0.05,
            spectral_centroid=2000,
            spectral_bandwidth=1500,
            spectral_rolloff=4000,
            spectral_flatness=0.1,
            pitch_mean=200,
            pitch_std=100.0,  # Very high variability
            formant_frequencies=[500, 1500, 2500],
            mfcc_means=[0.0] * 13,
            mfcc_stds=[5.0] * 13,
            tempo=120,
            onset_rate=8,
        )
        signal = _pitch_analysis(features)
        self.assertIsNotNone(signal)
        self.assertIn("피치 변동", signal.title)

    def test_spectral_analysis_smooth(self) -> None:
        """Unnaturally smooth spectrum should generate signal."""
        features = AudioFeatures(
            sample_rate=16000,
            duration_seconds=10.0,
            rms_energy=0.01,
            zero_crossing_rate=0.05,
            spectral_centroid=3000,
            spectral_bandwidth=800,  # Very narrow
            spectral_rolloff=4000,
            spectral_flatness=0.1,
            pitch_mean=200,
            pitch_std=10,
            formant_frequencies=[500, 1500, 2500],
            mfcc_means=[0.0] * 13,
            mfcc_stds=[5.0] * 13,
            tempo=120,
            onset_rate=8,
        )
        signal = _spectral_analysis(features)
        self.assertIsNotNone(signal)
        self.assertIn("스펙트럼", signal.title)

    def test_spectral_analysis_flat(self) -> None:
        """Unnaturally flat spectrum should generate signal."""
        features = AudioFeatures(
            sample_rate=16000,
            duration_seconds=10.0,
            rms_energy=0.01,
            zero_crossing_rate=0.05,
            spectral_centroid=2000,
            spectral_bandwidth=1500,
            spectral_rolloff=4000,
            spectral_flatness=0.9,  # Very flat
            pitch_mean=200,
            pitch_std=10,
            formant_frequencies=[500, 1500, 2500],
            mfcc_means=[0.0] * 13,
            mfcc_stds=[5.0] * 13,
            tempo=120,
            onset_rate=8,
        )
        signal = _spectral_analysis(features)
        self.assertIsNotNone(signal)
        self.assertIn("플랫", signal.title)

    def test_fluency_analysis_fast(self) -> None:
        """Unnaturally fast speech should generate signal."""
        features = AudioFeatures(
            sample_rate=16000,
            duration_seconds=10.0,
            rms_energy=0.01,
            zero_crossing_rate=0.05,
            spectral_centroid=2000,
            spectral_bandwidth=1500,
            spectral_rolloff=4000,
            spectral_flatness=0.1,
            pitch_mean=200,
            pitch_std=10,
            formant_frequencies=[500, 1500, 2500],
            mfcc_means=[0.0] * 13,
            mfcc_stds=[5.0] * 13,
            tempo=160,  # Very fast
            onset_rate=18,  # Very fast
        )
        signal = _fluency_analysis(features)
        self.assertIsNotNone(signal)
        self.assertIn("발화 속도", signal.title)

    def test_mfcc_analysis_stable(self) -> None:
        """Unnaturally stable MFCCs should generate signal."""
        features = AudioFeatures(
            sample_rate=16000,
            duration_seconds=10.0,
            rms_energy=0.01,
            zero_crossing_rate=0.05,
            spectral_centroid=2000,
            spectral_bandwidth=1500,
            spectral_rolloff=4000,
            spectral_flatness=0.1,
            pitch_mean=200,
            pitch_std=10,
            formant_frequencies=[500, 1500, 2500],
            mfcc_means=[0.0] * 13,
            mfcc_stds=[0.5] * 13,  # Very stable
            tempo=120,
            onset_rate=8,
        )
        signal = _mfcc_analysis(features)
        self.assertIsNotNone(signal)
        self.assertIn("음향 특성", signal.title)

    def test_energy_analysis_low(self) -> None:
        """Very low energy should generate signal."""
        features = AudioFeatures(
            sample_rate=16000,
            duration_seconds=10.0,
            rms_energy=0.0005,  # Very low
            zero_crossing_rate=0.05,
            spectral_centroid=2000,
            spectral_bandwidth=1500,
            spectral_rolloff=4000,
            spectral_flatness=0.1,
            pitch_mean=200,
            pitch_std=10,
            formant_frequencies=[500, 1500, 2500],
            mfcc_means=[0.0] * 13,
            mfcc_stds=[5.0] * 13,
            tempo=120,
            onset_rate=8,
        )
        signal = _energy_analysis(features)
        self.assertIsNotNone(signal)
        self.assertIn("에너지", signal.title)

    def test_noise_analysis_high(self) -> None:
        """High spectral flatness should generate signal."""
        features = AudioFeatures(
            sample_rate=16000,
            duration_seconds=10.0,
            rms_energy=0.01,
            zero_crossing_rate=0.05,
            spectral_centroid=2000,
            spectral_bandwidth=1500,
            spectral_rolloff=4000,
            spectral_flatness=0.7,  # High noise
            pitch_mean=200,
            pitch_std=10,
            formant_frequencies=[500, 1500, 2500],
            mfcc_means=[0.0] * 13,
            mfcc_stds=[5.0] * 13,
            tempo=120,
            onset_rate=8,
        )
        signal = _noise_analysis(features)
        self.assertIsNotNone(signal)
        self.assertIn("노이즈", signal.title)


if __name__ == "__main__":
    unittest.main()


def _has_librosa() -> bool:
    try:
        import librosa  # noqa: F401

        return True
    except ImportError:
        return False


class AudioSuccessPathTest(unittest.TestCase):
    """The extraction success path was only ever tested with missing files;
    a synthetic WAV finally exercises librosa feature extraction."""

    def _write_tone(self, path: Path, *, seconds: int = 3, hz: float = 440.0) -> None:
        import math
        import struct
        import wave

        sample_rate = 16000
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            frames = bytearray()
            for n in range(sample_rate * seconds):
                value = 0.4 * math.sin(2 * math.pi * hz * n / sample_rate)
                frames += struct.pack("<h", int(value * 32767))
            handle.writeframes(bytes(frames))

    @unittest.skipUnless(_has_librosa(), "librosa not installed")
    def test_tone_wav_produces_features(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "tone.wav"
            self._write_tone(wav_path)
            analysis = analyze_audio(wav_path)
            self.assertIsNotNone(analysis.features)
            self.assertAlmostEqual(analysis.features.duration_seconds, 3.0, delta=0.15)
            self.assertEqual(analysis.band in {"low", "medium", "high"}, True)
            self.assertGreater(analysis.features.sample_rate, 0)

    @unittest.skipUnless(_has_librosa(), "librosa not installed")
    def test_pure_tone_pitch_is_detected(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "tone.wav"
            self._write_tone(wav_path, hz=440.0)
            analysis = analyze_audio(wav_path)
            self.assertIsNotNone(analysis.features)
            self.assertAlmostEqual(analysis.features.pitch_mean, 440.0, delta=60.0)

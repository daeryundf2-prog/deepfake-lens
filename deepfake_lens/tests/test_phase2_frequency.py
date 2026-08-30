"""Verification tests for the Phase 2 frequency forensics and rPPG modules.

These tests verify real measurements, not tautologies: synthetic images are
constructed by *applying* the artifact (1/f^2 noise, spectral spikes,
bilinear upsampling) and the features must respond in the right direction.
"""

from __future__ import annotations

import unittest


def _has_numpy() -> bool:
    try:
        import numpy  # noqa: F401

        return True
    except ImportError:
        return False


def _natural_like_image(size: int = 256, seed: int = 7):
    """Fractal noise with approximately 1/f^2 power spectrum."""
    import numpy as np

    rng = np.random.default_rng(seed)
    white = rng.normal(size=(size, size))
    fy = np.fft.fftfreq(size)[:, None]
    fx = np.fft.fftfreq(size)[None, :]
    radius = np.hypot(fy, fx)
    radius[0, 0] = 1.0
    # Amplitude falls as 1/f, so power falls as 1/f^2.
    amplitude = 1.0 / np.maximum(radius, 1.0 / size)
    spectrum = np.fft.fft2(white) * amplitude
    image = np.fft.ifft2(spectrum).real
    image = (image - image.min()) / max(1e-9, float(np.ptp(image))) * 255.0
    return image


class SpectrumSlopeTest(unittest.TestCase):
    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_natural_image_slope_near_two(self) -> None:
        from deepfake_lens.frequency import frequency_features

        features = frequency_features(_natural_like_image())
        self.assertLess(features.spectrum_slope, -1.4)
        self.assertGreater(features.spectrum_slope, -2.8)

    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_checkerboard_injection_creates_spectral_spikes(self) -> None:
        import numpy as np

        from deepfake_lens.frequency import frequency_features

        image = _natural_like_image()
        size = image.shape[0]
        yy, xx = np.mgrid[0:size, 0:size]
        # Strong isolated periodic component at ~1/4 of Nyquist.
        injection = 60.0 * np.sin(2 * np.pi * yy * (size / 8) / size) * np.sin(2 * np.pi * xx * (size / 8) / size)
        spiked = frequency_features(image + injection)
        clean = frequency_features(image)
        self.assertGreater(spiked.spike_count, clean.spike_count)
        self.assertGreater(spiked.max_spike_prominence, clean.max_spike_prominence)

    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_bilinear_upsample_raises_npr_consistency(self) -> None:
        import numpy as np

        from deepfake_lens.frequency import _npr_consistency

        original = _natural_like_image(128)
        # 2x bilinear upsample with pure numpy: every even row/col equals a
        # source pixel, odd ones are exact midpoints.
        upsampled = np.kron(original, np.ones((2, 2)))
        upsampled[1::2, :] = (upsampled[0::2, :] + np.roll(upsampled[0::2, :], -1, axis=0)) / 2
        upsampled[:, 1::2] = (upsampled[:, 0::2] + np.roll(upsampled[:, 0::2], -1, axis=1)) / 2
        self.assertGreater(_npr_consistency(upsampled), _npr_consistency(original))

    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_dct_highfreq_zero_for_constant_image(self) -> None:
        import numpy as np

        from deepfake_lens.frequency import _dct_block_highfreq

        ratio, uniformity = _dct_block_highfreq(np.full((64, 64), 128.0))
        self.assertAlmostEqual(ratio, 0.0)
        self.assertAlmostEqual(uniformity, 0.0)


class FrequencyExpertIntegrationTest(unittest.TestCase):
    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_expert_present_in_ensemble(self) -> None:
        import tempfile
        from pathlib import Path

        import numpy as np

        from deepfake_lens.pixel import analyze_image_pixels

        image = _natural_like_image(64).astype(np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.png"
            _write_png(path, image)
            analysis = analyze_image_pixels(path, mode="deep")
        names = [expert.name for expert in analysis.experts]
        self.assertIn("frequency_forensics", names)
        expert = next(e for e in analysis.experts if e.name == "frequency_forensics")
        self.assertTrue(expert.available)
        self.assertIn("NPR", expert.reference)

    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_expert_unavailable_without_numpy(self) -> None:
        import builtins
        import importlib
        import tempfile
        from pathlib import Path

        import numpy as np

        image = _natural_like_image(64).astype(np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.png"
            _write_png(path, image)
            real_import = builtins.__import__

            def blocking_import(name, *args, **kwargs):
                if name == "numpy":
                    raise ImportError("blocked for test")
                return real_import(name, *args, **kwargs)

            builtins.__import__ = blocking_import
            try:
                from deepfake_lens import pixel as pixel_module

                importlib.reload(pixel_module)
                analysis = pixel_module.analyze_image_pixels(path, mode="deep")
                expert = next(e for e in analysis.experts if e.name == "frequency_forensics")
                self.assertFalse(expert.available)
            finally:
                builtins.__import__ = real_import
                importlib.reload(pixel_module)


def _write_png(path: Path, image) -> None:
    import struct
    import zlib

    height, width = image.shape
    raw = b"".join(
        b"\x00" + bytes(int(v) for v in image[y]) for y in range(height)
    )

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    )


class ChromPulseTest(unittest.TestCase):
    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_synthetic_pulse_detected_near_72_bpm(self) -> None:
        import numpy as np

        from deepfake_lens.rppg import rppg_from_rgb_samples

        fps = 30.0
        seconds = 30
        t = np.arange(seconds * fps) / fps
        pulse = np.sin(2 * np.pi * (72.0 / 60.0) * t)
        rng = np.random.default_rng(3)
        motion = rng.normal(0, 6.0, size=len(t))  # shared specular motion
        independent = rng.normal(0, 0.5, size=(len(t), 3))
        # Blood-volume pulse: R rises while G dips slightly (absorption
        # change), B barely follows; shared motion hits all channels.
        red = 120 + 8 * pulse + motion + independent[:, 0]
        green = 110 - 2 * pulse + motion + independent[:, 1]
        blue = 100 + 1 * pulse + motion + independent[:, 2]
        samples = list(zip(red.tolist(), green.tolist(), blue.tolist()))
        analysis = rppg_from_rgb_samples(samples, fps=fps)
        self.assertEqual(analysis.band, "low")
        self.assertIsNotNone(analysis.estimated_bpm)
        self.assertAlmostEqual(analysis.estimated_bpm, 72.0, delta=4.0)

    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_pulseless_noise_reports_missing_pulse(self) -> None:
        import numpy as np

        from deepfake_lens.rppg import rppg_from_rgb_samples

        rng = np.random.default_rng(11)
        motion = rng.normal(0, 6.0, size=30 * 30)
        independent = rng.normal(0, 0.5, size=(30 * 30, 3))
        red = (120 + motion + independent[:, 0]).tolist()
        green = (110 + motion + independent[:, 1]).tolist()
        blue = (100 + motion + independent[:, 2]).tolist()
        analysis = rppg_from_rgb_samples(list(zip(red, green, blue)), fps=30.0)
        self.assertEqual(analysis.score, 25)
        self.assertTrue(any("생체 펄스" in signal.title for signal in analysis.signals))

    def test_flat_signal_cannot_be_estimated(self) -> None:
        from deepfake_lens.rppg import rppg_from_rgb_samples

        samples = [(120.0, 110.0, 100.0)] * (30 * 30)
        analysis = rppg_from_rgb_samples(samples, fps=30.0)
        self.assertEqual(analysis.band, "unknown")
        self.assertIn("분산이 부족", analysis.verdict)

    def test_short_series_is_rejected(self) -> None:
        from deepfake_lens.rppg import rppg_from_rgb_samples

        analysis = rppg_from_rgb_samples([(120.0, 110.0, 100.0)] * 10, fps=30.0)
        self.assertEqual(analysis.band, "unknown")
        self.assertIn("짧습니다", analysis.verdict)


class RppgVideoErrorPathTest(unittest.TestCase):
    def test_missing_file_returns_error_analysis(self) -> None:
        from deepfake_lens.rppg import analyze_rppg

        analysis = analyze_rppg("/nonexistent/video.mp4")
        self.assertEqual(analysis.band, "unknown")
        self.assertIn("존재하지 않습니다", analysis.verdict)


if __name__ == "__main__":
    unittest.main()

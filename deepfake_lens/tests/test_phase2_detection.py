"""Verification tests for Phase 2 detection modules: SBI, PRNU, audio
regularity metrics. Synthetic cameras and distortions are constructed so the
measurements must respond in the right direction.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "experiments"))

# sbi (and numpy) are optional: the stdlib-only CI environment must still
# collect this module, so the import is guarded and the dependent tests
# skip when it is unavailable.
try:
    import sbi  # noqa: E402
except ImportError:
    sbi = None

from deepfake_lens.audio import AudioFeatures, _regularity_analysis, _relative_successive_variation  # noqa: E402
from deepfake_lens.prnu import (  # noqa: E402
    analyze_prnu,
    camera_fingerprint,
    normalized_cross_correlation,
    prnu_residual,
)


def _has_numpy() -> bool:
    try:
        import numpy  # noqa: F401

        return True
    except ImportError:
        return False


def _write_gray_png(path: Path, image) -> None:
    import struct
    import zlib

    height, width = image.shape
    raw = b"".join(b"\x00" + bytes(int(max(0, min(255, v))) for v in image[y]) for y in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class SbiDistortionTest(unittest.TestCase):
    @unittest.skipUnless(sbi is not None, "sbi/numpy not available")
    def test_resize_bilinear_interpolates_linear_ramp(self) -> None:
        import numpy as np

        ramp = np.array([[0.0, 1.0]])
        out = sbi.resize_bilinear(ramp, 1, 3)
        self.assertTrue(np.allclose(out, [[0.0, 0.5, 1.0]]))

    @unittest.skipUnless(sbi is not None, "sbi/numpy not available")
    def test_jpeg_simulation_quality_ordering(self) -> None:
        import numpy as np

        rng = np.random.default_rng(0)
        image = rng.uniform(0, 255, size=(64, 64, 3))
        high = sbi.jpeg_simulate(image, 95)
        low = sbi.jpeg_simulate(image, 20)
        rmse_high = float(np.sqrt(np.mean((high - image) ** 2)))
        rmse_low = float(np.sqrt(np.mean((low - image) ** 2)))
        self.assertLess(rmse_high, 3.0)
        self.assertGreater(rmse_low, rmse_high)
        again = sbi.jpeg_simulate(image, 95)
        self.assertTrue(np.array_equal(high, again))

    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_self_blended_image_is_deterministic_and_masked(self) -> None:
        import numpy as np

        rng = np.random.default_rng(5)
        image = rng.uniform(0, 255, size=(128, 128, 3))
        blended, mask = sbi.self_blended_image(image, np.random.default_rng(42))
        blended_again, mask_again = sbi.self_blended_image(image, np.random.default_rng(42))
        self.assertTrue(np.array_equal(blended, blended_again))
        self.assertTrue(np.array_equal(mask, mask_again))
        self.assertGreaterEqual(float(mask.min()), 0.0)
        self.assertLessEqual(float(mask.max()), 1.0)
        self.assertGreater(float(mask.max()), 0.9)
        self.assertLess(float(mask.min()), 0.1)
        # A blending pass must change the image somewhere.
        self.assertGreater(float(np.abs(blended - image).mean()), 0.5)


class SbiTrainerIntegrationTest(unittest.TestCase):
    def test_sbi_mode_accepts_only_real_records(self) -> None:
        import tempfile

        from train_detector import _image_records

        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, label in (("a.png", "real"), ("b.png", "ai"), ("c.png", "edited")):
                path = Path(tmp) / name
                path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
                paths.append({"path": str(path), "label": label, "split": "train"})
            manifest = {"records": paths}
            self.assertEqual(len(_image_records(manifest, sbi=True)), 1)
            self.assertEqual(len(_image_records(manifest, sbi=False)), 3)


class PrnuTest(unittest.TestCase):
    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def _camera_images(self, tmp: Path, camera_seed: int, count: int, size: int = 96) -> list[Path]:
        """Simulate one device: a fixed PRNU pattern modulating smooth scenes."""
        import numpy as np

        rng = np.random.default_rng(camera_seed)
        pattern = rng.normal(0, 1.0, size=(size, size))
        paths = []
        for index in range(count):
            scene = np.cumsum(rng.normal(0, 1.0, size=(size, size)), axis=1)
            scene = np.clip(120 + scene * 0.1, 0, 255)
            image = np.clip(scene * (1.0 + 0.03 * pattern) + rng.normal(0, 0.3, size=(size, size)), 0, 255)
            path = tmp / f"cam{camera_seed}_{index}.png"
            _write_gray_png(path, image)
            paths.append(path)
        return paths

    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_same_camera_correlates_other_camera_does_not(self) -> None:
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            camera_a = self._camera_images(tmp_path, camera_seed=100, count=5)
            references, query_same = camera_a[:4], camera_a[4]
            query_other = self._camera_images(tmp_path, camera_seed=200, count=1)[0]

            residuals = [prnu_residual(_load_gray(p)) for p in references]
            fingerprint = camera_fingerprint(residuals)
            same = normalized_cross_correlation(prnu_residual(_load_gray(query_same)), fingerprint)
            other = normalized_cross_correlation(prnu_residual(_load_gray(query_other)), fingerprint)
            self.assertGreater(same, 0.5)
            self.assertLess(abs(other), 0.15)

            analysis = analyze_prnu(query_same, references)
            self.assertEqual(analysis.band, "low")
            self.assertGreater(analysis.correlation, 0.5)
            analysis_other = analyze_prnu(query_other, references)
            self.assertEqual(analysis_other.score, 25)

    @unittest.skipUnless(_has_numpy(), "numpy not installed")
    def test_fewer_than_three_references_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = self._camera_images(Path(tmp), camera_seed=1, count=2)
            analysis = analyze_prnu(paths[0], paths)
            self.assertEqual(analysis.band, "unknown")
            self.assertIn("부족합니다", analysis.verdict)

    def test_missing_target_returns_error(self) -> None:
        analysis = analyze_prnu("/nonexistent/t.png", ["/a.png", "/b.png", "/c.png"])
        self.assertEqual(analysis.band, "unknown")
        self.assertIn("존재하지 않습니다", analysis.verdict)


def _load_gray(path: Path):
    import numpy as np

    data = path.read_bytes()
    from deepfake_lens.pixel import _load_png_raster

    raster, limitation = _load_png_raster(data, max_side=4096)
    if raster is None:
        raise ValueError(limitation or "decode failed")
    rgb = np.asarray(raster.pixels, dtype=np.float64).reshape(raster.height, raster.width, 3)
    return 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]


class AudioRegularityTest(unittest.TestCase):
    def test_relative_successive_variation(self) -> None:
        self.assertEqual(_relative_successive_variation([1.0, 1.0, 1.0]), 0.0)
        self.assertEqual(_relative_successive_variation([1.0]), 0.0)
        self.assertAlmostEqual(_relative_successive_variation([5.0, 6.0, 7.0]), 1.0 / 6.0)

    def _features(self, **overrides) -> AudioFeatures:
        base = dict(
            sample_rate=16000,
            duration_seconds=10.0,
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
            jitter=0.001,
            shimmer=0.05,
        )
        base.update(overrides)
        return AudioFeatures(**base)

    def test_very_regular_pitch_fires(self) -> None:
        signal = _regularity_analysis(self._features(jitter=0.001))
        self.assertIsNotNone(signal)
        self.assertIn("피치", signal.title)

    def test_natural_jitter_does_not_fire(self) -> None:
        self.assertIsNone(_regularity_analysis(self._features(jitter=0.02, shimmer=0.05)))
        self.assertIsNone(_regularity_analysis(self._features(jitter=0.001, duration_seconds=2.0)))

    def test_uniform_shimmer_fires(self) -> None:
        signal = _regularity_analysis(self._features(jitter=0.02, shimmer=0.005))
        self.assertIsNotNone(signal)
        self.assertIn("진폭", signal.title)


if __name__ == "__main__":
    unittest.main()

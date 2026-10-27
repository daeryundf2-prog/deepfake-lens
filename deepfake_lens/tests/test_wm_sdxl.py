"""techniques-2026 item 3: imwatermark dwtDct decoder and the SDXL fixed-message check.

The marked images are made with ``wm_imwatermark.encode_array_for_tests`` (the
library's encoder with the Haar DWT written out) on a seeded noise image —
dwtDct only round-trips reliably on textured content (measured: 8 of 30
real photos round-trip exactly through imwatermark's own encoder, see
docs/WATERMARK-MEASUREMENTS.md). When PyWavelets + the ``invisible-watermark``
library are installed (the ``tech`` gate image), the decoder is also compared
bit for bit with the library's.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

HAVE_IMAGING = all(importlib.util.find_spec(name) is not None for name in ("numpy", "PIL"))
HAVE_CV2 = HAVE_IMAGING and importlib.util.find_spec("cv2") is not None
HAVE_LIBRARY = HAVE_CV2 and all(importlib.util.find_spec(name) is not None for name in ("imwatermark", "pywt"))
SDXL_BITS = "101100111110110010010000011110111011000110011110"


def _noise(size: int = 512, seed: int = 1, amplitude: float = 64.0):
    import numpy as np

    rng = np.random.RandomState(seed)
    return np.clip(128 + rng.normal(0, amplitude, (size, size, 3)), 0, 255).astype(np.uint8)


def _save_png(array, path: Path) -> Path:
    from PIL import Image

    Image.fromarray(array).save(path)
    return path


@unittest.skipUnless(HAVE_IMAGING, "numpy and Pillow required")
class SdxlWatermarkTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_registry_holds_the_sdxl_message(self) -> None:
        from deepfake_lens.media_marks import load_registry

        # TB-8: the algorithm now has more registered messages (FLUX.1, SD 1/2); the SDXL entry is unchanged.
        [entry] = [e for e in load_registry().for_algorithm("imwatermark-dwtDct") if e["id"] == "sdxl-imwatermark-dwtdct-48"]
        self.assertEqual(entry["message_bits"], SDXL_BITS)
        self.assertEqual(int(SDXL_BITS, 2), 0b101100111110110010010000011110111011000110011110)

    def test_marked_image_is_strong_synthetic_evidence(self) -> None:
        from deepfake_lens.core import analyze_file
        from deepfake_lens.media_marks import SDXL_CHECK, SDXL_FOUND_TITLE
        from deepfake_lens.result_types import CoverageStatus, EvidenceDirection, EvidenceStrength, Verdict
        from deepfake_lens.wm_imwatermark import encode_array_for_tests

        path = _save_png(encode_array_for_tests(_noise(), SDXL_BITS), self.root / "sdxl.png")
        item = analyze_file(path)
        assert item.result is not None
        [found] = [e for e in item.result.evidence if e.title == SDXL_FOUND_TITLE]
        self.assertEqual((found.direction, found.strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG))
        self.assertIn("48/48", found.detail)
        [entry] = [e for e in item.result.coverage if e.check == SDXL_CHECK]
        self.assertEqual(entry.status, CoverageStatus.RAN)
        self.assertEqual(item.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)

    def test_rgb_order_pipeline_is_matched_too(self) -> None:
        from deepfake_lens.media_marks import sdxl_check
        from deepfake_lens.wm_imwatermark import encode_array_for_tests

        swapped = encode_array_for_tests(_noise()[:, :, ::-1].copy(), SDXL_BITS)[:, :, ::-1].copy()
        outcome = sdxl_check(_save_png(swapped, self.root / "rgb.png"))
        self.assertEqual(outcome.matched, "RGB")

    def test_one_bit_off_is_no_information(self) -> None:
        from deepfake_lens.core import analyze_file
        from deepfake_lens.media_marks import SDXL_CHECK, SDXL_FOUND_TITLE
        from deepfake_lens.wm_imwatermark import encode_array_for_tests

        flipped = SDXL_BITS[:-1] + ("0" if SDXL_BITS[-1] == "1" else "1")
        path = _save_png(encode_array_for_tests(_noise(), flipped), self.root / "other.png")
        item = analyze_file(path)
        assert item.result is not None
        self.assertFalse(any(e.title == SDXL_FOUND_TITLE for e in item.result.evidence))
        [entry] = [e for e in item.result.coverage if e.check == SDXL_CHECK]
        self.assertIn("무정보", entry.reason)
        self.assertTrue(any("원본성 근거가 아닙니다" in line for line in item.result.limitations))

    def test_small_image_is_skipped_with_reason(self) -> None:
        from deepfake_lens.core import analyze_file
        from deepfake_lens.media_marks import SDXL_CHECK
        from deepfake_lens.result_types import CoverageStatus

        path = _save_png(_noise(size=200), self.root / "small.png")
        item = analyze_file(path)
        assert item.result is not None
        [entry] = [e for e in item.result.coverage if e.check == SDXL_CHECK]
        self.assertEqual(entry.status, CoverageStatus.SKIPPED)
        self.assertIn("256px", entry.reason)

    def test_missing_numpy_is_dependency_skip(self) -> None:
        from deepfake_lens.media_marks import MarkLayers, _sdxl_layer
        from deepfake_lens.result_types import CoverageStatus

        layers = MarkLayers()
        with mock.patch.dict(sys.modules, {"numpy": None}):
            _sdxl_layer(self.root / "x.png", layers)
        self.assertEqual((layers.coverage[0].status, layers.coverage[0].reason), (CoverageStatus.SKIPPED, "의존성 부재: numpy"))

    def test_check_runs_without_opencv(self) -> None:
        """The Windows bundle has no cv2: the check decodes with numpy alone."""
        from deepfake_lens.media_marks import sdxl_check
        from deepfake_lens.wm_imwatermark import encode_array_for_tests

        path = _save_png(encode_array_for_tests(_noise(), SDXL_BITS), self.root / "nocv2.png")
        with mock.patch.dict(sys.modules, {"cv2": None}):
            self.assertEqual(sdxl_check(path).matched, "BGR")

    @unittest.skipUnless(HAVE_CV2, "opencv required for the reference conversion")
    def test_yuv_matches_opencv_for_every_colour(self) -> None:
        import cv2
        import numpy as np

        from deepfake_lens.wm_imwatermark import bgr_to_yuv_u8

        every = np.arange(1 << 24, dtype=np.uint32)
        bgr = np.stack([(every >> 16) & 255, (every >> 8) & 255, every & 255], axis=-1).astype(np.uint8).reshape(4096, 4096, 3)
        self.assertTrue(np.array_equal(bgr_to_yuv_u8(bgr), cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV)))

    @unittest.skipUnless(HAVE_CV2, "opencv required for the reference conversion")
    def test_fixture_encoder_matches_opencv_round_trip(self) -> None:
        import cv2
        import numpy as np

        from deepfake_lens.wm_imwatermark import _bgr_to_yuv_f32, _yuv_to_bgr_f32

        bgr = np.random.RandomState(3).uniform(0, 255, (256, 256, 3)).astype(np.float32)
        yuv = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV)
        np.testing.assert_allclose(_bgr_to_yuv_f32(bgr), yuv, rtol=0, atol=1e-3)
        np.testing.assert_allclose(_yuv_to_bgr_f32(yuv), cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR), rtol=0, atol=1e-3)

    def test_decoder_error_is_a_failed_check(self) -> None:
        from deepfake_lens.media_marks import MarkLayers, _sdxl_layer
        from deepfake_lens.result_types import CoverageStatus

        broken = self.root / "broken.png"
        broken.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        layers = MarkLayers()
        _sdxl_layer(broken, layers)
        self.assertEqual(layers.coverage[0].status, CoverageStatus.FAILED)

    @unittest.skipUnless(HAVE_LIBRARY, "invisible-watermark + PyWavelets required (tech image)")
    def test_decoder_is_bit_identical_to_the_library(self) -> None:
        import numpy as np

        from deepfake_lens.wm_imwatermark import decode_array

        # imwatermark.rivaGan imports torch at import time; dwtDct does not use it.
        stub = {} if importlib.util.find_spec("torch") is not None else {"torch": types.ModuleType("torch")}
        with mock.patch.dict(sys.modules, stub):
            from imwatermark import WatermarkDecoder

        for seed in range(6):
            rgb = _noise(size=384, seed=seed, amplitude=20 + 10 * seed)
            reference = "".join(str(int(bit)) for bit in WatermarkDecoder("bits", 48).decode(np.ascontiguousarray(rgb[:, :, ::-1]), "dwtDct"))
            self.assertEqual(decode_array(rgb).bits_bgr, reference)


if __name__ == "__main__":
    unittest.main()

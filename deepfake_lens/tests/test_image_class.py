"""Unit tests for the photo/non-photo gate (WP-D: G13) and the pixel fusion (G3)."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepfake_lens import image_class as ic
from deepfake_lens.core import analyze_file
from deepfake_lens.evidence_rules import image_class_evidence
from deepfake_lens.pixel import PixelExpertResult, _fuzzy_decision_tree_fusion
from deepfake_lens.result_types import (
    CHECK_LABELS,
    CoverageStatus,
    EvidenceDirection,
    EvidenceKind,
    EvidenceStrength,
    Verdict,
)

try:
    import numpy as np
    from PIL import Image

    HAVE_IMAGING = True
except ImportError:  # pragma: no cover - no-extras CI job
    HAVE_IMAGING = False

needs_imaging = unittest.skipUnless(HAVE_IMAGING, "numpy/Pillow 미설치")


def _expert(name: str, family: str, score: int, weight: float, available: bool = True) -> PixelExpertResult:
    return PixelExpertResult(name, family, score, weight, available, "detail", "ref")


class PixelFusionTest(unittest.TestCase):
    """G3: the fusion is the weighted mean — no floors, no bonuses."""

    def test_two_high_experts_no_longer_floor_at_72(self) -> None:
        experts = [_expert("a", "spectral", 70, 0.3), _expert("b", "pixel", 70, 0.3), _expert("c", "statistical", 0, 0.4)]
        score, detail = _fuzzy_decision_tree_fusion(experts)
        self.assertEqual(score, 42)  # (70*.3 + 70*.3) / 1.0 — formerly max(72, ...)
        self.assertIn("가중평균 42", detail)
        self.assertIn("하한·가산 규칙 없음", detail)

    def test_single_medium_expert_no_longer_floor_at_34(self) -> None:
        experts = [_expert("a", "pixel", 50, 0.2), _expert("b", "frequency", 0, 0.8)]
        score, _ = _fuzzy_decision_tree_fusion(experts)
        self.assertEqual(score, 10)  # formerly max(34, ...)

    def test_unavailable_experts_ignored_and_empty_is_zero(self) -> None:
        self.assertEqual(_fuzzy_decision_tree_fusion([_expert("a", "pixel", 90, 1.0, available=False)])[0], 0)
        experts = [_expert("a", "pixel", 90, 1.0, available=False), _expert("b", "pixel", 40, 1.0)]
        self.assertEqual(_fuzzy_decision_tree_fusion(experts)[0], 40)


class GateHelpersTest(unittest.TestCase):
    def test_resolution_gate_is_single_source(self) -> None:
        from deepfake_lens import core

        self.assertEqual(core.MODEL_MIN_SIDE_PX, ic.MEASURABLE_MIN_SIDE_PX)
        self.assertIsNone(ic.resolution_out_of_range(None))
        self.assertIsNone(ic.resolution_out_of_range((128, 128)))
        reason = ic.resolution_out_of_range((300, 64))
        self.assertEqual(reason, "측정 범위 밖: 해상도 300x64 (최소 변 128px 미만)")

    def test_too_small_from_header_needs_no_decoding(self) -> None:
        with patch.object(ic, "np", None):
            result = ic.classify_image("/nonexistent.png", dimensions=(96, 64))
        self.assertEqual(result.kind, ic.TOO_SMALL)
        self.assertEqual(result.skip_reason(), "측정 범위 밖: 해상도 96x64 (최소 변 128px 미만)")
        self.assertIsNone(ic.too_small_class(128, 10))

    def test_missing_numpy_is_a_dependency_skip(self) -> None:
        with patch.object(ic, "np", None):
            with self.assertRaises(ModuleNotFoundError) as caught:
                ic.classify_image("/nonexistent.png", dimensions=(640, 480))
        self.assertEqual(caught.exception.name, "numpy")

    def test_labels_and_skip_reason(self) -> None:
        self.assertIn("image_class", CHECK_LABELS)
        for kind in ic.IMAGE_KINDS:
            self.assertIn(kind, ic.KIND_LABELS)
        self.assertEqual(ic.ImageClass(ic.SCREENSHOT, ["x"]).skip_reason(), "사진 아님: screenshot")
        self.assertTrue(ic.ImageClass(ic.PHOTO).is_photo)

    def test_class_evidence_is_deterministic_neutral_weak(self) -> None:
        self.assertEqual(image_class_evidence(None), [])
        for kind in ic.IMAGE_KINDS:
            (item,) = image_class_evidence(ic.ImageClass(kind, ["근거"]))
            self.assertEqual(item.title, f"이미지 유형: {ic.KIND_LABELS[kind]}")
            self.assertEqual(
                (item.kind, item.direction, item.strength, item.layer),
                (EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "image_class"),
            )
            self.assertIsNone(item.probability)


@needs_imaging
class ClassifierRulesTest(unittest.TestCase):
    def _classify(self, array: "np.ndarray") -> ic.ImageClass:
        return ic.classify_image(array)

    def test_flat_and_gradient_are_patterns(self) -> None:
        flat: np.ndarray = np.full((300, 400, 3), 90, dtype=np.uint8)
        self.assertEqual(self._classify(flat).kind, ic.PATTERN)
        ramp = np.tile(np.linspace(0, 255, 400)[None, :, None], (300, 1, 3))
        result = self._classify(ramp)
        self.assertEqual(result.kind, ic.PATTERN)
        self.assertTrue(any("그라데이션" in reason or "잔차" in reason for reason in result.reasons))

    def test_white_noise_and_gray_input(self) -> None:
        rng = np.random.default_rng(7)
        noise = rng.integers(0, 256, size=(256, 256), dtype=np.uint8)  # 2-D gray
        result = self._classify(noise)
        self.assertEqual(result.kind, ic.PATTERN)
        self.assertTrue(any("백색 잡음" in reason for reason in result.reasons))

    def test_graphic_rule(self) -> None:
        canvas = Image.new("RGB", (800, 600), (240, 240, 250))
        from PIL import ImageDraw

        draw = ImageDraw.Draw(canvas)
        rng = np.random.default_rng(1)
        for _ in range(12):
            x, y = (int(v) for v in rng.integers(0, 700, 2))
            r = int(rng.integers(20, 120))
            draw.ellipse((x, y, x + r, y + r), fill=tuple(int(v) for v in rng.integers(0, 255, 3)))
        # Anti-aliased resampling adds >64 colours, so the palette rule
        # does not fire; flat area and low colour ratio make it a graphic.
        graphic = canvas.resize((400, 300), Image.LANCZOS).resize((800, 600), Image.BICUBIC)
        result = self._classify(np.asarray(graphic))
        self.assertEqual(result.kind, ic.GRAPHIC, result.stats)

    def test_screen_resolution_alone_is_not_a_screenshot(self) -> None:
        rng = np.random.default_rng(3)
        # A noisy 1080x1920 frame: screen-sized but no solid bar / exact edges.
        frame = np.clip(128 + rng.normal(0, 20, size=(1920, 1080, 3)), 0, 255).astype(np.uint8)
        result = self._classify(frame)
        self.assertNotEqual(result.kind, ic.SCREENSHOT)
        self.assertTrue(result.stats["screen_resolution"])

    def test_rgba_and_sixteen_bit_inputs_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rgba = Path(tmp) / "a.png"
            Image.new("RGBA", (200, 150), (10, 20, 30, 0)).save(rgba)
            self.assertEqual(ic.classify_image(rgba).kind, ic.PATTERN)
            deep = Path(tmp) / "b.png"
            Image.fromarray(np.full((150, 200), 4000, dtype=np.uint16)).save(deep)
            self.assertEqual(ic.classify_image(deep).kind, ic.PATTERN)
            buffer = io.BytesIO()
            Image.new("RGB", (50, 40)).save(buffer, format="PNG")
            buffer.seek(0)
            self.assertEqual(ic.classify_image(buffer).kind, ic.TOO_SMALL)

    def test_deterministic(self) -> None:
        rng = np.random.default_rng(11)
        image = rng.integers(0, 256, size=(200, 300, 3), dtype=np.uint8)
        first, second = self._classify(image), self._classify(image.copy())
        self.assertEqual(first, second)


@needs_imaging
class CoreGateIntegrationTest(unittest.TestCase):
    def _flat_png(self, root: Path) -> Path:
        path = root / "flat.png"
        Image.new("RGB", (300, 200), (200, 50, 50)).save(path)
        return path

    def test_gate_unavailable_lets_detectors_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._flat_png(Path(tmp))
            with patch("deepfake_lens.core.classify_image", side_effect=ModuleNotFoundError("numpy", name="numpy")):
                result = analyze_file(path, pixel_mode="fast").result
            assert result is not None
            coverage = {entry.check: entry for entry in result.coverage}
            self.assertEqual(coverage["image_class"].status, CoverageStatus.SKIPPED)
            self.assertEqual(coverage["image_class"].reason, "의존성 부재: numpy")
            self.assertFalse(coverage["pixel"].reason.startswith("사진 아님"))
            self.assertFalse(any(item.layer == "image_class" for item in result.evidence))

    def test_gate_failure_is_recorded_and_forces_undetermined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._flat_png(Path(tmp))
            with patch("deepfake_lens.core.classify_image", side_effect=ValueError("decoder exploded")):
                result = analyze_file(path).result
            assert result is not None
            coverage = {entry.check: entry for entry in result.coverage}
            self.assertEqual(coverage["image_class"].status, CoverageStatus.FAILED)
            self.assertTrue(coverage["image_class"].reason.startswith("ValueError"))
            self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)

    def test_non_photo_keeps_metadata_conclusion(self) -> None:
        from PIL import PngImagePlugin

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "marked.png"
            info = PngImagePlugin.PngInfo()
            info.add_text("parameters", "a cat\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, Size: 512x512, Model: sd15")
            Image.new("RGB", (512, 512), (128, 128, 128)).save(path, pnginfo=info)
            result = analyze_file(path).result
            assert result is not None
            self.assertEqual(result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
            coverage = {entry.check: entry for entry in result.coverage}
            self.assertEqual(coverage["external_model"].reason, "사진 아님: pattern")


if __name__ == "__main__":
    unittest.main()

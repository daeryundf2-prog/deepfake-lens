"""techniques-2026 item 5: Meta VideoSeal / AudioSeal checks (optional torch extra).

The rule cases replace the decoders with recorded readings (no torch). The
real-run cases need torch (+ audioseal) and the pinned weights in the models
folder — the ``tech`` gate image.
"""

from __future__ import annotations

import importlib.util
import math
import os
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from deepfake_lens.result_types import CoverageStatus, EvidenceDirection, EvidenceStrength

REPO = Path(__file__).resolve().parents[2]
PHOTO = REPO / "fixtures" / "benchmark" / "real-like-texture.png"
HAVE_TORCH = importlib.util.find_spec("torch") is not None
HAVE_AUDIOSEAL = HAVE_TORCH and importlib.util.find_spec("audioseal") is not None


def _weight(name: str) -> bool:
    from deepfake_lens.vendor_weights import default_models_dir

    return (default_models_dir() / name).is_file()


def _wav(path: Path, seconds: float = 3.0) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / 16000))) for i in range(int(16000 * seconds))))
    return path


def _registry(entries: list[dict[str, object]]):
    from deepfake_lens.media_marks import Registry

    return Registry("test", "0" * 64, tuple(entries))


class VideoSealRuleTest(unittest.TestCase):
    def _layer(self, bits: str, entries: list[dict[str, object]]):
        from deepfake_lens import wm_meta
        from deepfake_lens.media_marks import MarkLayers, _videoseal_layer

        reading = wm_meta.VideoSealReading(bits, 1.5, 1)
        layers = MarkLayers()
        with mock.patch.dict(sys.modules, {"torch": sys.modules.get("torch") or mock.MagicMock()}), \
                mock.patch.object(wm_meta, "videoseal_read_image", return_value=reading), \
                mock.patch("deepfake_lens.media_marks.load_registry", return_value=_registry(entries)):
            _videoseal_layer(PHOTO, layers, video=False)
        return layers

    def test_decoded_message_without_registry_entry_is_no_information(self) -> None:
        layers = self._layer("01" * 128, [])
        self.assertEqual(layers.evidence, [])
        [entry] = layers.coverage
        self.assertEqual(entry.status, CoverageStatus.RAN)
        self.assertIn("무정보", entry.reason)
        self.assertIn("디코드 메시지", layers.findings[0].detail)

    def test_registered_message_within_the_agreement_rule_names_the_generator(self) -> None:
        message = "01" * 128
        near = "10" + message[2:]  # 2 bits differ: 254/256
        layers = self._layer(near, [{"id": "t", "algorithm": "videoseal-1.0", "message_bits": message, "generator": "테스트 생성기"}])
        [item] = layers.evidence
        self.assertEqual((item.direction, item.strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG))
        self.assertIn("254/256", item.detail)

    def test_chance_level_agreement_never_matches(self) -> None:
        message = "01" * 128
        far = "".join("1" if i % 3 == 0 else c for i, c in enumerate(message))
        layers = self._layer(far, [{"id": "t", "algorithm": "videoseal-1.0", "message_bits": message, "generator": "x"}])
        self.assertEqual(layers.evidence, [])

    def test_without_torch_the_check_is_a_dependency_skip(self) -> None:
        from deepfake_lens.media_marks import MarkLayers, VIDEOSEAL_CHECK, _videoseal_layer

        layers = MarkLayers()
        with mock.patch.dict(sys.modules, {"torch": None}):
            _videoseal_layer(PHOTO, layers, video=False)
        self.assertEqual((layers.coverage[0].check, layers.coverage[0].status, layers.coverage[0].reason),
                         (VIDEOSEAL_CHECK, CoverageStatus.SKIPPED, "의존성 부재: torch"))


class AudioSealRuleTest(unittest.TestCase):
    def _layer(self, probability: float, bits: str = "1010101010101010", entries=()):
        from deepfake_lens import wm_meta
        from deepfake_lens.media_marks import MarkLayers, _audioseal_layer

        layers = MarkLayers()
        fake = {name: sys.modules.get(name) or mock.MagicMock() for name in ("torch", "audioseal")}
        with mock.patch.dict(sys.modules, fake), \
                mock.patch.object(wm_meta, "audioseal_read", return_value=wm_meta.AudioSealReading(probability, bits, 3.0)), \
                mock.patch("deepfake_lens.media_marks.load_registry", return_value=_registry(list(entries))):
            _audioseal_layer(Path("a.wav"), layers)
        return layers

    def test_detection_is_a_neutral_fact(self) -> None:
        layers = self._layer(0.93)
        [item] = layers.evidence
        self.assertEqual((item.direction, item.strength), (EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK))
        self.assertIn("AI 생성 여부를 뜻하지 않습니다", item.detail)

    def test_below_threshold_is_no_information(self) -> None:
        layers = self._layer(0.12)
        self.assertEqual(layers.evidence, [])
        self.assertIn("무정보", layers.coverage[0].reason)

    def test_exact_registered_message_names_the_generator(self) -> None:
        layers = self._layer(0.93, "1100110011001100", [{"id": "a", "algorithm": "audioseal-16", "message_bits": "1100110011001100", "generator": "g"}])
        self.assertEqual([i.direction for i in layers.evidence], [EvidenceDirection.NEUTRAL, EvidenceDirection.SYNTHETIC])
        one_off = self._layer(0.93, "1100110011001101", [{"id": "a", "algorithm": "audioseal-16", "message_bits": "1100110011001100", "generator": "g"}])
        self.assertEqual([i.direction for i in one_off.evidence], [EvidenceDirection.NEUTRAL])

    def test_without_the_audioseal_package_the_check_is_a_dependency_skip(self) -> None:
        from deepfake_lens.media_marks import MarkLayers, _audioseal_layer

        layers = MarkLayers()
        with mock.patch.dict(sys.modules, {"torch": sys.modules.get("torch") or mock.MagicMock(), "audioseal": None}):
            _audioseal_layer(Path("a.wav"), layers)
        self.assertEqual((layers.coverage[0].status, layers.coverage[0].reason), (CoverageStatus.SKIPPED, "의존성 부재: audioseal"))


@unittest.skipUnless(HAVE_TORCH, "torch required (tech image)")
class MetaWeightsTest(unittest.TestCase):
    def test_missing_weights_are_skipped_with_the_fetch_script(self) -> None:
        from deepfake_lens.media_marks import MarkLayers, _videoseal_layer

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"DEEPFAKE_LENS_MODELS_DIR": tmp}):
            layers = MarkLayers()
            _videoseal_layer(PHOTO, layers, video=False)
        self.assertEqual(layers.coverage[0].status, CoverageStatus.SKIPPED)
        self.assertIn("가중치 없음: videoseal-y_256b_img.jit", layers.coverage[0].reason)

    def test_weights_with_other_bytes_are_refused(self) -> None:
        from deepfake_lens import wm_meta
        from deepfake_lens.media_marks import MarkLayers, _videoseal_layer

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"DEEPFAKE_LENS_MODELS_DIR": tmp}), \
                mock.patch.dict(wm_meta._MODELS, clear=True):
            Path(tmp, "videoseal-y_256b_img.jit").write_bytes(b"not the pinned model")
            layers = MarkLayers()
            _videoseal_layer(PHOTO, layers, video=False)
        self.assertEqual(layers.coverage[0].status, CoverageStatus.FAILED)
        self.assertIn("미고정 모델: videoseal-y_256b_img.jit", layers.coverage[0].reason)


@unittest.skipUnless(HAVE_TORCH and _weight("videoseal-y_256b_img.jit"), "torch + pinned VideoSeal weights required (tech image)")
class VideoSealRealRunTest(unittest.TestCase):
    def test_embedded_registered_message_is_matched_after_png_round_trip(self) -> None:
        import numpy as np
        import torch
        from PIL import Image

        from deepfake_lens import wm_meta
        from deepfake_lens.media_marks import MarkLayers, _videoseal_layer

        model = wm_meta._videoseal()
        rng = np.random.RandomState(4)
        cover = np.clip(128 + rng.normal(0, 40, (256, 256, 3)), 0, 255).astype(np.uint8)
        message = "".join(rng.choice(["0", "1"]) for _ in range(256))
        with torch.inference_mode():
            marked = model.embed(torch.from_numpy(cover.astype(np.float32) / 255).permute(2, 0, 1)[None],
                                 torch.tensor([[float(b) for b in message]]))
        image = (marked[0].clamp(0, 1).permute(1, 2, 0).numpy() * 255).round().astype(np.uint8)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "vs.png"
            Image.fromarray(image).save(path)
            layers = MarkLayers()
            with mock.patch("deepfake_lens.media_marks.load_registry",
                            return_value=_registry([{"id": "t", "algorithm": "videoseal-1.0", "message_bits": message, "generator": "테스트"}])):
                _videoseal_layer(path, layers, video=False)
            [item] = layers.evidence
            self.assertEqual(item.direction, EvidenceDirection.SYNTHETIC)
            plain = MarkLayers()
            _videoseal_layer(PHOTO, plain, video=False)
            self.assertEqual(plain.evidence, [])


@unittest.skipUnless(HAVE_AUDIOSEAL and _weight("audioseal-detector_base.pth") and importlib.util.find_spec("numpy") is not None
                     and __import__("shutil").which("ffmpeg"), "torch + audioseal + pinned detector + ffmpeg required (tech image)")
class AudioSealRealRunTest(unittest.TestCase):
    def test_clean_tone_is_not_detected(self) -> None:
        from deepfake_lens.media_marks import MarkLayers, _audioseal_layer

        with tempfile.TemporaryDirectory() as tmp:
            layers = MarkLayers()
            _audioseal_layer(_wav(Path(tmp) / "tone.wav"), layers)
        self.assertEqual(layers.coverage[0].status, CoverageStatus.RAN)
        self.assertEqual(layers.evidence, [])


if __name__ == "__main__":
    unittest.main()


class WeightPinsAgreeTest(unittest.TestCase):
    """The tech gate image and the fetch script download exactly the bytes models/assets.json pins."""

    def test_dockerfile_and_fetch_script_use_the_pinned_digests(self) -> None:
        import re

        from deepfake_lens.model_assets import AUDIOSEAL_WEIGHTS, TRUSTMARK_DECODERS, VIDEOSEAL_WEIGHTS, expected_sha256

        dockerfile = (REPO / "scripts" / "gate" / "Dockerfile.tech").read_text(encoding="utf-8")
        fetch = (REPO / "scripts" / "fetch_watermark_models.py").read_text(encoding="utf-8")
        for name in (*TRUSTMARK_DECODERS, VIDEOSEAL_WEIGHTS, AUDIOSEAL_WEIGHTS):
            with self.subTest(asset=name):
                digest = expected_sha256(name)
                assert digest is not None
                match = re.search(rf'"{re.escape(name)}": \("([^"]+)", "([0-9a-f]{{64}})"\)', dockerfile)
                assert match is not None
                self.assertEqual(match.group(2), digest)
                self.assertIn(f'"{name}": "{match.group(1)}"', fetch)

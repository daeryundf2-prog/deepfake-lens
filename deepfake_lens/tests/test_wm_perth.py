"""TB-2: Resemble AI PerTh implicit watermark — presence is a fact; the registered Chatterbox entry is moderate, never a conclusion."""

from __future__ import annotations

import importlib.util
import json
import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from deepfake_lens.decision import decide
from deepfake_lens.media_marks import PERTH_CHECK, PERTH_FOUND_TITLE, PERTH_REGISTERED_TITLE, MarkLayers, _perth_layer, load_registry
from deepfake_lens.result_types import CoverageStatus, EvidenceDirection, EvidenceStrength, Grade, Verdict

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures" / "perth"
HAVE_TORCH = importlib.util.find_spec("torch") is not None
HAVE_STACK = HAVE_TORCH and importlib.util.find_spec("librosa") is not None and shutil.which("ffmpeg") is not None


def _weight_present() -> bool:
    from deepfake_lens.vendor_weights import default_models_dir
    from deepfake_lens.wm_perth import PERTH_ASSET

    return (default_models_dir() / PERTH_ASSET).is_file()


def _tone(path: Path, seconds: float = 2.0, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 220 * i / rate))) for i in range(int(seconds * rate))))
    return path


class RegistryTest(unittest.TestCase):
    def test_chatterbox_entry_is_registered_with_its_source(self) -> None:
        [entry] = load_registry().for_algorithm("perth-implicit")
        self.assertEqual(entry["expected_payload"], "1")
        self.assertIn("resemble-ai/chatterbox", entry["source"])

    def test_pins_agree(self) -> None:
        import re

        from deepfake_lens.model_assets import PERTH_WEIGHTS, expected_sha256

        digest = expected_sha256(PERTH_WEIGHTS)
        assert digest is not None
        self.assertIn(digest, (REPO / "scripts" / "gate" / "Dockerfile.tech2").read_text(encoding="utf-8"))
        fetch = (REPO / "scripts" / "fetch_watermark_models.py").read_text(encoding="utf-8")
        wheel = re.search(r'WHEEL = "([0-9a-f]{64})"', (REPO / "scripts" / "gate" / "Dockerfile.tech2").read_text(encoding="utf-8"))
        assert wheel is not None
        self.assertIn(wheel.group(1), fetch)


class SemanticsTest(unittest.TestCase):
    """The layer with a stubbed decoder reading: what a presence may and may not say."""

    def _layers(self, confidence: float) -> MarkLayers:
        from deepfake_lens.wm_perth import PerthReading

        layers = MarkLayers()
        with mock.patch.dict("sys.modules", {"torch": mock.MagicMock()}), \
                mock.patch("deepfake_lens.wm_perth.perth_read", return_value=PerthReading(confidence, 4.0, 24000)):
            _perth_layer(Path("x.wav"), layers)
        return layers

    def test_presence_is_a_weak_fact_plus_a_moderate_registered_item(self) -> None:
        layers = self._layers(0.97)
        [entry] = layers.coverage
        self.assertEqual(entry.status, CoverageStatus.RAN)
        titles = {e.title: e for e in layers.evidence}
        self.assertEqual((titles[PERTH_FOUND_TITLE].direction, titles[PERTH_FOUND_TITLE].strength), (EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK))
        registered = titles[PERTH_REGISTERED_TITLE]
        self.assertEqual((registered.direction, registered.strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE))
        # Never a conclusion by itself (decision rule 2 needs a strong item); it blocks rule 5.
        self.assertEqual(decide(layers.evidence, layers.coverage, Grade.EVIDENCE), Verdict.UNDETERMINED)
        self.assertEqual(layers.findings[0].outcome, "found")

    def test_absence_is_no_information(self) -> None:
        layers = self._layers(0.01)
        self.assertEqual(layers.evidence, [])
        self.assertIn("무정보", layers.coverage[0].reason)
        self.assertEqual(layers.findings[0].outcome, "not_found")


class EnvironmentTest(unittest.TestCase):
    @unittest.skipIf(HAVE_TORCH, "torch is installed")
    def test_without_torch_the_check_is_skipped(self) -> None:
        layers = MarkLayers()
        with tempfile.TemporaryDirectory() as tmp:
            _perth_layer(_tone(Path(tmp) / "a.wav"), layers)
        self.assertEqual(layers.coverage[0].status, CoverageStatus.SKIPPED)
        self.assertIn("의존성 부재: torch", layers.coverage[0].reason)

    @unittest.skipUnless(HAVE_TORCH, "torch required")
    def test_other_weight_bytes_fail_closed(self) -> None:
        from deepfake_lens import wm_perth

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / wm_perth.PERTH_ASSET).write_bytes(b"not the pinned checkpoint")
            layers = MarkLayers()
            with mock.patch("deepfake_lens.vendor_weights.default_models_dir", return_value=folder):
                wm_perth._MODELS.clear()
                _perth_layer(_tone(folder / "a.wav"), layers)
        self.assertEqual(layers.coverage[0].status, CoverageStatus.FAILED)
        self.assertIn("미고정 모델", layers.coverage[0].reason)

    @unittest.skipUnless(HAVE_TORCH, "torch required")
    def test_missing_weight_is_skipped_with_the_fetch_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layers = MarkLayers()
            with mock.patch("deepfake_lens.vendor_weights.default_models_dir", return_value=Path(tmp)):
                _perth_layer(_tone(Path(tmp) / "a.wav"), layers)
        self.assertEqual(layers.coverage[0].status, CoverageStatus.SKIPPED)
        self.assertIn("fetch_watermark_models.py", layers.coverage[0].reason)


class TonalGuardTest(unittest.TestCase):
    """Out of the measured (speech) domain: a pure tone is skipped instead of decoded (a 440 Hz sine scored 0.99)."""

    @unittest.skipUnless(importlib.util.find_spec("numpy") is not None, "numpy required")
    def test_tonal_ratio_separates_tones_from_voiced_signals(self) -> None:
        import numpy as np

        from deepfake_lens.wm_perth import MAX_TONAL_RATIO, tonal_ratio

        t = np.arange(48000 * 3) / 48000
        self.assertGreaterEqual(tonal_ratio(0.3 * np.sin(2 * np.pi * 440 * t), 48000), MAX_TONAL_RATIO)
        with wave.open(str(FIXTURES / "synthetic-perth.wav"), "rb") as handle:
            voiced = np.frombuffer(handle.readframes(handle.getnframes()), dtype="<i2") / 32768.0
        self.assertLess(tonal_ratio(voiced, 24000), MAX_TONAL_RATIO)

    @unittest.skipUnless(HAVE_STACK, "torch, librosa and ffmpeg required (tech2 image)")
    def test_a_sine_is_skipped(self) -> None:
        if not _weight_present():
            self.skipTest("perth-implicit-250000.pth.tar not in the models folder (tech2 image)")
        layers = MarkLayers()
        with tempfile.TemporaryDirectory() as tmp:
            _perth_layer(_tone(Path(tmp) / "sine.wav", seconds=3.0, rate=48000), layers)
        self.assertEqual(layers.coverage[0].status, CoverageStatus.SKIPPED)
        self.assertIn("순음", layers.coverage[0].reason)
        self.assertEqual(layers.evidence, [])


@unittest.skipUnless(HAVE_STACK, "torch, librosa and ffmpeg required (tech2 image)")
class DecoderTest(unittest.TestCase):
    """Against the confidences resemble-perth 1.0.1 itself computed for the fixtures (fixtures/perth/README.md)."""

    def setUp(self) -> None:
        if not _weight_present():
            self.skipTest("perth-implicit-250000.pth.tar not in the models folder (tech2 image)")

    def test_matches_the_reference_package(self) -> None:
        from deepfake_lens import wm_perth

        reference = json.loads((FIXTURES / "reference.json").read_text(encoding="utf-8"))
        for name, expected in reference["confidence"].items():
            with self.subTest(file=name):
                reading = wm_perth.perth_read(FIXTURES / name)
                self.assertAlmostEqual(reading.confidence, expected, delta=1e-3)
        self.assertTrue(wm_perth.perth_read(FIXTURES / "synthetic-perth.wav").present)
        self.assertFalse(wm_perth.perth_read(FIXTURES / "synthetic-clean.wav").present)

    def test_audio_row(self) -> None:
        from deepfake_lens.core import analyze_file

        result = analyze_file(FIXTURES / "synthetic-perth.wav").result
        assert result is not None
        [entry] = [c for c in result.coverage if c.check == PERTH_CHECK]
        self.assertEqual(entry.status, CoverageStatus.RAN)
        self.assertIn(PERTH_REGISTERED_TITLE, [e.title for e in result.evidence])
        self.assertNotEqual(result.verdict_code, Verdict.MANIPULATION_EVIDENCE)


if __name__ == "__main__":
    unittest.main()

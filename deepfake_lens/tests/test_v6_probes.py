"""V6 research-candidate probes: speaker/stylometry compare, lipsync, KGW."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, call, patch


def _write_sine_wav(path: Path, *, freq: float = 220.0, seconds: float = 6.0, rate: int = 22050) -> None:
    import math
    import struct

    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(
            b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / rate)))
                for i in range(frames)
            )
        )


class SpeakerComparisonTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import librosa  # noqa: F401
        except ImportError:
            self.skipTest("librosa not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def _mfcc_only(self):
        """Force the MFCC fallback — sine tones aren't speech, so an
        installed ECAPA model's behaviour on them is meaningless."""
        import unittest.mock

        import deepfake_lens.audio as audio_mod

        return unittest.mock.patch.object(
            audio_mod, "_ecapa_speaker_similarity", return_value=None
        )

    def test_identical_audio_is_same_speaker(self) -> None:
        from deepfake_lens.audio import compare_speakers

        a = self.dir / "a.wav"
        b = self.dir / "b.wav"
        _write_sine_wav(a, freq=220.0)
        _write_sine_wav(b, freq=220.0)
        with self._mfcc_only():
            result = compare_speakers(a, b)
        self.assertEqual(result.band, "same")
        self.assertGreaterEqual(result.same_speaker_score, 67)

    def test_very_different_audio_scores_lower(self) -> None:
        from deepfake_lens.audio import compare_speakers

        a = self.dir / "a.wav"
        b = self.dir / "b.wav"
        _write_sine_wav(a, freq=110.0)
        _write_sine_wav(b, freq=1800.0)
        with self._mfcc_only():
            result = compare_speakers(a, b)
        self.assertLess(result.same_speaker_score, 67)
        self.assertTrue(result.limitations)

    def test_missing_file_is_graceful(self) -> None:
        from deepfake_lens.audio import compare_speakers

        a = self.dir / "a.wav"
        _write_sine_wav(a)
        result = compare_speakers(a, self.dir / "missing.wav")
        self.assertEqual(result.band, "unknown")

    @unittest.skipUnless(os.environ.get("DEEPFAKE_LENS_MODEL_TESTS") == "1", "set DEEPFAKE_LENS_MODEL_TESTS=1")
    def test_ecapa_self_comparison_when_available(self) -> None:
        """With speechbrain installed, same file → ECAPA path, high score."""
        try:
            import speechbrain  # noqa: F401
            import soundfile  # noqa: F401
        except ImportError:
            self.skipTest("speechbrain/soundfile not installed")
        from deepfake_lens.audio import compare_speakers

        a = self.dir / "a.wav"
        _write_sine_wav(a, freq=220.0)
        result = compare_speakers(a, a)
        self.assertIn("ECAPA", result.verdict)
        self.assertGreaterEqual(result.same_speaker_score, 60)


class StylometryComparisonTest(unittest.TestCase):
    def test_similar_texts_same_author(self) -> None:
        from deepfake_lens.text_advanced import compare_texts

        a = "인공지능 기술은 빠르게 발전하고 있으며, 다양한 산업 분야에 적용되고 있다. 또한, 윤리적 문제도 함께 논의된다. " * 8
        b = "인공지능의 발전 속도는 매우 빠르며, 여러 산업 영역에 활용되고 있다. 또한, 윤리 문제가 함께 거론된다. " * 8
        result = compare_texts(a, b)
        self.assertEqual(result.band, "same")
        self.assertGreaterEqual(result.same_author_score, 67)

    def test_different_style_scores_lower(self) -> None:
        from deepfake_lens.text_advanced import compare_texts

        formal = "본 보고서는 인공지능 기술의 산업 적용 현황을 체계적으로 분석한다. 또한, 향후 발전 방향에 대해 논의한다. " * 8
        casual = "야 이거 진짜 대박임 ㅋㅋㅋ 완전 신기해! 나 어제 봤는데 말이 안 됨 ㅋㅋ " * 8
        result = compare_texts(formal, casual)
        self.assertLess(result.same_author_score, 50)

    def test_short_text_adds_limitation(self) -> None:
        from deepfake_lens.text_advanced import compare_texts

        result = compare_texts("짧다.", "짧다.")
        self.assertTrue(any("400자" in lim for lim in result.limitations))


class LipsyncTest(unittest.TestCase):
    def test_nonexistent_video_unavailable(self) -> None:
        from deepfake_lens.lipsync import analyze_lipsync

        result = analyze_lipsync("nonexistent.mp4")
        self.assertFalse(result.available)
        self.assertEqual(result.score, 0)

    def test_contract_fields(self) -> None:
        from deepfake_lens.lipsync import LipsyncAnalysis

        payload = LipsyncAnalysis(True, 30, "verdict", 0.05, 0.4, 100, 0.01, []).to_json()
        self.assertEqual(payload["best_correlation"], 0.05)
        self.assertIn("mouth_samples", payload)


class WatermarkTest(unittest.TestCase):
    def test_short_text_unavailable(self) -> None:
        from deepfake_lens.watermark import detect_kgw_watermark

        result = detect_kgw_watermark("짧은 텍스트", secret="key")
        self.assertFalse(result.available)

    def test_green_list_deterministic(self) -> None:
        from deepfake_lens.watermark import _green_list

        a = _green_list([1, 2], "secret", 1000, 0.25)
        b = _green_list([1, 2], "secret", 1000, 0.25)
        c = _green_list([1, 2], "other", 1000, 0.25)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(len(a), 250)

    def test_watermarked_text_detected(self) -> None:
        """Simulated KGW generation: force-sampling green tokens must score high."""
        try:
            import transformers
        except ImportError:
            transformers = ModuleType("transformers")
            transformers.AutoTokenizer = SimpleNamespace(from_pretrained=Mock())
        from deepfake_lens.watermark import _green_list, detect_kgw_watermark

        class FakeTokenizer:
            def __len__(self):
                return 1000

            def decode(self, token_ids):
                return " ".join(str(token_id) for token_id in token_ids)

            def encode(self, text, *, add_special_tokens):
                self_test.assertFalse(add_special_tokens)
                return [int(token) for token in text.split()]

        self_test = self
        tokenizer = FakeTokenizer()
        vocab = len(tokenizer)
        secret = "unit-test-secret"
        # Generate a token stream biased to the green list.
        token_ids = [100]
        rng_ids = list(range(vocab))
        import random as _r
        gen = _r.Random(42)
        for _ in range(200):
            green = sorted(_green_list(token_ids[-1:], secret, vocab, 0.25))
            pick = green[gen.randrange(len(green))] if gen.random() < 0.9 else rng_ids[gen.randrange(vocab)]
            token_ids.append(pick)
        text = tokenizer.decode(token_ids)
        self.assertGreaterEqual(len(text), 200)
        self.assertEqual(tokenizer.encode(text, add_special_tokens=False), token_ids)
        with (
            patch.dict(sys.modules, {"transformers": transformers}),
            patch.object(transformers.AutoTokenizer, "from_pretrained", return_value=tokenizer) as load_tokenizer,
            patch("socket.socket.connect", side_effect=AssertionError("unexpected network acquisition")) as connect,
        ):
            result = detect_kgw_watermark(text, secret=secret)
            self.assertTrue(result.available)
            self.assertEqual(result.token_count, len(token_ids))
            self.assertGreaterEqual(result.z_score or 0, 4.0)

            # A wrong key must NOT flag the same stream.
            wrong = detect_kgw_watermark(text, secret="wrong-key")
            self.assertTrue(wrong.available)
            self.assertLess(wrong.z_score or 0, 4.0)
            self.assertEqual(load_tokenizer.call_args_list, [call("Qwen/Qwen2.5-0.5B")] * 2)
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class CopyMoveTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            importlib.import_module("numpy")
        except ImportError:
            self.skipTest("numpy not installed")

    def _image(self, *, forged: bool):
        import numpy as np

        rng = np.random.default_rng(0)
        img = rng.normal(128, 20, (128, 128))
        if forged:
            img[70:110, 70:110] = img[10:50, 10:50]
        return img

    def test_forged_region_flagged(self) -> None:
        from deepfake_lens.frequency import copy_move_score

        ratio, detail = copy_move_score(self._image(forged=True))
        self.assertGreaterEqual(ratio, 0.03)
        self.assertIn("copy-move", detail)

    def test_clean_noise_not_flagged(self) -> None:
        from deepfake_lens.frequency import copy_move_score

        ratio, _ = copy_move_score(self._image(forged=False))
        self.assertEqual(ratio, 0.0)

    def test_repetitive_texture_suppressed(self) -> None:
        import numpy as np

        from deepfake_lens.frequency import copy_move_score

        rng = np.random.default_rng(0)
        smooth = np.tile(np.linspace(0, 255, 128), (128, 1)) + rng.normal(0, 3, (128, 128))
        ratio, _ = copy_move_score(smooth)
        self.assertLess(ratio, 0.2)


class CompareFilesTest(unittest.TestCase):
    def test_text_pair_dispatches_stylometry(self) -> None:
        from deepfake_lens.core import compare_files

        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.txt"
            b = Path(tmp) / "b.txt"
            a.write_text("인공지능 기술은 빠르게 발전하고 있으며 다양한 산업에 적용된다. 또한 윤리 문제가 함께 논의된다. " * 8, encoding="utf-8")
            b.write_text("인공지능의 발전 속도는 빠르며 여러 산업 분야에 활용된다. 또한 윤리 문제가 함께 거론된다. " * 8, encoding="utf-8")
            result = compare_files(a, b)
        self.assertEqual(result["kind"], "stylometry")
        self.assertIn("score", result)

    def test_mixed_pair_rejected(self) -> None:
        from deepfake_lens.core import compare_files

        result = compare_files(Path("a.wav"), Path("b.txt"))
        self.assertIn("error", result)


class CopyMoveKeypointTest(unittest.TestCase):
    """SIFT/ORB + transform-voting copy-move (rotation/scale robust)."""

    def setUp(self) -> None:
        try:
            importlib.import_module("numpy")
            importlib.import_module("cv2")
        except ImportError:
            self.skipTest("numpy/opencv not installed")

    def _texture(self, seed: int = 1, size: int = 300):
        import cv2
        import numpy as np

        rng = np.random.default_rng(seed)
        image = rng.normal(0, 1, (size, size))
        for k in (3, 8, 20):
            image = image + cv2.GaussianBlur(
                rng.normal(0, 1, (size, size)).astype(np.float32), (0, 0), k
            )
        return (image - image.min()) / (image.max() - image.min()) * 255

    def test_scaled_clone_flagged(self) -> None:
        import cv2

        from deepfake_lens.frequency import copy_move_keypoint_score

        base = self._texture()
        forged = base.copy()
        forged[180:264, 180:264] = cv2.resize(
            forged[30:100, 30:100].astype("uint8"), (84, 84)
        )
        ratio, detail = copy_move_keypoint_score(forged)
        self.assertGreaterEqual(ratio, 0.03)
        self.assertIn("copy-move", detail)

    def test_translated_clone_flagged(self) -> None:
        from deepfake_lens.frequency import copy_move_keypoint_score

        base = self._texture()
        forged = base.copy()
        forged[180:250, 180:250] = forged[40:110, 40:110]
        ratio, _ = copy_move_keypoint_score(forged)
        self.assertGreaterEqual(ratio, 0.03)

    def test_clean_texture_not_flagged(self) -> None:
        from deepfake_lens.frequency import copy_move_keypoint_score

        self.assertEqual(copy_move_keypoint_score(self._texture(2))[0], 0.0)

    def test_random_noise_not_flagged(self) -> None:
        import numpy as np

        from deepfake_lens.frequency import copy_move_keypoint_score

        rng = np.random.default_rng(0)
        ratio, _ = copy_move_keypoint_score(rng.normal(128, 30, (300, 300)))
        self.assertEqual(ratio, 0.0)


class SynthIDWatermarkTest(unittest.TestCase):
    """B-1: mean-g SynthID detection under own keys (transformers built-in)."""

    @unittest.skipUnless(os.environ.get("DEEPFAKE_LENS_MODEL_TESTS") == "1", "set DEEPFAKE_LENS_MODEL_TESTS=1")
    def test_wrong_key_reports_no_signal(self) -> None:
        try:
            importlib.import_module("torch")
            from transformers import AutoTokenizer
        except ImportError:
            self.skipTest("transformers/torch not installed")
        try:
            tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B", local_files_only=True)
        except OSError:
            self.skipTest("Qwen2.5-0.5B tokenizer not cached")
        from deepfake_lens.watermark import detect_synthid_watermark

        text = "The printing press was invented around 1440 by Johannes Gutenberg. It made books cheap to produce and transformed the spread of knowledge across Europe within a generation." * 3
        with patch.object(AutoTokenizer, "from_pretrained", return_value=tokenizer) as load_tokenizer:
            result = detect_synthid_watermark(text, keys=[17, 23, 42, 90, 77])
        load_tokenizer.assert_called_once_with("Qwen/Qwen2.5-0.5B")
        self.assertTrue(result.available)
        self.assertLess(result.score, 50)

    def test_missing_keys_unavailable(self) -> None:
        from deepfake_lens.watermark import detect_synthid_watermark

        result = detect_synthid_watermark("x" * 300, keys=[])
        self.assertFalse(result.available)

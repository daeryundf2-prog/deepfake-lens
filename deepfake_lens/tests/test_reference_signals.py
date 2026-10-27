"""Gated-off ``reference_signal`` profiles: raw score as a reference row only.

A stubbed runtime returns a fixed output; the scan row must carry one
``reference_signals`` entry with the raw score while verdict, evidence,
coverage and caveats stay exactly those of the same profile without the
flag.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from deepfake_lens.core import analyze_file
from deepfake_lens.measurement_gate import check_profile
from deepfake_lens.model_adapter import REFERENCE_CONFIDENCE, REFERENCE_SIGNAL_TITLE
from deepfake_lens.model_pins import is_sha256
from deepfake_lens.tests.test_core import _photo_like_pixel_fn, _write_valid_rgb_png

MODELS = Path(__file__).resolve().parents[1] / "models"
CF_PROFILE = MODELS / "community-forensics-vit-runtime.json"
AASIST_PROFILE = MODELS / "aasist-runtime.json"


def _profile_copy(source: Path, root: Path, *, reference: bool, pin_matches: bool = True) -> Path:
    profile = json.loads(source.read_text(encoding="utf-8"))
    checkpoint = root / "stub-weights.bin"
    checkpoint.write_bytes(b"stub weights")
    profile["checkpoint"] = str(checkpoint)
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest() if pin_matches else "0" * 64
    profile["pin"] = {"sha256": digest}
    profile["reference_signal"] = reference
    path = root / source.name
    path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    return path


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x10\x00\xf0" * 16000)


def _decision(item) -> dict[str, object]:
    result = item.result
    assert result is not None
    return {
        "verdict_code": result.verdict_code,
        "grade": result.grade,
        "evidence": [entry.to_json() for entry in result.evidence],
        "coverage": [entry.to_json() for entry in result.coverage],
        "conclusion_caveats": result.conclusion_caveats,
        "score": result.score,
        "probability": result.probability,
    }


class ReferenceSignalTest(unittest.TestCase):
    def _image_pair(self, *, pin_matches: bool = True):
        items = []
        for reference in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                photo = root / "photo.png"
                _write_valid_rgb_png(photo, 160, 160, _photo_like_pixel_fn(160, 160, seed=3))
                models = root / "models"
                models.mkdir()
                _profile_copy(CF_PROFILE, models, reference=reference, pin_matches=pin_matches)
                with patch("deepfake_lens.model_adapter._preprocess_image", return_value=None), patch(
                    "deepfake_lens.model_adapter._run_onnx", return_value=[2.0]
                ):
                    items.append(analyze_file(photo, model_path=models))
        return items

    def test_image_reference_row_and_unchanged_decision(self) -> None:
        baseline, referenced = self._image_pair()
        self.assertEqual(_decision(referenced), _decision(baseline))
        added = [s for s in referenced.result.reference_signals if s not in baseline.result.reference_signals]
        self.assertEqual(len(added), 1, referenced.result.reference_signals)
        (signal,) = added
        self.assertEqual(signal.title, REFERENCE_SIGNAL_TITLE)
        self.assertEqual(signal.weight, 88)  # sigmoid(2.0) = 0.881
        self.assertIn("88/100", signal.detail)
        self.assertIn("미측정", signal.detail)
        self.assertIn("결론과 근거 항목에 쓰이지 않습니다", signal.detail)
        model = referenced.result.model_analysis
        self.assertFalse(model.available)
        self.assertEqual(model.confidence, REFERENCE_CONFIDENCE)

    def test_pin_mismatch_adds_no_reference_row(self) -> None:
        baseline, referenced = self._image_pair(pin_matches=False)
        self.assertEqual(_decision(referenced), _decision(baseline))
        self.assertEqual(referenced.result.reference_signals, baseline.result.reference_signals)

    def test_audio_reference_row_and_unchanged_decision(self) -> None:
        items = []
        for reference in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                wav = root / "voice.wav"
                _write_wav(wav)
                profile = _profile_copy(AASIST_PROFILE, root, reference=reference)
                with patch("deepfake_lens.model_adapter._run_aasist", return_value=[3.0, 0.0]):
                    items.append(analyze_file(wav, model_path=profile))
        baseline, referenced = items
        self.assertEqual(_decision(referenced), _decision(baseline))
        added = [s for s in referenced.result.reference_signals if s not in baseline.result.reference_signals]
        self.assertEqual([s.title for s in added], [REFERENCE_SIGNAL_TITLE])
        self.assertEqual(added[0].weight, 95)  # softmax([3, 0])[0] = 0.953
        self.assertNotIn("외부 모델", " ".join(item.title for item in referenced.result.evidence))

    def test_committed_profiles_are_pinned_reference_only(self) -> None:
        for path in (CF_PROFILE, AASIST_PROFILE):
            profile = json.loads(path.read_text(encoding="utf-8"))
            self.assertIs(profile["supported"], False, path.name)
            self.assertIs(profile["reference_signal"], True, path.name)
            self.assertIsNone(profile["measured_on"], path.name)
            self.assertTrue(is_sha256(profile["pin"]["sha256"]), path.name)
            self.assertEqual(profile["license"], "MIT", path.name)
            self.assertEqual(check_profile(path), [], path.name)


if __name__ == "__main__":
    unittest.main()

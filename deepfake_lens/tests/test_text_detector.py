"""Tests for the hf-text-classifier runtime (models/openai-detector-runtime.json).

Mirrors test_aasist_engine.py: profile contract, modality filtering, and
scan plumbing. Everything must pass without torch/transformers — the real
model is exercised only in the optional-download path.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deepfake_lens.core import analyze_file, scan_directory
from deepfake_lens.model_adapter import analyze_external_model

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPO_ROOT / "models" / "openai-detector-runtime.json"
IMAGE_PROFILE_PATH = REPO_ROOT / "models" / "aide-runtime.json"
AUDIO_PROFILE_PATH = REPO_ROOT / "models" / "aasist-runtime.json"


def _write_text(path: Path, body: str = "sample text body for testing " * 8) -> None:
    path.write_text(body, encoding="utf-8")


class TextDetectorProfileTest(unittest.TestCase):
    """models/openai-detector-runtime.json must satisfy the adapter contract."""

    def test_committed_profile_matches_contract(self) -> None:
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

        self.assertEqual(profile["type"], "deepfake-lens-runtime-profile-v1")
        self.assertEqual(profile["runtime"], "hf-text-classifier")
        self.assertEqual(profile["modality"], "text")
        self.assertEqual(profile["hub_model"], "openai-community/roberta-base-openai-detector")
        # id2label verified against the model config: index 0 = Fake.
        self.assertEqual(profile["score_index"], 0)
        self.assertEqual(profile["score_activation"], "softmax")
        self.assertTrue(any("not a truth label" in item for item in profile["limitations"]))
        self.assertTrue(any("GPT-2" in item for item in profile["limitations"]))

    def test_text_profile_is_filtered_to_text_modality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            txt = Path(tmp) / "note.txt"
            _write_text(txt)

            # An image/audio profile never runs on a text file and vice versa.
            self.assertIsNone(analyze_external_model(txt, IMAGE_PROFILE_PATH, modality="text"))
            self.assertIsNone(analyze_external_model(txt, AUDIO_PROFILE_PATH, modality="text"))
            self.assertIsNone(analyze_external_model(txt, PROFILE_PATH, modality="image"))
            self.assertIsNone(analyze_external_model(txt, PROFILE_PATH, modality="audio"))

    def test_missing_transformers_degrades_gracefully(self) -> None:
        """Without torch/transformers the runtime reports unavailable, not a crash."""
        try:
            import transformers  # noqa: F401
            self.skipTest("transformers installed; unavailable-path assertion does not apply")
        except ImportError:
            pass
        with tempfile.TemporaryDirectory() as tmp:
            txt = Path(tmp) / "note.txt"
            _write_text(txt)
            analysis = analyze_external_model(txt, PROFILE_PATH, modality="text")
            self.assertIsNotNone(analysis)
            self.assertFalse(analysis.available)
            self.assertEqual(analysis.score, 0)


class TextDetectorScanTest(unittest.TestCase):
    """Scan plumbing: .txt/.md items carry model_analysis like image/audio."""

    def test_analyze_file_marks_text_and_carries_model_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            txt = root / "note.txt"
            _write_text(txt)

            item = analyze_file(txt, root=root, model_path=[PROFILE_PATH])

            self.assertEqual(item.kind, "text")
            self.assertEqual(item.status, "analyzed")
            self.assertIsNotNone(item.result)
            self.assertIsNotNone(item.result.model_analysis)
            payload = item.to_json()
            self.assertIn("model_analysis", payload["result"])

    def test_scan_without_text_profile_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_text(root / "note.txt")

            summary, items = scan_directory(root, model_path=None)

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].kind, "text")
            self.assertIsNone(items[0].result.model_analysis)


if __name__ == "__main__":
    unittest.main()

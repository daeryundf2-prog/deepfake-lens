"""Tests for the hf-text-classifier runtime (models/fakespot-detector-runtime.json).

Mirrors test_aasist_engine.py: profile contract, modality filtering, and
scan plumbing. Everything must pass without torch/transformers — the real
model is exercised only in the optional-download path.

G2/WP-C: these tests used models/openai-detector-runtime.json, which was
removed (docs/MODEL-REJECTIONS.md); they now run against the remaining
hf-text-classifier profile. The committed profile is supported:false and
unpinned, so runtime-path tests use an enabled copy with a revision pin.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepfake_lens.cli import DEFAULT_TEXT_ENGINE_PROFILE, default_text_model_path
from deepfake_lens.core import analyze_file, scan_directory
from deepfake_lens.model_adapter import FAILED_CONFIDENCE, analyze_external_model
from deepfake_lens.result_types import CoverageStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_MODELS = Path(__file__).resolve().parents[1] / "models"
PROFILE_PATH = PKG_MODELS / "fakespot-detector-runtime.json"
IMAGE_PROFILE_PATH = PKG_MODELS / "aide-runtime.json"
AUDIO_PROFILE_PATH = PKG_MODELS / "aasist-runtime.json"
# A syntactically valid hub commit id; the runtime is mocked, nothing is fetched.
FAKE_REVISION = "0123456789abcdef0123456789abcdef01234567"


def _write_text(path: Path, body: str = "sample text body for testing " * 8) -> None:
    path.write_text(body, encoding="utf-8")


def _enabled_copy(root: Path, *, revision: str = FAKE_REVISION) -> tuple[Path, dict]:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    profile.pop("supported", None)
    profile.pop("reason", None)
    profile["pin"] = {"revision": revision}
    path = root / PROFILE_PATH.name
    path.write_text(json.dumps(profile), encoding="utf-8")
    return path, profile


class TextDetectorProfileTest(unittest.TestCase):
    """models/fakespot-detector-runtime.json must satisfy the adapter contract."""

    def test_committed_profile_matches_contract(self) -> None:
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

        self.assertEqual(profile["type"], "deepfake-lens-runtime-profile-v1")
        self.assertEqual(profile["runtime"], "hf-text-classifier")
        self.assertEqual(profile["modality"], "text")
        self.assertEqual(profile["hub_model"], "fakespot-ai/roberta-base-ai-text-detection-v1")
        # id2label verified against the model config: index 1 = AI.
        self.assertEqual(profile["score_index"], 1)
        self.assertEqual(profile["score_activation"], "softmax")
        self.assertTrue(any("진위 판정이 아닙니다" in item for item in profile["limitations"]))  # R4
        self.assertEqual(profile["trained_languages"], ["en"])
        # G9: hub models are pinned by commit revision; phase 0 ships it empty.
        self.assertEqual(profile["pin"], {"revision": ""})
        self.assertIs(profile["supported"], False)

    def test_no_default_text_engine(self) -> None:
        """G2: no text model is a default member any more."""
        self.assertIsNone(DEFAULT_TEXT_ENGINE_PROFILE)
        self.assertIsNone(default_text_model_path())
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(default_text_model_path(Path(tmp)))

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
            enabled, _ = _enabled_copy(Path(tmp))
            analysis = analyze_external_model(txt, enabled, modality="text")
            self.assertIsNotNone(analysis)
            self.assertFalse(analysis.available)
            self.assertEqual(analysis.score, 0)
            self.assertTrue(analysis.detail.startswith("의존성 부재"), analysis.detail)

    def test_unpinned_hub_profile_is_refused_before_loading(self) -> None:
        """G10: an empty revision never reaches from_pretrained."""
        with tempfile.TemporaryDirectory() as tmp:
            txt = Path(tmp) / "note.txt"
            _write_text(txt)
            for revision, expected in (("", "미고정 프로필"), ("main", "40자리 커밋 SHA")):
                with self.subTest(revision=revision):
                    enabled, _ = _enabled_copy(Path(tmp), revision=revision)
                    with patch("deepfake_lens.model_adapter._run_hf_text_classifier") as run_classifier:
                        analysis = analyze_external_model(txt, enabled, modality="text")
                    run_classifier.assert_not_called()
                    self.assertFalse(analysis.available)
                    self.assertEqual(analysis.confidence, FAILED_CONFIDENCE)
                    self.assertTrue(analysis.detail.startswith("미고정 프로필"), analysis.detail)
                    self.assertIn(expected, analysis.detail)


class TextDetectorScanTest(unittest.TestCase):
    """Scan plumbing: .txt/.md items carry model_analysis like image/audio."""

    def test_analyze_file_marks_text_and_carries_model_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            txt = root / "note.txt"
            _write_text(txt)

            enabled, profile = _enabled_copy(root)
            with patch(
                "deepfake_lens.model_adapter._run_hf_text_classifier",
                autospec=True,
                return_value=[0.0, 2.0],
            ) as run_classifier:
                item = analyze_file(txt, root=root, model_path=[enabled])
            run_classifier.assert_called_once_with(txt, profile)

            self.assertEqual(item.kind, "text")
            self.assertEqual(item.status, "analyzed")
            self.assertIsNotNone(item.result)
            self.assertIsNotNone(item.result.model_analysis)
            self.assertTrue(item.result.model_analysis.available)
            self.assertEqual(item.result.model_analysis.score, 88)
            payload = item.to_json()
            self.assertIn("model_analysis", payload["result"])
            self.assertTrue(payload["result"]["model_analysis"]["available"])
            self.assertEqual(payload["result"]["model_analysis"]["score"], 88)

    def test_committed_profile_is_skipped_in_a_scan(self) -> None:
        """G9/WP-C: the shipped profile is supported:false -> skipped, not run."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            txt = root / "note.txt"
            _write_text(txt)
            with patch("deepfake_lens.model_adapter._run_hf_text_classifier") as run_classifier:
                item = analyze_file(txt, root=root, model_path=[PROFILE_PATH])
            run_classifier.assert_not_called()
            assert item.result is not None
            entries = [entry for entry in item.result.coverage if entry.check == "external_model"]
            self.assertEqual([entry.status for entry in entries], [CoverageStatus.SKIPPED])
            self.assertIn("측정 게이트", entries[0].reason)

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

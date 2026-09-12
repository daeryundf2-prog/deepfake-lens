"""Tests for the AASIST audio runtime adapter (models/aasist-runtime.json).

Mirrors test_aide_engine.py: profile contract, graceful degradation (no
checkpoint, no torch), fetch script behavior, and score plumbing through
analyze_audio / scan_directory on synthetic WAVs built with stdlib wave.

Torch-dependent tests skip cleanly in the base environment; everything
else must pass without torch/numpy.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from deepfake_lens.audio import analyze_audio
from deepfake_lens.cli import DEFAULT_AUDIO_ENGINE_PROFILE, default_audio_model_path, main as cli_main
from deepfake_lens.core import analyze_file, scan_directory
from deepfake_lens.model_adapter import analyze_external_model, load_model_threshold

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = REPO_ROOT / "models" / "aasist-runtime.json"
CHECKPOINT_PATH = PROFILE_PATH.parent / "aasist.pth"
IMAGE_PROFILE_PATH = REPO_ROOT / "models" / "aide-runtime.json"


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


def _checkpoint_downloaded() -> bool:
    """True when a real checkpoint is present; unavailable-path tests skip then."""
    return CHECKPOINT_PATH.exists()


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), REPO_ROOT / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_wav(path: Path, *, seconds: float = 2.0, hz: float = 220.0, rate: int = 16000) -> None:
    """Synthetic mono 16-bit PCM WAV via stdlib only."""
    frames = bytearray()
    for n in range(int(rate * seconds)):
        value = 0.3 * math.sin(2 * math.pi * hz * n / rate)
        frames += struct.pack("<h", int(value * 32767))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))


class AasistRuntimeProfileTest(unittest.TestCase):
    """models/aasist-runtime.json must satisfy the model_adapter contract."""

    def test_committed_profile_matches_adapter_contract(self) -> None:
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))

        self.assertEqual(profile["type"], "deepfake-lens-runtime-profile-v1")
        self.assertEqual(profile["runtime"], "aasist")
        self.assertEqual(profile["modality"], "audio")
        self.assertEqual(profile["sample_rate"], 16000)
        self.assertEqual(profile["window_samples"], 64600)
        self.assertEqual(profile["score_index"], 0)
        self.assertEqual(profile["score_activation"], "softmax")
        self.assertEqual(profile["score_semantics"], "spoof_probability")
        self.assertEqual(profile["source_url"], "https://github.com/clovaai/aasist")
        # Relative checkpoint paths resolve against the profile directory.
        self.assertEqual((PROFILE_PATH.parent / profile["checkpoint"]).name, "aasist.pth")
        # Honest limitations: checkpoint not committed + not a truth label.
        self.assertTrue(any("not committed" in item for item in profile["limitations"]))
        self.assertTrue(any("not a truth label" in item for item in profile["limitations"]))

    def test_load_model_threshold_reads_profile(self) -> None:
        self.assertEqual(load_model_threshold(PROFILE_PATH), 67)

    def test_missing_checkpoint_is_graceful(self) -> None:
        """Absent weights must produce available=False with a reason, not a crash."""
        if _checkpoint_downloaded():
            self.skipTest("checkpoint is present; unavailable-path assertion does not apply")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "sample.wav"
            _write_wav(wav)

            analysis = analyze_external_model(wav, PROFILE_PATH, modality="audio")

            self.assertIsNotNone(analysis)
            self.assertFalse(analysis.available)
            self.assertEqual(analysis.score, 0)
            self.assertIn("checkpoint was not found", analysis.detail)
            self.assertIn("AASIST", analysis.model)
            self.assertTrue(any("fetch_aasist" in item for item in analysis.limitations))

    def test_modality_filtering_keeps_profiles_in_lane(self) -> None:
        """An image profile never runs on audio and vice versa."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "sample.wav"
            _write_wav(wav)

            # Profiles that exist but do not match the file's modality behave
            # like no profile at all: the adapter returns None.
            image_only = analyze_external_model(wav, IMAGE_PROFILE_PATH, modality="audio")
            self.assertIsNone(image_only)

            audio_only = analyze_external_model(wav, PROFILE_PATH, modality="image")
            self.assertIsNone(audio_only)

    def test_mixed_model_path_filters_to_audio_member(self) -> None:
        """A list of profiles resolves to just the audio-modality member."""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "sample.wav"
            _write_wav(wav)

            analysis = analyze_external_model(wav, [IMAGE_PROFILE_PATH, PROFILE_PATH], modality="audio")

            self.assertIsNotNone(analysis)
            # Single surviving member -> not aggregated into a model-zoo result.
            self.assertIn("AASIST", analysis.model)
            self.assertEqual(analysis.models, [])

    def test_fake_checkpoint_never_scores(self) -> None:
        """A garbage checkpoint must degrade to unavailable, not produce a score."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / "aasist.pth"
            fake.write_bytes(b"not a real checkpoint")
            profile_copy = root / "aasist-runtime.json"
            profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            profile_copy.write_text(json.dumps(profile), encoding="utf-8")
            wav = root / "sample.wav"
            _write_wav(wav)

            analysis = analyze_external_model(wav, profile_copy, modality="audio")

            self.assertFalse(analysis.available)
            # Without torch the runtime reports 'not installed'; with torch the
            # load fails — either way no score is produced.
            self.assertEqual(analysis.score, 0)


class AasistScanPipelineTest(unittest.TestCase):
    """Scan-time plumbing: audio files get a model_analysis entry like images."""

    def test_analyze_file_marks_audio_and_carries_model_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "clip.wav"
            _write_wav(wav)

            item = analyze_file(wav, root=root, model_path=[PROFILE_PATH])

            self.assertEqual(item.kind, "audio")
            self.assertEqual(item.status, "analyzed")
            self.assertIsNotNone(item.result)
            self.assertIsNotNone(item.result.model_analysis)
            payload = item.to_json()
            self.assertIn("model_analysis", payload["result"])
            if item.result.model_analysis.available:
                self.assertEqual(item.result.model_analysis.score, payload["result"]["model_analysis"]["score"])
            else:
                self.assertFalse(payload["result"]["model_analysis"]["available"])

    def test_scan_summary_counts_external_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_wav(root / "clip.wav")

            summary, items = scan_directory(root, model_path=[PROFILE_PATH])

            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertEqual(item.kind, "audio")
            expected = 1 if item.result.model_analysis.available else 0
            self.assertEqual(summary.external_model_active, expected)
            if expected:
                self.assertGreaterEqual(summary.analyzed, 1)

    def test_scan_without_model_path_still_analyzes_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_wav(root / "clip.wav")

            summary, items = scan_directory(root, model_path=None)

            item = items[0]
            self.assertEqual(item.kind, "audio")
            self.assertEqual(item.status, "analyzed")
            self.assertIsNone(item.result.model_analysis)
            self.assertEqual(summary.external_model_active, 0)

    def test_analyze_audio_json_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            _write_wav(wav)

            analysis = analyze_audio(wav, model_path=PROFILE_PATH)

            payload = analysis.to_json()
            self.assertIn("model_analysis", payload)
            self.assertIsNotNone(payload["model_analysis"])
            self.assertIn("available", payload["model_analysis"])
            if analysis.model_analysis and analysis.model_analysis.available:
                self.assertTrue(any(signal.title == "외부 모델 강한 의심" or "외부 모델" in signal.title for signal in analysis.signals))


class AasistRunnerScriptTest(unittest.TestCase):
    """scripts/run_aasist.py behaves standalone, torch or not."""

    def test_module_loads_without_torch(self) -> None:
        module = _load_script("run_aasist.py")
        self.assertEqual(module.AASIST_CONFIG["nb_samp"], 64600)
        if not _has_torch():
            self.assertIsNotNone(module._IMPORT_ERROR)
            with self.assertRaises(ImportError):
                module.load_model(CHECKPOINT_PATH)

    def test_main_reports_unavailable_without_torch(self) -> None:
        if _has_torch():
            self.skipTest("torch is installed; unavailable-path assertion does not apply")
        module = _load_script("run_aasist.py")
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            _write_wav(wav)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = module.main(["--checkpoint", str(CHECKPOINT_PATH), "--audio", str(wav)])
            self.assertEqual(rc, 3)
            payload = json.loads(out.getvalue())
            self.assertFalse(payload["available"])

    def test_main_reports_missing_checkpoint(self) -> None:
        if not _has_torch():
            self.skipTest("without torch the script exits at the dependency check first")
        module = _load_script("run_aasist.py")
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            _write_wav(wav)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = module.main(["--checkpoint", str(Path(tmp) / "missing.pth"), "--audio", str(wav)])
            self.assertEqual(rc, 2)
            self.assertFalse(json.loads(out.getvalue())["available"])

    def test_waveform_loader_decodes_pcm_wav(self) -> None:
        module = _load_script("run_aasist.py")
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy not installed")
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            _write_wav(wav, seconds=1.0)
            samples = module.load_waveform(wav, sample_rate=16000, max_seconds=30)
            self.assertEqual(len(samples), 16000)
            self.assertLessEqual(float(samples.max()), 1.0)
            # 8 kHz source resamples up to 16 kHz.
            wav8 = Path(tmp) / "clip8k.wav"
            _write_wav(wav8, seconds=1.0, rate=8000)
            samples8 = module.load_waveform(wav8, sample_rate=16000, max_seconds=30)
            self.assertAlmostEqual(len(samples8), 16000, delta=4)

    def test_pad_mirrors_upstream(self) -> None:
        module = _load_script("run_aasist.py")
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy not installed")
        short = np.ones(100, dtype=np.float32)
        padded = module.pad(short, 64600)
        self.assertEqual(padded.shape[0], 64600)
        long = np.ones(70000, dtype=np.float32)
        self.assertEqual(module.pad(long, 64600).shape[0], 64600)


@unittest.skipUnless(_has_torch(), "torch not installed")
class AasistTorchInferenceTest(unittest.TestCase):
    """Real inference paths — skipped in the base environment."""

    def test_random_init_checkpoint_produces_bounded_score(self) -> None:
        """A fresh state dict proves plumbing end to end without real weights."""
        module = _load_script("run_aasist.py")
        import torch

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = module.AasistModel(module.AASIST_CONFIG)
            torch.save(model.state_dict(), root / "aasist.pth")
            profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
            profile_copy = root / "aasist-runtime.json"
            profile_copy.write_text(json.dumps(profile), encoding="utf-8")
            wav = root / "clip.wav"
            _write_wav(wav, seconds=6.0)  # >1 window exercises averaging

            analysis = analyze_external_model(wav, profile_copy, modality="audio")

            self.assertTrue(analysis.available, analysis.detail)
            self.assertGreaterEqual(analysis.score, 0)
            self.assertLessEqual(analysis.score, 100)

    @unittest.skipUnless(_checkpoint_downloaded(), "official checkpoint not fetched")
    def test_official_checkpoint_scores_synthetic_tone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "tone.wav"
            _write_wav(wav, seconds=5.0)

            analysis = analyze_external_model(wav, PROFILE_PATH, modality="audio")

            self.assertTrue(analysis.available, analysis.detail)
            self.assertGreaterEqual(analysis.score, 0)
            self.assertLessEqual(analysis.score, 100)
            self.assertEqual(analysis.confidence, _expected_confidence(analysis.score))

    def _expected_confidence(self, score: int) -> str:
        return "high" if score >= 80 else "medium" if score >= 50 else "low"


class FetchAasistTest(unittest.TestCase):
    """scripts/fetch_aasist.py arg parsing, checksum verification, overwrite rules."""

    def test_download_via_file_url_and_verify_sha256(self) -> None:
        fetch = _load_script("fetch_aasist.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "AASIST.pth"
            payload = b"tiny fake checkpoint payload"
            source.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            dest_dir = root / "models"

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = fetch.main(["--url", source.as_uri(), "--dest", str(dest_dir), "--sha256", digest])

            self.assertEqual(rc, 0)
            downloaded = dest_dir / "aasist.pth"
            self.assertEqual(downloaded.read_bytes(), payload)
            self.assertIn(digest, out.getvalue())
            self.assertIn("license", out.getvalue().lower())

    def test_sha256_mismatch_fails_and_removes_file(self) -> None:
        fetch = _load_script("fetch_aasist.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "AASIST.pth"
            source.write_bytes(b"payload")
            dest_dir = root / "models"

            rc = fetch.main(["--url", source.as_uri(), "--dest", str(dest_dir), "--sha256", "0" * 64])

            self.assertEqual(rc, 1)
            self.assertFalse((dest_dir / "aasist.pth").exists())
            self.assertFalse((dest_dir / "aasist.pth.part").exists())

    def test_download_without_sha256_still_prints_digest(self) -> None:
        fetch = _load_script("fetch_aasist.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "ckpt.bin"
            source.write_bytes(b"weights")

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = fetch.main(["--url", source.as_uri(), "--dest", str(root / "models")])

            self.assertEqual(rc, 0)
            self.assertIn(hashlib.sha256(b"weights").hexdigest(), out.getvalue())
            self.assertIn("no --sha256", out.getvalue())

    def test_refuses_overwrite_without_force(self) -> None:
        fetch = _load_script("fetch_aasist.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "ckpt.bin"
            source.write_bytes(b"new")
            dest_dir = root / "models"
            dest_dir.mkdir()
            existing = dest_dir / "aasist.pth"
            existing.write_bytes(b"old")

            rc = fetch.main(["--url", source.as_uri(), "--dest", str(dest_dir)])
            self.assertEqual(rc, 2)
            self.assertEqual(existing.read_bytes(), b"old")

            rc = fetch.main(["--url", source.as_uri(), "--dest", str(dest_dir), "--force"])
            self.assertEqual(rc, 0)
            self.assertEqual(existing.read_bytes(), b"new")

    def test_drive_folder_url_prints_instructions_without_network(self) -> None:
        fetch = _load_script("fetch_aasist.py")
        with tempfile.TemporaryDirectory() as tmp:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = fetch.main(["--url", "https://drive.google.com/drive/folders/abc123", "--dest", tmp])

            self.assertEqual(rc, 2)
            self.assertIn("uc?id=<FILE_ID>", err.getvalue())
            self.assertFalse((Path(tmp) / "aasist.pth").exists())

    def test_url_classification(self) -> None:
        fetch = _load_script("fetch_aasist.py")
        self.assertEqual(
            fetch.classify_url("https://drive.google.com/drive/folders/AbC123"),
            ("folder", "AbC123"),
        )
        self.assertEqual(
            fetch.classify_url("https://drive.google.com/file/d/AbC123/view?usp=sharing"),
            ("file", "AbC123"),
        )
        self.assertEqual(
            fetch.classify_url("https://drive.google.com/uc?id=ZzZ789&export=download"),
            ("file", "ZzZ789"),
        )
        kind, _ = fetch.classify_url("https://raw.githubusercontent.com/clovaai/aasist/main/models/weights/AASIST.pth")
        self.assertEqual(kind, "direct")

    def test_sha256_file_helper(self) -> None:
        fetch = _load_script("fetch_aasist.py")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blob.bin"
            path.write_bytes(b"abc")
            self.assertEqual(fetch.sha256_file(path), hashlib.sha256(b"abc").hexdigest())


class DefaultAudioEngineDiscoveryTest(unittest.TestCase):
    """CLI auto-discovery of models/aasist-runtime.json for audio files."""

    def test_discovers_committed_profile(self) -> None:
        self.assertEqual(default_audio_model_path(), PROFILE_PATH)

    def test_returns_none_when_models_dir_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(default_audio_model_path(Path(tmp)))

    def test_scan_auto_discovers_audio_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_wav(root / "clip.wav")

            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["scan", str(root), "--format", "json"])

            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            item = payload["items"][0]
            self.assertEqual(item["kind"], "audio")
            self.assertIsNotNone(item["result"]["model_analysis"])
            self.assertIn("AASIST", item["result"]["model_analysis"]["model"])

    def test_scan_no_default_engine_skips_audio_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_wav(root / "clip.wav")

            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["scan", str(root), "--format", "json", "--no-default-engine"])

            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertIsNone(payload["items"][0]["result"]["model_analysis"])

    def test_audio_command_passes_default_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            _write_wav(wav)

            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["audio", str(wav), "--format", "json"])

            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertIn("model_analysis", payload)
            self.assertIsNotNone(payload["model_analysis"])
            self.assertIn("AASIST", payload["model_analysis"]["model"])

    def test_audio_command_no_default_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            _write_wav(wav)

            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                rc = cli_main(["audio", str(wav), "--format", "json", "--no-default-engine"])

            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertIsNone(payload["model_analysis"])


if __name__ == "__main__":
    unittest.main()

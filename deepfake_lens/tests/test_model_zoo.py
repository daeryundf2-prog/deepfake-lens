"""Model zoo: multi-profile aggregation, placeholder profiles, new runtimes.

Covers profile loading for the committed models/*.json zoo, directory and
profile-set expansion in analyze_external_model, per-model reporting with an
agreement signal, and graceful degradation when checkpoints are absent.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zlib
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from deepfake_lens.core import _model_analysis_from_json
from deepfake_lens.model_pins import empty_pin_for
from deepfake_lens.model_adapter import (
    AGREEMENT_SPREAD,
    PROFILE_SET_TYPE,
    TEXT_RUNTIMES,
    analyze_external_model,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_MODELS = Path(__file__).resolve().parents[1] / "models"
MODELS_DIR = PKG_MODELS
# G2/G33 (WP-C): the profiles that remain after the zoo cleanup.
EXPECTED_PROFILES = {
    "aide-runtime.json", "ai-image-swin-runtime.json", "community-forensics-vit-runtime.json",
    "community-forensics-frames-runtime.json", "sd-turbo-det-runtime.json", "sbi-effnet-runtime.json",
    "sbi-frames-runtime.json", "aasist-runtime.json", "wav2vec-deepfake-audio-runtime.json",
    "fakespot-detector-runtime.json",
}
REMOVED_PROFILES = (
    "korean-roberta-text-detector-runtime.json", "cnndetection-runtime.json", "univfd-runtime.json",
    "qwen-ppl-runtime.json", "binoculars-runtime.json", "openai-detector-runtime.json",
    "aide-frames-runtime.json", "umm-maybe-detector-runtime.json", "melodymachine-w2v2-runtime.json",
    "dire-runtime.json", "genconvit-face-runtime.json", "faceswap-ffpp-runtime.json",
    "faceswap-ffpp-frames-runtime.json", "face-manipulation-vit-runtime.json",
    "face-manipulation-vit-frames-runtime.json",
)
# A syntactically valid hub commit id for pinned temp profiles.
FAKE_REVISION = "0123456789abcdef0123456789abcdef01234567"
WIRED_RUNTIMES = {"onnx", "torchscript", "aide", "clip-linear", "torchvision", "aasist", "hf-text-classifier", "hf-image-classifier", "hf-audio-classifier", "video-frames", "causal-lm-ppl", "binoculars"}
# Runtimes that carry no checkpoint field of their own: hf-*-classifier
# names a hub model id, video-frames nests the checkpointed image profile.
CHECKPOINT_LESS_RUNTIMES = {"hf-text-classifier", "hf-image-classifier", "hf-audio-classifier", "video-frames"}
VIDEO_ONLY_RUNTIMES = {"video-frames"}


def _write_rgb_png(path: Path, width: int = 8, height: int = 8) -> None:
    rows = []
    for _y in range(height):
        row = bytearray([0])
        for _x in range(width):
            row.extend((200, 120, 40))
        rows.append(bytes(row))
    ihdr = _chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + ihdr + _chunk(b"IDAT", zlib.compress(b"".join(rows))) + _chunk(b"IEND", b""))


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return len(payload).to_bytes(4, "big") + kind + payload + b"\x00\x00\x00\x00"


def _score_map_profile(name: str, scores: dict[str, int]) -> dict[str, object]:
    return {"name": name, "score_map": scores}


class CommittedProfilesTest(unittest.TestCase):
    """models/*.json profiles must satisfy the adapter contract honestly."""

    def _profiles(self) -> dict[str, dict]:
        # Only *-runtime.json files are model profiles — thresholds.json and
        # other provenance manifests in the same directory must not be read
        # as adapter configs.
        return {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(MODELS_DIR.glob("*-runtime.json"))
        }

    def test_zoo_has_expected_profiles(self) -> None:
        # G2/G33 (WP-C): 15 rejected/placeholder profiles were removed; their
        # measurement notes live in docs/MODEL-REJECTIONS.md.
        names = set(self._profiles())
        self.assertEqual(names, EXPECTED_PROFILES)

    def test_removed_profiles_are_gone_and_documented(self) -> None:
        """G2/G33: deleted profiles stay deleted and keep a rejection record."""
        rejections = (REPO_ROOT / "docs" / "MODEL-REJECTIONS.md").read_text(encoding="utf-8")
        for name in REMOVED_PROFILES:
            with self.subTest(profile=name):
                self.assertFalse((MODELS_DIR / name).exists())
                self.assertIn(f"`{name}`", rejections)

    def test_generated_model_docs_match_profiles(self) -> None:
        """G9: models/README.md, NOTICE.md and the registry's profile block
        are generated from the profiles (scripts/sync_model_docs.py --check)."""
        spec = importlib.util.spec_from_file_location("sync_model_docs", REPO_ROOT / "scripts" / "sync_model_docs.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for path, expected in module.render().items():
            with self.subTest(path=path.name):
                self.assertEqual(path.read_text(encoding="utf-8"), expected, "run: python scripts/sync_model_docs.py")
        from deepfake_lens.model_registry import DETECTOR_REGISTRY

        keys = {candidate.key for candidate in DETECTOR_REGISTRY}
        for profile in self._profiles().values():
            self.assertIn(profile["candidate_key"], keys)

    def test_umm_maybe_weights_have_one_profile(self) -> None:
        """G33: the umm-maybe hub weights appear in exactly one profile."""
        owners = [name for name, profile in self._profiles().items() if profile.get("hub_model") == "umm-maybe/AI-image-detector"]
        self.assertEqual(owners, ["ai-image-swin-runtime.json"])

    def test_wired_profiles_use_implemented_runtimes(self) -> None:
        # G9 (WP-C): every profile is supported:false in phase 0, so the
        # contract is checked on all of them rather than skipping them.
        for name, profile in self._profiles().items():
            self.assertEqual(profile["type"], "deepfake-lens-runtime-profile-v1", name)
            self.assertIn(profile["runtime"], WIRED_RUNTIMES, name)
            # Hub-resolved runtimes name a model id instead of a local file;
            # video-frames nests the checkpointed image profile under "inner".
            if profile["runtime"] in {"hf-text-classifier", "hf-image-classifier", "hf-audio-classifier", "causal-lm-ppl", "binoculars"}:
                self.assertIn("hub_model", profile, name)
            elif profile["runtime"] == "video-frames":
                inner = profile.get("inner")
                self.assertIsInstance(inner, dict, name)
                self.assertIn(inner.get("runtime"), WIRED_RUNTIMES - VIDEO_ONLY_RUNTIMES, name)
                self.assertTrue("checkpoint" in inner or "hub_model" in inner, name)
            else:
                self.assertIn("checkpoint", profile, name)
            self.assertTrue(profile.get("limitations"), f"{name} must carry honest limitations")

    def test_every_profile_is_gated_and_carries_an_empty_pin(self) -> None:
        """G9: supported:false (measurement gate not met), a pin object of
        the right kind (sha256 for local weights, revision for hub models),
        and a measured_on slot for WP-I."""
        for name, profile in self._profiles().items():
            with self.subTest(profile=name):
                self.assertIs(profile["supported"], False)
                self.assertIn("측정 게이트", profile["reason"])
                self.assertIn("measured_on", profile)
                self.assertIsNone(profile["measured_on"])
                self.assertEqual(profile["pin"], empty_pin_for(profile))
                target = profile["inner"] if profile["runtime"] == "video-frames" else profile
                expected_key = "revision" if "hub_model" in target else "sha256"
                self.assertEqual(set(profile["pin"]), {expected_key})

    def test_gated_profile_degrades_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "img.png"
            _write_rgb_png(image)
            analysis = analyze_external_model(image, MODELS_DIR / "aide-runtime.json")
        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)
        self.assertEqual(analysis.confidence, "unavailable")
        self.assertIn("AIDE", analysis.model)
        self.assertIn("측정 게이트", analysis.detail)

    def test_placeholder_profile_degrades_with_reason(self) -> None:
        # Formerly exercised the committed dire-runtime.json placeholder,
        # which was removed in WP-C (G2); same contract on a temp profile.
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "img.png"
            _write_rgb_png(image)
            profile_path = Path(tmp) / "placeholder-runtime.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "type": "deepfake-lens-runtime-profile-v1",
                        "name": "Placeholder detector",
                        "runtime": "dire",
                        "supported": False,
                        "reason": "not wired: needs a reconstruction pipeline.",
                        "fetch": "Source: https://example.invalid/placeholder",
                        "limitations": ["This profile never produces scores."],
                    }
                ),
                encoding="utf-8",
            )
            analysis = analyze_external_model(image, profile_path)
        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)
        self.assertIn("Placeholder", analysis.model)
        self.assertIn("not wired", analysis.detail.lower())
        self.assertTrue(any("example.invalid/placeholder" in item for item in analysis.limitations))

    def test_crop_faces_gates_off_faceless_image(self) -> None:
        """crop_faces profiles must skip face-free images before inference."""
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "img.png"
            _write_rgb_png(image)
            profile_path = Path(tmp) / "cf-runtime.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "type": "deepfake-lens-runtime-profile-v1",
                        "name": "crop-gate-test",
                        "runtime": "hf-image-classifier",
                        "hub_model": "unused/gated-before-load",
                        "crop_faces": True,
                    }
                ),
                encoding="utf-8",
            )
            analysis = analyze_external_model(image, profile_path)
        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)
        self.assertIn("crop_faces", analysis.detail)

    def test_causal_lm_ppl_degrades_on_empty_text(self) -> None:
        # Formerly used the committed qwen-ppl-runtime.json (removed, G2);
        # the runtime contract is kept on a pinned temp profile.
        self.assertIn("causal-lm-ppl", TEXT_RUNTIMES)
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.txt"
            empty.write_text("", encoding="utf-8")
            profile_path = Path(tmp) / "ppl-runtime.json"
            profile_path.write_text(
                json.dumps({"name": "ppl", "runtime": "causal-lm-ppl", "modality": "text", "hub_model": "org/lm", "pin": {"revision": FAKE_REVISION}}),
                encoding="utf-8",
            )
            analysis = analyze_external_model(empty, profile_path, modality="text")
        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)

    def test_binoculars_degrades_on_empty_text(self) -> None:
        # Formerly used the committed binoculars-runtime.json (removed, G2).
        self.assertIn("binoculars", TEXT_RUNTIMES)
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.txt"
            empty.write_text("", encoding="utf-8")
            profile_path = Path(tmp) / "bino-runtime.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "name": "bino",
                        "runtime": "binoculars",
                        "modality": "text",
                        "hub_model": "org/performer",
                        "observer_model": "org/observer",
                        "pin": {"revision": FAKE_REVISION, "observer_revision": FAKE_REVISION},
                    }
                ),
                encoding="utf-8",
            )
            analysis = analyze_external_model(empty, profile_path, modality="text")
        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)

    def test_binoculars_requires_both_revisions(self) -> None:
        """G10: the observer LM is a second hub load and needs its own pin."""
        with tempfile.TemporaryDirectory() as tmp:
            text = Path(tmp) / "t.txt"
            text.write_text("some text", encoding="utf-8")
            profile_path = Path(tmp) / "bino-runtime.json"
            profile_path.write_text(
                json.dumps({"name": "bino", "runtime": "binoculars", "modality": "text", "hub_model": "a/b", "observer_model": "c/d", "pin": {"revision": FAKE_REVISION}}),
                encoding="utf-8",
            )
            analysis = analyze_external_model(text, profile_path, modality="text")
        self.assertEqual(analysis.confidence, "failed")
        self.assertTrue(analysis.detail.startswith("미고정 프로필"), analysis.detail)
        self.assertIn("observer_revision", analysis.detail)


class MultiProfileAggregationTest(unittest.TestCase):
    """analyze_external_model with >1 profile must report per-model + agreement."""

    def _make_dir(self, root: Path, profiles: dict[str, dict]) -> Path:
        zoo = root / "zoo"
        zoo.mkdir()
        for filename, profile in profiles.items():
            (zoo / filename).write_text(json.dumps(profile), encoding="utf-8")
        return zoo

    def test_directory_of_score_maps_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "img.png"
            _write_rgb_png(image)
            zoo = self._make_dir(
                root,
                {
                    "a-runtime.json": _score_map_profile("model-a", {"img.png": 80}),
                    "b-runtime.json": _score_map_profile("model-b", {"img.png": 90}),
                },
            )
            analysis = analyze_external_model(image, zoo)

        self.assertTrue(analysis.available)
        self.assertEqual(analysis.score, 85)
        self.assertEqual(len(analysis.models), 2)
        self.assertEqual({m["model"] for m in analysis.models}, {"model-a", "model-b"})
        self.assertTrue(all(m["available"] for m in analysis.models))
        self.assertIn("agreement: high", analysis.detail)
        self.assertTrue(any("prioritization signal" in item for item in analysis.limitations))

    def test_disagreement_drops_confidence_and_flags_limitation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "img.png"
            _write_rgb_png(image)
            low, high = 90 - AGREEMENT_SPREAD - 10, 90
            zoo = self._make_dir(
                root,
                {
                    "a-runtime.json": _score_map_profile("model-a", {"img.png": low}),
                    "b-runtime.json": _score_map_profile("model-b", {"img.png": high}),
                },
            )
            analysis = analyze_external_model(image, zoo)

        self.assertTrue(analysis.available)
        self.assertEqual(analysis.confidence, "low")
        self.assertIn("agreement: low", analysis.detail)
        self.assertTrue(any("disagree" in item for item in analysis.limitations))

    def test_partial_availability_scores_only_available_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "img.png"
            _write_rgb_png(image)
            zoo = self._make_dir(
                root,
                {
                    "a-runtime.json": _score_map_profile("model-a", {"img.png": 72}),
                    # wired runtime profile whose checkpoint does not exist
                    "b-runtime.json": {"name": "model-b", "runtime": "onnx", "checkpoint": "missing.onnx"},
                },
            )
            analysis = analyze_external_model(image, zoo)

        self.assertTrue(analysis.available)
        self.assertEqual(analysis.score, 72)
        self.assertIn("1/2 model profiles", analysis.detail)
        members = {m["model"]: m for m in analysis.models}
        self.assertFalse(members["model-b"]["available"])
        self.assertIn("checkpoint was not found", members["model-b"]["detail"])

    def test_all_members_missing_degrades_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "img.png"
            _write_rgb_png(image)
            zoo = self._make_dir(
                root,
                {
                    "a-runtime.json": {"name": "model-a", "runtime": "torchscript", "checkpoint": "missing.pt"},
                    "b-runtime.json": {"name": "model-b", "runtime": "onnx", "checkpoint": "missing.onnx"},
                },
            )
            analysis = analyze_external_model(image, zoo)

        self.assertFalse(analysis.available)
        self.assertEqual(analysis.score, 0)
        self.assertEqual(analysis.confidence, "unavailable")
        self.assertIn("0/2 model profiles", analysis.detail)
        self.assertEqual(len(analysis.models), 2)

    def test_onnx_audio_missing_checkpoint_reports_unavailable(self) -> None:
        """The onnx-audio runtime must degrade to 'unavailable' (not score 0)
        when its checkpoint is absent — same contract as image ONNX members."""
        import struct
        import wave

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "clip.wav"
            with wave.open(str(wav), "wb") as fh:
                fh.setnchannels(1)
                fh.setsampwidth(2)
                fh.setframerate(16000)
                fh.writeframes(b"".join(struct.pack("<h", 0) for _ in range(1600)))
            zoo = self._make_dir(
                root,
                {
                    "a-runtime.json": {
                        "name": "audio-a",
                        "runtime": "onnx-audio",
                        "checkpoint": "missing.onnx",
                    },
                },
            )
            analysis = analyze_external_model(wav, zoo, modality="audio")

        self.assertFalse(analysis.available)
        self.assertEqual(analysis.score, 0)
        self.assertEqual(analysis.confidence, "unavailable")
        self.assertIn("checkpoint", analysis.detail)

    def test_profile_set_resolves_members_relative_to_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "img.png"
            _write_rgb_png(image)
            members = root / "members"
            members.mkdir()
            (members / "a-runtime.json").write_text(json.dumps(_score_map_profile("model-a", {"img.png": 60})), encoding="utf-8")
            (members / "b-runtime.json").write_text(json.dumps(_score_map_profile("model-b", {"img.png": 70})), encoding="utf-8")
            profile_set = root / "zoo.json"
            profile_set.write_text(
                json.dumps({"type": PROFILE_SET_TYPE, "name": "zoo-set", "profiles": ["members/a-runtime.json", "members/b-runtime.json"]}),
                encoding="utf-8",
            )
            analysis = analyze_external_model(image, profile_set)

        self.assertTrue(analysis.available)
        self.assertEqual(analysis.score, 65)
        self.assertEqual(analysis.model, "zoo-set")
        self.assertEqual(len(analysis.models), 2)

    def test_list_of_paths_and_sidecar_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "img.png"
            _write_rgb_png(image)
            a = root / "a-runtime.json"
            b = root / "b-runtime.json"
            a.write_text(json.dumps(_score_map_profile("model-a", {"img.png": 50})), encoding="utf-8")
            b.write_text(json.dumps(_score_map_profile("model-b", {"img.png": 70})), encoding="utf-8")
            # *.model.json files are per-file score sidecars, not profiles.
            (root / "img.png.model.json").write_text(json.dumps({"score": 99}), encoding="utf-8")

            analysis = analyze_external_model(image, [a, b])
            self.assertTrue(analysis.available)
            self.assertEqual(len(analysis.models), 2)

            dir_analysis = analyze_external_model(image, root)
            self.assertEqual(len(dir_analysis.models), 2)

    def test_bare_checkpoint_relative_path_is_not_doubled(self) -> None:
        """A bare .onnx passed as a relative path must resolve once — a
        doubled 'dir/dir/file' checkpoint path was the regression."""
        analysis = analyze_external_model(Path("img.png"), Path("models/does-not-exist.onnx"), modality="image")
        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)
        self.assertNotIn("models/models", analysis.detail.replace("/", "\\"))
        self.assertNotIn("models\\models", analysis.detail)
        self.assertIn("does-not-exist.onnx", analysis.detail)

    @unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow not installed")
    def test_degraded_weight_applies_on_low_quality_jpeg(self) -> None:
        """A member with degraded_weight must lose influence on recompressed JPEGs."""
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hi = root / "img_hi.jpg"
            lo = root / "img_lo.jpg"
            Image.new("RGB", (128, 128), (200, 120, 40)).save(hi, quality=95)
            Image.new("RGB", (128, 128), (200, 120, 40)).save(lo, quality=40)
            a = root / "a-runtime.json"
            b = root / "b-runtime.json"
            fragile = {**_score_map_profile("fragile", {"img_hi.jpg": 80, "img_lo.jpg": 80}), "degraded_weight": 0.05}
            stable = _score_map_profile("stable", {"img_hi.jpg": 40, "img_lo.jpg": 40})
            a.write_text(json.dumps(fragile), encoding="utf-8")
            b.write_text(json.dumps(stable), encoding="utf-8")

            high_q = analyze_external_model(hi, [a, b])
            low_q = analyze_external_model(lo, [a, b])

        # q95: equal weights -> mean(80, 40) = 60
        self.assertEqual(high_q.score, 60)
        # q40: fragile down-weighted to 0.05 -> aggregate slides toward 40
        self.assertLessEqual(low_q.score, 45)
        self.assertTrue(any("down-weighted" in item for item in low_q.limitations))

    @unittest.skipUnless(importlib.util.find_spec("PIL") is not None, "Pillow not installed")
    def test_low_resolution_flagged_as_unreliable(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            small = root / "tiny.jpg"
            Image.new("RGB", (64, 64), (200, 120, 40)).save(small, quality=95)
            a = root / "a-runtime.json"
            b = root / "b-runtime.json"
            a.write_text(json.dumps(_score_map_profile("model-a", {"tiny.jpg": 80})), encoding="utf-8")
            b.write_text(json.dumps(_score_map_profile("model-b", {"tiny.jpg": 40})), encoding="utf-8")
            analysis = analyze_external_model(small, [a, b])
        self.assertTrue(any("below every member" in item for item in analysis.limitations))

    def test_empty_directory_is_graceful(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "img.png"
            _write_rgb_png(image)
            analysis = analyze_external_model(image, root)
        self.assertFalse(analysis.available)
        self.assertIn("no model profiles", analysis.detail)

    def test_committed_zoo_directory_runs_all_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "img.png"
            _write_rgb_png(image)
            analysis = analyze_external_model(image, MODELS_DIR)

        self.assertIsNotNone(analysis)
        # G2/G33 (WP-C): the image-modality members left after the cleanup
        # (aide, ai-image-swin, community-forensics-vit, sd-turbo-det,
        # sbi-effnet) — was 12 before the rejected profiles were removed.
        self.assertEqual(len(analysis.models), 5)
        names = {m["model"] for m in analysis.models}
        self.assertTrue(any("AIDE" in name for name in names))
        self.assertTrue(any("SBI" in name or "sbi" in name for name in names))
        # G9: every member is supported:false in phase 0 -> skipped with the
        # gate reason, never scored, whatever weights are on disk.
        self.assertFalse(analysis.available)
        self.assertEqual(analysis.score, 0)
        for member in analysis.models:
            self.assertFalse(member["available"], member)
            self.assertIn("측정 게이트", member["detail"])

    def test_aggregate_models_round_trip_through_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "img.png"
            _write_rgb_png(image)
            for name, score in (("model-a", 40), ("model-b", 60)):
                (root / f"{name}.json").write_text(json.dumps(_score_map_profile(name, {"img.png": score})), encoding="utf-8")
            analysis = analyze_external_model(image, root)

        restored = _model_analysis_from_json(asdict(analysis))
        self.assertEqual(restored.models, analysis.models)
        self.assertEqual(restored.score, analysis.score)


class VideoFramesRuntimeTest(unittest.TestCase):
    """The video-frames runtime samples frames with cv2 and scores each with
    the nested image profile — plumbing is verifiable without weights."""

    def _write_video(self, path: Path, frames: int = 12, size: tuple[int, int] = (64, 48)) -> None:
        import cv2
        import numpy as np

        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, size)
        self.assertTrue(writer.isOpened())
        for i in range(frames):
            frame = np.full((size[1], size[0], 3), (i * 7) % 255, dtype=np.uint8)
            writer.write(frame)
        writer.release()
        self.assertTrue(path.is_file())

    def _profile(self, inner: dict | None) -> dict[str, object]:
        return {
            "type": "deepfake-lens-runtime-profile-v1",
            "name": "test video-frames",
            "runtime": "video-frames",
            "modality": "video",
            "frames": 3,
            **({"inner": inner} if inner is not None else {}),
        }

    @unittest.skipUnless(importlib.util.find_spec("cv2") is not None, "opencv not installed")
    def test_frames_scored_through_inner_profile(self) -> None:
        """Every sampled frame goes through the inner runtime; a missing
        checkpoint degrades per frame and the aggregate reports it."""
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            self._write_video(video)
            profile_path = Path(tmp) / "vf-runtime.json"
            profile_path.write_text(
                json.dumps(self._profile({"runtime": "torchvision", "checkpoint": "missing.pth", "name": "inner-net"})),
                encoding="utf-8",
            )
            analysis = analyze_external_model(video, profile_path, modality="video")

        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)
        self.assertGreaterEqual(len(analysis.models), 1)
        self.assertTrue(all(not frame["available"] for frame in analysis.models))
        self.assertIn("no scores", analysis.detail)

    def test_missing_inner_profile_is_graceful_error(self) -> None:
        """A video-frames profile without 'inner' must degrade, not crash."""
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "vf-runtime.json"
            profile_path.write_text(json.dumps(self._profile(None)), encoding="utf-8")
            analysis = analyze_external_model(Path(tmp) / "clip.mp4", profile_path, modality="video")

        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)
        self.assertIn("inner", analysis.detail)

    def test_recursive_inner_runtime_rejected(self) -> None:
        """video-frames must not nest itself — that would recurse forever."""
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "vf-runtime.json"
            profile_path.write_text(
                json.dumps(self._profile({"runtime": "video-frames", "inner": {}})),
                encoding="utf-8",
            )
            analysis = analyze_external_model(Path(tmp) / "clip.mp4", profile_path, modality="video")

        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)
        self.assertIn("image runtime", analysis.detail)

    def test_inner_validation_precedes_optional_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile_path = Path(tmp) / "vf-runtime.json"
            for inner, detail in ((None, "inner"), ({"runtime": "video-frames"}, "image runtime")):
                with self.subTest(inner=inner):
                    profile_path.write_text(json.dumps(self._profile(inner)), encoding="utf-8")
                    with patch("deepfake_lens.model_adapter.importlib.import_module", side_effect=ImportError("cv2 unavailable")) as optional_import:
                        analysis = analyze_external_model(Path(tmp) / "clip.mp4", profile_path, modality="video")
                    self.assertIsNotNone(analysis)
                    self.assertFalse(analysis.available)
                    self.assertIn(detail, analysis.detail)
                    optional_import.assert_not_called()

            profile_path.write_text(json.dumps(self._profile({"runtime": "onnx", "checkpoint": "missing.onnx"})), encoding="utf-8")
            with patch("deepfake_lens.model_adapter.importlib.import_module", side_effect=ImportError("cv2 unavailable")) as optional_import:
                analysis = analyze_external_model(Path(tmp) / "clip.mp4", profile_path, modality="video")
            self.assertIsNotNone(analysis)
            self.assertFalse(analysis.available)
            self.assertIn("optional and not installed", analysis.detail)
            optional_import.assert_called_once_with("cv2")

    def test_video_profile_does_not_match_image_files(self) -> None:
        """A modality=video profile is filtered out for image scans."""
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "img.png"
            _write_rgb_png(image)
            profile_path = Path(tmp) / "vf-runtime.json"
            profile_path.write_text(json.dumps(self._profile({"runtime": "onnx", "checkpoint": "x.onnx"})), encoding="utf-8")
            analysis = analyze_external_model(image, profile_path, modality="image")

        self.assertIsNone(analysis)

    def test_committed_frames_profiles_match_video_modality(self) -> None:
        # aide-frames-runtime.json was removed (G2/WP-C); the remaining
        # video-frames profiles carry the same contract.
        for name, inner_runtime in (("community-forensics-frames-runtime.json", "onnx"), ("sbi-frames-runtime.json", "torchvision")):
            with self.subTest(profile=name):
                profile = json.loads((MODELS_DIR / name).read_text(encoding="utf-8"))
                self.assertEqual(profile["modality"], "video")
                self.assertEqual(profile["runtime"], "video-frames")
                self.assertEqual(profile["inner"]["runtime"], inner_runtime)
                # G9: the outer pin describes the inner checkpoint.
                self.assertEqual(profile["pin"], {"sha256": ""})


def _has_torchvision() -> bool:
    return importlib.util.find_spec("torch") is not None and importlib.util.find_spec("torchvision") is not None


def _write_decodable_png(path: Path, size: int = 32) -> None:
    """_write_rgb_png emits a zero-CRC IHDR that decoders reject; runtime
    tests need a real PNG."""
    from PIL import Image

    Image.new("RGB", (size, size), (200, 120, 40)).save(path)


class TorchvisionHeadTest(unittest.TestCase):
    """torchvision runtime must rewire both .fc (ResNet) and .classifier
    (EfficientNet) heads to the profile's num_classes."""

    def _write_profile(self, tmp: Path, arch: str, num_classes: int, checkpoint: Path) -> Path:
        import hashlib

        profile = {
            "type": "deepfake-lens-runtime-profile-v1",
            "name": f"{arch}-head-test",
            "runtime": "torchvision",
            "arch": arch,
            "num_classes": num_classes,
            "checkpoint": checkpoint.name,
            "input_size": 32,
            "score_activation": "softmax",
            # G9: weights load only against a matching pin.
            "pin": {"sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()},
        }
        path = tmp / f"{arch}-runtime.json"
        path.write_text(json.dumps(profile), encoding="utf-8")
        return path

    @unittest.skipUnless(_has_torchvision(), "torch/torchvision not installed")
    def test_efficientnet_classifier_head_rewired_and_loads(self) -> None:
        import torch
        from torchvision.models import efficientnet_b0

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            reference = efficientnet_b0(weights=None, num_classes=2)
            checkpoint = tmp / "eff.pth"
            torch.save(reference.state_dict(), checkpoint)
            profile_path = self._write_profile(tmp, "efficientnet_b0", 2, checkpoint)
            image = tmp / "img.png"
            _write_decodable_png(image)
            analysis = analyze_external_model(image, profile_path)

        self.assertIsNotNone(analysis)
        self.assertTrue(analysis.available)
        self.assertTrue(0 <= analysis.score <= 100)

    @unittest.skipUnless(_has_torchvision(), "torch/torchvision not installed")
    def test_resnet_fc_head_still_rewired(self) -> None:
        import torch
        from torchvision.models import resnet18

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            reference = resnet18(weights=None, num_classes=2)
            checkpoint = tmp / "res.pth"
            torch.save(reference.state_dict(), checkpoint)
            profile_path = self._write_profile(tmp, "resnet18", 2, checkpoint)
            image = tmp / "img.png"
            _write_decodable_png(image)
            analysis = analyze_external_model(image, profile_path)

        self.assertIsNotNone(analysis)
        self.assertTrue(analysis.available)


class ScoreBiasTest(unittest.TestCase):
    """score_bias subtracts calibration points after activation (SBI v2
    measured a ~16-point upward shift on real faces)."""

    def test_bias_subtracts_from_normalized_score(self) -> None:
        from deepfake_lens.model_adapter import _score_from_outputs

        base = _score_from_outputs([0.0, 4.0], {"score_index": 1})
        biased = _score_from_outputs([0.0, 4.0], {"score_index": 1, "score_bias": 35})
        self.assertEqual(biased, max(0, base - 35))

    def test_bias_clamps_at_zero(self) -> None:
        from deepfake_lens.model_adapter import _score_from_outputs

        score = _score_from_outputs([4.0, 0.0], {"score_index": 1, "score_bias": 35})
        self.assertEqual(score, 0)

    def test_no_bias_unchanged(self) -> None:
        from deepfake_lens.model_adapter import _score_from_outputs

        self.assertEqual(_score_from_outputs([0.0, 4.0], {"score_index": 1}),
                         _score_from_outputs([0.0, 4.0], {"score_index": 1, "score_bias": 0}))


if __name__ == "__main__":
    unittest.main()


class LanguageGateTest(unittest.TestCase):
    """English-only members must be excluded on Korean-dominant text."""

    def _profile(self, tmp: str, name: str, weight: float, langs: list | None) -> Path:
        import json

        profile = {"type": "deepfake-lens-runtime-profile-v1", "name": name, "ensemble_weight": weight}
        if langs is not None:
            profile["trained_languages"] = langs
        path = Path(tmp) / f"{name}.json"
        path.write_text(json.dumps(profile), encoding="utf-8")
        return path

    def test_english_member_downweighted_on_korean(self) -> None:
        import tempfile

        from deepfake_lens.model_adapter import _aggregate_profile_results
        from deepfake_lens.model_adapter import ExternalModelAnalysis

        with tempfile.TemporaryDirectory() as tmp:
            en = self._profile(tmp, "en-only", 1.0, ["en"])
            agnostic = self._profile(tmp, "multilingual", 1.0, None)
            results = [
                (en, ExternalModelAnalysis(True, 90, "high", "en-only", "", [])),
                (agnostic, ExternalModelAnalysis(True, 10, "high", "multilingual", "", [])),
            ]
            fused = _aggregate_profile_results(results, hangul_ratio=0.9)
        # en-only member is excluded, so score = multilingual member's 10
        self.assertEqual(fused.score, 10)
        self.assertTrue(any("excluded" in item for item in fused.limitations))

    def test_no_downweight_on_english_text(self) -> None:
        import tempfile

        from deepfake_lens.model_adapter import _aggregate_profile_results
        from deepfake_lens.model_adapter import ExternalModelAnalysis

        with tempfile.TemporaryDirectory() as tmp:
            en = self._profile(tmp, "en-only", 1.0, ["en"])
            agnostic = self._profile(tmp, "multilingual", 1.0, None)
            results = [
                (en, ExternalModelAnalysis(True, 90, "high", "en-only", "", [])),
                (agnostic, ExternalModelAnalysis(True, 10, "high", "multilingual", "", [])),
            ]
            fused = _aggregate_profile_results(results, hangul_ratio=0.0)
        self.assertEqual(fused.score, 50)
        self.assertFalse(any("excluded" in item for item in fused.limitations))

    def test_gated_member_takes_no_part_in_spread_or_agreement(self) -> None:
        """G33 regression: _aggregate_profile_results assigned ``scores``
        twice and the second (over every available member) overwrote the
        first, so an excluded English-only member still drove the spread."""
        import tempfile

        from deepfake_lens.model_adapter import ExternalModelAnalysis, _aggregate_profile_results

        with tempfile.TemporaryDirectory() as tmp:
            en = self._profile(tmp, "en-only", 1.0, ["en"])
            ko_a = self._profile(tmp, "ko-a", 1.0, ["ko"])
            ko_b = self._profile(tmp, "ko-b", 1.0, None)
            results = [
                (en, ExternalModelAnalysis(True, 98, "high", "en-only", "", [])),
                (ko_a, ExternalModelAnalysis(True, 10, "low", "ko-a", "", [])),
                (ko_b, ExternalModelAnalysis(True, 15, "low", "ko-b", "", [])),
            ]
            fused = _aggregate_profile_results(results, hangul_ratio=0.9)
        # Spread over the two contributing members is 5, not 98 - 10 = 88.
        self.assertIn("member spread=5", fused.detail)
        self.assertIn("agreement: high", fused.detail)
        self.assertFalse(any("disagree" in item for item in fused.limitations))
        self.assertIn("2/3 model profiles", fused.detail)
        self.assertEqual(fused.score, 12)
        # The gated member is reported as skipped, so its coverage entry is
        # "skipped", not "ran".
        members = {m["model"]: m for m in fused.models}
        self.assertFalse(members["en-only"]["available"])
        self.assertEqual(members["en-only"]["confidence"], "skipped")
        self.assertIn("언어 게이트", members["en-only"]["detail"])

    def test_gated_member_alone_does_not_hide_disagreement(self) -> None:
        """The reverse case: two contributing members that disagree must
        still read as disagreement whatever the gated member scored."""
        import tempfile

        from deepfake_lens.model_adapter import ExternalModelAnalysis, _aggregate_profile_results

        with tempfile.TemporaryDirectory() as tmp:
            en = self._profile(tmp, "en-only", 1.0, ["en"])
            ko_a = self._profile(tmp, "ko-a", 1.0, ["ko"])
            ko_b = self._profile(tmp, "ko-b", 1.0, None)
            results = [
                (en, ExternalModelAnalysis(True, 50, "medium", "en-only", "", [])),
                (ko_a, ExternalModelAnalysis(True, 10, "low", "ko-a", "", [])),
                (ko_b, ExternalModelAnalysis(True, 90, "high", "ko-b", "", [])),
            ]
            fused = _aggregate_profile_results(results, hangul_ratio=0.9)
        self.assertIn("member spread=80", fused.detail)
        self.assertEqual(fused.confidence, "low")

    def test_all_members_gated_is_not_available(self) -> None:
        """Every scoring member excluded -> no usable aggregate (not score 0 / available)."""
        import tempfile

        from deepfake_lens.model_adapter import ExternalModelAnalysis, _aggregate_profile_results

        with tempfile.TemporaryDirectory() as tmp:
            en_a = self._profile(tmp, "en-a", 1.0, ["en"])
            en_b = self._profile(tmp, "en-b", 1.0, ["en"])
            results = [
                (en_a, ExternalModelAnalysis(True, 90, "high", "en-a", "", [])),
                (en_b, ExternalModelAnalysis(True, 80, "high", "en-b", "", [])),
            ]
            fused = _aggregate_profile_results(results, hangul_ratio=0.9)
        self.assertFalse(fused.available)
        self.assertEqual(fused.confidence, "unavailable")
        self.assertIn("0/2 model profiles", fused.detail)


class ModelCacheLRUTest(unittest.TestCase):
    """_ModelLRU bounds resident model count and evicts least-recently-used."""

    def test_evicts_oldest_beyond_limit(self) -> None:
        from deepfake_lens.model_adapter import _ModelLRU

        cache = _ModelLRU(2)
        cache["a"], cache["b"], cache["c"] = 1, 2, 3
        self.assertNotIn("a", cache)
        self.assertEqual(list(cache), ["b", "c"])

    def test_get_refreshes_recency(self) -> None:
        from deepfake_lens.model_adapter import _ModelLRU

        cache = _ModelLRU(2)
        cache["a"], cache["b"] = 1, 2
        cache.get("a")
        cache["c"] = 3
        self.assertNotIn("b", cache)
        self.assertIn("a", cache)

    def test_env_configured_limit(self) -> None:
        import os
        from deepfake_lens.model_adapter import _model_cache_limit

        saved = os.environ.get("DEEPFAKE_LENS_MODEL_CACHE_MAX")
        try:
            os.environ["DEEPFAKE_LENS_MODEL_CACHE_MAX"] = "9"
            self.assertEqual(_model_cache_limit(), 9)
            os.environ["DEEPFAKE_LENS_MODEL_CACHE_MAX"] = "0"
            self.assertEqual(_model_cache_limit(), 1)
            os.environ["DEEPFAKE_LENS_MODEL_CACHE_MAX"] = "bogus"
            self.assertEqual(_model_cache_limit(), 4)
        finally:
            if saved is None:
                os.environ.pop("DEEPFAKE_LENS_MODEL_CACHE_MAX", None)
            else:
                os.environ["DEEPFAKE_LENS_MODEL_CACHE_MAX"] = saved

    def test_clear_all_model_caches_flushes_state(self) -> None:
        from deepfake_lens.model_adapter import (
            _AIDE_RUNNERS,
            _CLIP_HEADS,
            clear_all_model_caches,
        )

        _AIDE_RUNNERS["test_key"] = ("a", "b", "c")
        _CLIP_HEADS["test_head"] = ("x", "y")
        self.assertIn("test_key", _AIDE_RUNNERS)
        self.assertIn("test_head", _CLIP_HEADS)

        clear_all_model_caches()
        self.assertEqual(len(_AIDE_RUNNERS), 0)
        self.assertEqual(len(_CLIP_HEADS), 0)


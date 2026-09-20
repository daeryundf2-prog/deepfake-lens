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
from deepfake_lens.model_adapter import (
    AGREEMENT_SPREAD,
    PROFILE_SET_TYPE,
    TEXT_RUNTIMES,
    analyze_external_model,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "models"
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
        return {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(MODELS_DIR.glob("*.json"))
        }

    def test_zoo_has_expected_profiles(self) -> None:
        names = set(self._profiles())
        self.assertEqual(
            names,
            {"aide-runtime.json", "univfd-runtime.json", "cnndetection-runtime.json", "dire-runtime.json", "aasist-runtime.json", "openai-detector-runtime.json", "aide-frames-runtime.json", "fakespot-detector-runtime.json", "qwen-ppl-runtime.json", "binoculars-runtime.json", "faceswap-ffpp-runtime.json", "faceswap-ffpp-frames-runtime.json", "face-manipulation-vit-runtime.json", "face-manipulation-vit-frames-runtime.json", "wav2vec-deepfake-audio-runtime.json", "ai-image-swin-runtime.json", "sbi-effnet-runtime.json"},
        )

    def test_wired_profiles_use_implemented_runtimes(self) -> None:
        for name, profile in self._profiles().items():
            if profile.get("supported") is False:
                continue
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

    def test_dire_is_documented_placeholder(self) -> None:
        profile = self._profiles()["dire-runtime.json"]
        self.assertIs(profile["supported"], False)
        self.assertTrue(profile["reason"])
        self.assertIn("http", profile["fetch"])

    def test_placeholder_profile_degrades_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "img.png"
            _write_rgb_png(image)
            analysis = analyze_external_model(image, MODELS_DIR / "dire-runtime.json")
        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)
        self.assertIn("DIRE", analysis.model)
        self.assertIn("not wired", analysis.detail.lower())
        self.assertTrue(any("github.com/ZhendongWang6/DIRE" in item for item in analysis.limitations))

    def test_univfd_profile_records_clip_contract(self) -> None:
        profile = self._profiles()["univfd-runtime.json"]
        self.assertEqual(profile["runtime"], "clip-linear")
        self.assertEqual(profile["backbone"], "openai/clip-vit-large-patch14")
        self.assertEqual(profile["mean"], [0.48145466, 0.4578275, 0.40821073])
        self.assertEqual(profile["score_activation"], "sigmoid")

    def test_cnndetection_profile_records_torchvision_contract(self) -> None:
        profile = self._profiles()["cnndetection-runtime.json"]
        self.assertEqual(profile["runtime"], "torchvision")
        self.assertEqual(profile["arch"], "resnet50")
        self.assertEqual(profile["num_classes"], 1)
        self.assertEqual(profile["state_dict_prefix"], "model.")

    def test_face_vit_profile_records_hub_contract(self) -> None:
        profile = self._profiles()["face-manipulation-vit-runtime.json"]
        self.assertIs(profile["supported"], False)
        self.assertIn("rejected", profile["reason"])
        self.assertEqual(profile["runtime"], "hf-image-classifier")
        self.assertEqual(profile["modality"], "image")
        self.assertIn("hub_model", profile)
        self.assertEqual(profile["score_label"], "Fake")
        self.assertIs(profile["crop_faces"], True)
        self.assertEqual(profile["crop_aggregate"], "max")

    def test_rejected_ffpp_profiles_are_disabled(self) -> None:
        for name in ("faceswap-ffpp-runtime.json", "faceswap-ffpp-frames-runtime.json"):
            profile = self._profiles()[name]
            self.assertIs(profile["supported"], False, name)
            self.assertIn("rejected", profile["reason"], name)

    def test_rejected_ffpp_profile_degrades_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "img.png"
            _write_rgb_png(image)
            analysis = analyze_external_model(image, MODELS_DIR / "faceswap-ffpp-runtime.json")
        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)
        self.assertIn("rejected", analysis.detail.lower())

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

    def test_disabled_face_vit_profile_reports_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "img.png"
            _write_rgb_png(image)
            analysis = analyze_external_model(image, MODELS_DIR / "face-manipulation-vit-runtime.json")
        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)
        self.assertIn("rejected", analysis.detail.lower())

    def test_qwen_ppl_profile_records_ppl_contract(self) -> None:
        profile = self._profiles()["qwen-ppl-runtime.json"]
        self.assertEqual(profile["runtime"], "causal-lm-ppl")
        self.assertEqual(profile["modality"], "text")
        self.assertEqual(profile["hub_model"], "Qwen/Qwen2.5-0.5B")
        self.assertLess(profile["ppl_low"], profile["ppl_high"])
        self.assertIn("causal-lm-ppl", TEXT_RUNTIMES)

    def test_causal_lm_ppl_degrades_on_empty_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.txt"
            empty.write_text("", encoding="utf-8")
            analysis = analyze_external_model(empty, MODELS_DIR / "qwen-ppl-runtime.json", modality="text")
        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)

    def test_binoculars_profile_records_contract(self) -> None:
        profile = self._profiles()["binoculars-runtime.json"]
        self.assertEqual(profile["runtime"], "binoculars")
        self.assertEqual(profile["modality"], "text")
        self.assertIn("observer_model", profile)
        self.assertLess(profile["ratio_low"], profile["ratio_high"])
        self.assertIn("binoculars", TEXT_RUNTIMES)

    def test_binoculars_degrades_on_empty_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.txt"
            empty.write_text("", encoding="utf-8")
            analysis = analyze_external_model(empty, MODELS_DIR / "binoculars-runtime.json", modality="text")
        self.assertIsNotNone(analysis)
        self.assertFalse(analysis.available)


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
        self.assertEqual(len(analysis.models), 8)
        names = {m["model"] for m in analysis.models}
        self.assertTrue(any("AIDE" in name for name in names))
        self.assertTrue(any("DIRE" in name for name in names))
        # requires_face members must appear as gated (unavailable) on the
        # faceless probe image rather than crashing or scoring.
        self.assertTrue(any("dima806" in name or "deepfake-vs-real" in name for name in names))
        # crop_faces members must likewise gate on the faceless probe image.
        self.assertTrue(any("SBI" in name or "sbi" in name for name in names))
        # Without downloaded checkpoints every member must degrade cleanly.
        if not any(MODELS_DIR.glob(pattern) for pattern in ("*.pth", "*.pt", "*.onnx")):
            self.assertFalse(analysis.available)
            self.assertEqual(analysis.score, 0)

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

    def test_committed_aide_frames_profile_matches_video_modality(self) -> None:
        profile = json.loads((MODELS_DIR / "aide-frames-runtime.json").read_text(encoding="utf-8"))
        self.assertEqual(profile["modality"], "video")
        self.assertEqual(profile["runtime"], "video-frames")
        self.assertEqual(profile["inner"]["runtime"], "aide")


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
        profile = {
            "type": "deepfake-lens-runtime-profile-v1",
            "name": f"{arch}-head-test",
            "runtime": "torchvision",
            "arch": arch,
            "num_classes": num_classes,
            "checkpoint": checkpoint.name,
            "input_size": 32,
            "score_activation": "softmax",
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

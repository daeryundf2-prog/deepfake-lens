"""Model zoo: multi-profile aggregation, placeholder profiles, new runtimes.

Covers profile loading for the committed models/*.json zoo, directory and
profile-set expansion in analyze_external_model, per-model reporting with an
agreement signal, and graceful degradation when checkpoints are absent.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import zlib
from dataclasses import asdict
from pathlib import Path

from deepfake_lens.core import _model_analysis_from_json
from deepfake_lens.model_adapter import (
    AGREEMENT_SPREAD,
    PROFILE_SET_TYPE,
    analyze_external_model,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "models"
WIRED_RUNTIMES = {"onnx", "torchscript", "aide", "clip-linear", "torchvision"}


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
            {"aide-runtime.json", "univfd-runtime.json", "cnndetection-runtime.json", "dire-runtime.json"},
        )

    def test_wired_profiles_use_implemented_runtimes(self) -> None:
        for name, profile in self._profiles().items():
            if profile.get("supported") is False:
                continue
            self.assertEqual(profile["type"], "deepfake-lens-runtime-profile-v1", name)
            self.assertIn(profile["runtime"], WIRED_RUNTIMES, name)
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
        self.assertEqual(len(analysis.models), 4)
        names = {m["model"] for m in analysis.models}
        self.assertTrue(any("AIDE" in name for name in names))
        self.assertTrue(any("DIRE" in name for name in names))
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


if __name__ == "__main__":
    unittest.main()

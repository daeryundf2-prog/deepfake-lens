"""R15-3 (round 15): no model asset is loaded without a sha256 pin — ``models/assets.json``.

The verifier found three loaders that took any file: the MediaPipe
FaceLandmarker ``.task`` (``DEEPFAKE_LENS_FACE_LANDMARKER`` or the models
folder; handed to ``create_from_options``, a failure swallowed), the SyncNet
weights (third-party ``torch.load``) and the Haar cascade
(``DEEPFAKE_LENS_HAAR_CASCADE``). Each asset is now registered with its
expected sha256; an unregistered, unpinned or different file is refused
("미고정 모델: <자산>", the check is ``failed``), and the loader gets the
verified bytes (a buffer, or a private copy), not the original path.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any, Iterator
from unittest import mock

from deepfake_lens import model_assets
from deepfake_lens.checks import run_check
from deepfake_lens.model_assets import (
    FACE_LANDMARKER,
    HAAR_FRONTALFACE,
    PACKAGED_MANIFEST,
    S3FD_WEIGHTS,
    SYNCNET_WEIGHTS,
    AssetPinError,
)
from deepfake_lens.result_types import CoverageStatus

PACKAGE = Path(__file__).resolve().parents[1]
BUNDLED_HAAR = PACKAGE / "models" / HAAR_FRONTALFACE
HAVE_CV2 = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@contextlib.contextmanager
def _models_dir(pins: dict[str, str]) -> Iterator[Path]:
    """A models directory whose own assets.json pins ``pins`` (the packaged manifest with those sha256 set)."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp).resolve()
        payload = json.loads(PACKAGED_MANIFEST.read_text(encoding="utf-8"))
        for entry in payload["assets"]:
            if entry["name"] in pins:
                entry["sha256"] = pins[entry["name"]]
        (folder / "assets.json").write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_MODELS_DIR": str(folder)}):
            yield folder


class ManifestTest(unittest.TestCase):
    def test_every_non_profile_asset_is_registered_with_source_and_license(self) -> None:
        payload = json.loads(PACKAGED_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], model_assets.ASSET_MANIFEST_SCHEMA)
        names = [entry["name"] for entry in payload["assets"]]
        self.assertEqual(sorted(names), sorted([HAAR_FRONTALFACE, FACE_LANDMARKER, SYNCNET_WEIGHTS, S3FD_WEIGHTS]))
        for entry in payload["assets"]:
            with self.subTest(asset=entry["name"]):
                self.assertTrue(entry["source"] and entry["license"] and entry["used_by"])
                self.assertIn("sha256", entry)
                self.assertTrue(entry["sha256"] == "" or model_assets._is_sha256(entry["sha256"]))

    def test_the_bundled_haar_cascade_is_pinned_to_its_bytes(self) -> None:
        self.assertEqual(model_assets.expected_sha256(HAAR_FRONTALFACE, model_assets.load_manifest(PACKAGED_MANIFEST)), _sha(BUNDLED_HAAR.read_bytes()))

    def test_downloadable_assets_ship_unpinned_and_are_refused(self) -> None:
        packaged = model_assets.load_manifest(PACKAGED_MANIFEST)
        for asset in (FACE_LANDMARKER, SYNCNET_WEIGHTS, S3FD_WEIGHTS):
            with self.subTest(asset=asset):
                self.assertIsNone(model_assets.expected_sha256(asset, packaged))
                with self.assertRaises(AssetPinError) as caught:
                    model_assets.check_bytes(asset, b"anything", packaged)
                self.assertEqual(str(caught.exception), f"미고정 모델: {asset}")

    def test_unregistered_mismatched_and_unreadable_manifests_are_refused(self) -> None:
        packaged = model_assets.load_manifest(PACKAGED_MANIFEST)
        with self.assertRaises(AssetPinError):
            model_assets.check_bytes("other.task", b"x", packaged)  # not registered
        with self.assertRaises(AssetPinError) as caught:
            model_assets.check_bytes(HAAR_FRONTALFACE, BUNDLED_HAAR.read_bytes() + b" ", packaged)
        self.assertIn("sha256 불일치", str(caught.exception))
        model_assets.check_bytes(HAAR_FRONTALFACE, BUNDLED_HAAR.read_bytes(), packaged)  # the pinned bytes pass
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "assets.json"
            broken.write_text("{not json", encoding="utf-8")
            self.assertEqual(model_assets.load_manifest(broken), {})  # pins nothing: every asset refused
            broken.write_text(json.dumps({"schema": "other", "assets": [{"name": HAAR_FRONTALFACE, "sha256": _sha(b"")}]}), encoding="utf-8")
            self.assertEqual(model_assets.load_manifest(broken), {})

    def test_a_models_dir_manifest_governs_that_dir(self) -> None:
        with _models_dir({FACE_LANDMARKER: _sha(b"landmarker")}) as folder:
            self.assertEqual(model_assets.manifest_path(), folder / "assets.json")
            model_assets.check_bytes(FACE_LANDMARKER, b"landmarker")
        self.assertEqual(model_assets.manifest_path(Path(tempfile.gettempdir()) / "no-such-models-dir"), PACKAGED_MANIFEST)

    def test_a_verified_copy_holds_exactly_the_pinned_bytes(self) -> None:
        copy = model_assets.verified_copy(HAAR_FRONTALFACE, BUNDLED_HAAR)
        try:
            self.assertEqual(Path(copy).read_bytes(), BUNDLED_HAAR.read_bytes())
            self.assertNotEqual(os.path.realpath(copy), os.path.realpath(BUNDLED_HAAR))
        finally:
            Path(copy).unlink()
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / HAAR_FRONTALFACE
            other.write_bytes(BUNDLED_HAAR.read_bytes().replace(b"<stages>", b"<stages> ", 1))
            before = set(os.listdir(model_assets.scratch_dir()))
            with self.assertRaises(AssetPinError):
                model_assets.verified_copy(HAAR_FRONTALFACE, other)
            self.assertEqual(set(os.listdir(model_assets.scratch_dir())), before)  # the refused copy is gone


class _Cascade:
    """Stands in for ``cv2.CascadeClassifier`` (OpenCV 5 wheels have none); records the bytes it was given."""

    opened: list[tuple[str, bytes]] = []
    faces: list[tuple[int, int, int, int]] = []

    def __init__(self, path: str = "") -> None:
        data = Path(path).read_bytes() if os.path.isfile(path) else b""
        type(self).opened.append((path, data))
        self._empty = not data

    def empty(self) -> bool:
        return self._empty

    def detectMultiScale(self, *args: Any) -> list:  # noqa: N802 - OpenCV's name
        return list(type(self).faces)


@unittest.skipUnless(HAVE_CV2, "opencv not installed")
class HaarCascadePinTest(unittest.TestCase):
    def setUp(self) -> None:
        import cv2

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        _Cascade.opened = []
        _Cascade.faces = []
        patcher = mock.patch.object(cv2, "CascadeClassifier", _Cascade, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_override_with_other_bytes_is_refused_and_nothing_loads_in_its_place(self) -> None:
        from deepfake_lens import face

        other = self.root / "my_cascade.xml"
        other.write_bytes(BUNDLED_HAAR.read_bytes() + b"\n<!-- edited -->\n")
        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_HAAR_CASCADE": str(other)}):
            with mock.patch.object(face, "haar_cascade_candidates", return_value=[str(other)]):
                with self.assertRaises(AssetPinError) as caught:
                    face.load_face_cascade()
                self.assertIn("미고정 모델: 재정의 cascade sha256 불일치", str(caught.exception))
                self.assertEqual(_Cascade.opened, [])  # never handed to OpenCV
            # R16-2 (round 16): the real candidate list holds only the override —
            # this test used to assert that the bundled copy then loaded silently
            # (the defect: the operator's override was ignored, docs said failed).
            with self.assertRaises(face.OverrideCascadePinError):
                face.load_face_cascade()
            self.assertEqual(_Cascade.opened, [])
        cascade = face.load_face_cascade()  # no override: the bundled copy
        self.assertFalse(cascade.empty())
        self.assertEqual(len(_Cascade.opened), 1)
        opened, data = _Cascade.opened[0]
        self.assertEqual(data, BUNDLED_HAAR.read_bytes())  # exactly the pinned bytes …
        self.assertNotEqual(os.path.realpath(opened), os.path.realpath(BUNDLED_HAAR))  # … from a private copy
        self.assertFalse(os.path.exists(opened))  # removed after loading

    def test_face_detection_with_only_an_unpinned_cascade_is_an_error_not_no_face(self) -> None:
        import numpy as np

        from deepfake_lens import face

        other = self.root / HAAR_FRONTALFACE
        other.write_bytes(b"<opencv_storage></opencv_storage>")
        blank: Any = np.full((160, 160, 3), 128, np.uint8)
        with mock.patch.object(face, "haar_cascade_candidates", return_value=[str(other)]), mock.patch.object(
            face, "_mediapipe_detect_faces", side_effect=face.FaceDetectorUnavailable("mediapipe 없음")
        ):
            with self.assertRaises(face.FaceDetectionError) as caught:
                face._detect_faces_strict(blank)
        self.assertIn("AssetPinError", str(caught.exception))
        self.assertIn(f"미고정 모델: {HAAR_FRONTALFACE}", str(caught.exception))


@unittest.skipUnless(HAVE_CV2, "opencv not installed")
class FaceLandmarkerPinTest(unittest.TestCase):
    def setUp(self) -> None:
        from deepfake_lens import face

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.asset = self.root / FACE_LANDMARKER
        self.asset.write_bytes(b"fake face landmarker bundle")
        env = mock.patch.dict(os.environ, {"DEEPFAKE_LENS_FACE_LANDMARKER": str(self.asset)})
        env.start()
        self.addCleanup(env.stop)
        cache: Any = mock.patch.object(face, "_VERIFIED_LANDMARKER", {})
        cache.start()
        self.addCleanup(cache.stop)

    def _fake_mediapipe(self) -> tuple[dict[str, Any], dict[str, types.ModuleType]]:
        seen: dict[str, Any] = {}

        class BaseOptions:
            def __init__(self, **kwargs: Any) -> None:
                seen["base_options"] = kwargs

        class _Landmarker:
            def __enter__(self) -> "_Landmarker":
                return self

            def __exit__(self, *exc: Any) -> None:
                return None

            def detect(self, image: Any) -> Any:
                return types.SimpleNamespace(face_landmarks=[])

        vision = types.ModuleType("mediapipe.tasks.python.vision")
        vision.FaceLandmarkerOptions = lambda **kwargs: kwargs  # type: ignore[attr-defined]
        vision.RunningMode = types.SimpleNamespace(IMAGE="IMAGE")  # type: ignore[attr-defined]
        vision.FaceLandmarker = types.SimpleNamespace(create_from_options=lambda options: _Landmarker())  # type: ignore[attr-defined]
        python = types.ModuleType("mediapipe.tasks.python")
        python.BaseOptions = BaseOptions  # type: ignore[attr-defined]
        python.vision = vision  # type: ignore[attr-defined]
        tasks = types.ModuleType("mediapipe.tasks")
        tasks.python = python  # type: ignore[attr-defined]
        mp = types.ModuleType("mediapipe")
        mp.tasks = tasks  # type: ignore[attr-defined]
        mp.Image = lambda **kwargs: kwargs  # type: ignore[attr-defined]
        mp.ImageFormat = types.SimpleNamespace(SRGB="SRGB")  # type: ignore[attr-defined]
        modules = {"mediapipe": mp, "mediapipe.tasks": tasks, "mediapipe.tasks.python": python, "mediapipe.tasks.python.vision": vision}
        return seen, modules

    def test_an_unpinned_task_file_is_refused_before_mediapipe_sees_it(self) -> None:
        import numpy as np

        from deepfake_lens import face

        seen, modules = self._fake_mediapipe()
        image: Any = np.zeros((64, 64, 3), np.uint8)
        with mock.patch.dict(sys.modules, modules):
            with self.assertRaises(AssetPinError) as caught:
                face._facelandmarker_landmarks(image, 0, 0, 32, 32)
        self.assertEqual(str(caught.exception), f"미고정 모델: {FACE_LANDMARKER}")
        self.assertEqual(seen, {})  # create_from_options never ran

    def test_a_pinned_task_is_handed_over_as_the_verified_buffer(self) -> None:
        import numpy as np

        from deepfake_lens import face

        seen, modules = self._fake_mediapipe()
        image: Any = np.zeros((64, 64, 3), np.uint8)
        with _models_dir({FACE_LANDMARKER: _sha(self.asset.read_bytes())}), mock.patch.dict(sys.modules, modules):
            self.assertIsNone(face._facelandmarker_landmarks(image, 0, 0, 32, 32))  # ran; no face in the fake result
            self.assertEqual(seen["base_options"], {"model_asset_buffer": self.asset.read_bytes()})  # no path at all
            self.asset.write_bytes(b"another bundle, same name")
            with self.assertRaises(AssetPinError) as caught:
                face._facelandmarker_landmarks(image, 0, 0, 32, 32)
        self.assertIn("sha256 불일치", str(caught.exception))

    def test_the_face_check_is_failed_with_the_reason(self) -> None:
        import cv2
        import numpy as np

        from deepfake_lens import face
        from deepfake_lens.native_path import imwrite_any

        photo = self.root / "photo.png"
        rng = np.random.default_rng(3)
        self.assertTrue(imwrite_any(photo, rng.integers(0, 255, (128, 128, 3), dtype=np.uint8)))
        _Cascade.opened = []
        _Cascade.faces = [(10, 10, 60, 60)]  # Haar "finds" a face: its landmarks are measured next
        with mock.patch.object(cv2, "CascadeClassifier", _Cascade, create=True):
            value, entry = run_check("face_manipulation", lambda: face.analyze_faces(photo))
        self.assertIsNone(value)
        self.assertEqual(entry.status, CoverageStatus.FAILED)
        self.assertEqual(entry.reason, f"AssetPinError: 미고정 모델: {FACE_LANDMARKER}")

    def test_a_repin_and_a_removed_pin_take_effect_in_the_same_process(self) -> None:
        """R16-3 (round 16): the verified-bytes cache followed the file's stat only — a long-running server
        kept serving bytes a re-pin or a removed pin no longer allows. The key now holds the current pin."""
        from deepfake_lens import face

        data = self.asset.read_bytes()
        with _models_dir({FACE_LANDMARKER: _sha(data)}) as folder:
            self.assertEqual(face._verified_facelandmarker(self.asset), data)
            _set_pin(folder, FACE_LANDMARKER, _sha(b"another trusted copy"))  # re-pinned to other bytes
            with self.assertRaises(AssetPinError) as caught:
                face._verified_facelandmarker(self.asset)
            self.assertIn("sha256 불일치", str(caught.exception))
            _set_pin(folder, FACE_LANDMARKER, "")  # pin removed
            with self.assertRaises(AssetPinError) as caught:
                face._verified_facelandmarker(self.asset)
            self.assertEqual(str(caught.exception), f"미고정 모델: {FACE_LANDMARKER}")
            (folder / "assets.json").write_text("{broken", encoding="utf-8")  # manifest unreadable: pins nothing
            with self.assertRaises(AssetPinError):
                face._verified_facelandmarker(self.asset)
        with _models_dir({FACE_LANDMARKER: _sha(data)}):
            self.assertEqual(face._verified_facelandmarker(self.asset), data)  # pinned again: served


class SyncNetPinTest(unittest.TestCase):
    def setUp(self) -> None:
        from deepfake_lens import lipsync

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        for patch in (
            mock.patch.object(lipsync, "_SYNCNET_PIPELINE", None),
            mock.patch.object(lipsync, "_SYNCNET_FAILED", False),
            mock.patch.object(lipsync, "_SYNCNET_KEY", None),  # R16-3
            mock.patch.object(lipsync, "_SYNCNET_BROKEN_KEY", None),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.built: list[dict[str, Any]] = []
        self.loads: list[dict[str, Any]] = []
        built, loads = self.built, self.loads

        def real_load(path: str, **kwargs: Any) -> dict[str, Any]:
            loads.append({"path": path, "bytes": Path(path).read_bytes(), **kwargs})
            return {}

        torch = types.ModuleType("torch")
        torch.load = real_load  # type: ignore[attr-defined]

        class SyncNetPipeline:
            def __init__(self, config: dict[str, str], device: str = "cpu") -> None:
                import torch as loaded_torch

                built.append(dict(config))
                for key in ("s3fd_weights", "syncnet_weights"):
                    loaded_torch.load(config[key], weights_only=False)  # a third party asking for full unpickling

            def inference(self, path: str) -> tuple:
                return [0], [5.0], [], 5.0, 1.0, {}, True

        pipeline = types.ModuleType("syncnet_python.syncnet_pipeline")
        pipeline.SyncNetPipeline = SyncNetPipeline  # type: ignore[attr-defined]
        package = types.ModuleType("syncnet_python")
        package.syncnet_pipeline = pipeline  # type: ignore[attr-defined]
        modules = mock.patch.dict(sys.modules, {"torch": torch, "syncnet_python": package, "syncnet_python.syncnet_pipeline": pipeline})
        modules.start()
        self.addCleanup(modules.stop)
        self.weights = {S3FD_WEIGHTS: b"s3fd weights", SYNCNET_WEIGHTS: b"syncnet weights"}

    def _provision(self, folder: Path) -> None:
        for name, data in self.weights.items():
            (folder / name).write_bytes(data)

    def test_unpinned_weights_are_refused_every_time(self) -> None:
        from deepfake_lens import lipsync

        with _models_dir({}) as folder:
            self._provision(folder)
            for _ in range(2):  # never latched: every video records the refusal
                with self.assertRaises(AssetPinError) as caught:
                    lipsync._syncnet_analysis(self.root / "clip.mp4")
                self.assertIn("미고정 모델: sfd_face.pth", str(caught.exception))
            value, entry = run_check("lipsync", lambda: lipsync.analyze_lipsync(self.root / "clip.mp4"))
        self.assertEqual((entry.status, entry.reason), (CoverageStatus.FAILED, f"AssetPinError: 미고정 모델: {S3FD_WEIGHTS}"))
        self.assertEqual(self.built, [])  # the third-party loader never saw them

    def test_pinned_weights_load_from_private_copies_with_weights_only_forced(self) -> None:
        from deepfake_lens import lipsync

        pins = {name: _sha(data) for name, data in self.weights.items()}
        with _models_dir(pins) as folder:
            self._provision(folder)
            with contextlib.redirect_stderr(io.StringIO()):
                analysis = lipsync._syncnet_analysis(self.root / "clip.mp4")
        self.assertIsNotNone(analysis)
        self.assertEqual(len(self.built), 1)
        self.assertEqual([load["bytes"] for load in self.loads], [self.weights[S3FD_WEIGHTS], self.weights[SYNCNET_WEIGHTS]])
        self.assertEqual([load["weights_only"] for load in self.loads], [True, True])  # forced over weights_only=False
        for load in self.loads:
            self.assertNotEqual(Path(load["path"]).parent, folder)  # a private copy, not the models dir file
            self.assertFalse(os.path.exists(load["path"]))  # removed once loaded
        import torch

        self.assertEqual(torch.load.__name__, "real_load")  # the patch is undone

    def test_a_swapped_weight_file_is_refused(self) -> None:
        from deepfake_lens import lipsync

        pins = {name: _sha(data) for name, data in self.weights.items()}
        with _models_dir(pins) as folder:
            self._provision(folder)
            (folder / SYNCNET_WEIGHTS).write_bytes(b"other weights")
            with self.assertRaises(AssetPinError) as caught:
                lipsync._syncnet_analysis(self.root / "clip.mp4")
        self.assertIn(f"미고정 모델: {SYNCNET_WEIGHTS} — sha256 불일치", str(caught.exception))
        self.assertEqual(self.built, [])

    def test_a_repin_and_a_removed_pin_take_effect_in_the_same_process(self) -> None:
        """R16-3 (round 16): the SyncNet pipeline was built once per process and reused after a re-pin or a removed pin."""
        from deepfake_lens import lipsync

        pins = {name: _sha(data) for name, data in self.weights.items()}
        clip = self.root / "clip.mp4"
        with _models_dir(pins) as folder, contextlib.redirect_stderr(io.StringIO()):
            self._provision(folder)
            self.assertIsNotNone(lipsync._syncnet_analysis(clip))
            self.assertIsNotNone(lipsync._syncnet_analysis(clip))
            self.assertEqual(len(self.built), 1)  # unchanged pins: the pipeline is reused
            _set_pin(folder, SYNCNET_WEIGHTS, _sha(b"other syncnet weights"))
            with self.assertRaises(AssetPinError) as caught:
                lipsync._syncnet_analysis(clip)
            self.assertIn(f"미고정 모델: {SYNCNET_WEIGHTS} — sha256 불일치", str(caught.exception))
            _set_pin(folder, SYNCNET_WEIGHTS, "")
            with self.assertRaises(AssetPinError) as caught:
                lipsync._syncnet_analysis(clip)
            self.assertEqual(str(caught.exception), f"미고정 모델: {SYNCNET_WEIGHTS}")
            self.assertEqual(len(self.built), 1)  # never rebuilt from refused bytes
            _set_pin(folder, SYNCNET_WEIGHTS, pins[SYNCNET_WEIGHTS])
            self.assertIsNotNone(lipsync._syncnet_analysis(clip))
            self.assertEqual(len(self.built), 2)  # pinned again: rebuilt from freshly verified copies
            (folder / SYNCNET_WEIGHTS).write_bytes(b"swapped after the build")
            with self.assertRaises(AssetPinError):
                lipsync._syncnet_analysis(clip)  # the file changed: verified again, refused


def _rewrite_in_place(path: Path, data: bytes) -> None:
    """Rewrite ``path`` in place and make sure its mtime moved past the previous one.

    R17-12: the product notices a changed manifest / asset file by its
    (mtime, ctime, size, inode); a rewrite with the same size inside one tick
    of the file-system clock (≈1 ms on some kernels) leaves all four equal —
    the documented residual (HANDOFF §7). An operator's edit is not made
    within a millisecond of the previous one, so the test moves the mtime
    forward when the clock did not, instead of depending on its granularity.
    """
    before = path.stat().st_mtime_ns if path.exists() else None
    path.write_bytes(data)
    if before is not None and path.stat().st_mtime_ns <= before:
        later = before + 1_000_000
        os.utime(path, ns=(later, later))


def _set_pin(folder: Path, asset: str, digest: str) -> None:
    """Rewrite ``asset``'s sha256 in ``folder``/assets.json in place (what an operator's re-pin or edit does)."""
    manifest = folder / "assets.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    for entry in payload["assets"]:
        if entry["name"] == asset:
            entry["sha256"] = digest
    # R17-12: a re-pin keeps the size (64 hex digits) — the stat must still change.
    _rewrite_in_place(manifest, json.dumps(payload).encode("utf-8"))


class PinAssetCommandTest(unittest.TestCase):
    def test_pin_asset_records_the_digest_and_the_asset_then_loads(self) -> None:
        from deepfake_lens import cli

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            (folder / FACE_LANDMARKER).write_bytes(b"trusted copy")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main(["vendor-weights", "pin-asset", FACE_LANDMARKER, "--models-dir", str(folder)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["sha256"], _sha(b"trusted copy"))
            written = model_assets.load_manifest(folder / "assets.json")
            self.assertEqual(written[FACE_LANDMARKER]["sha256"], _sha(b"trusted copy"))
            self.assertEqual(written[HAAR_FRONTALFACE]["sha256"], _sha(BUNDLED_HAAR.read_bytes()))  # the packaged pins carried over
            with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_MODELS_DIR": str(folder)}):
                self.assertEqual(model_assets.verified_bytes(FACE_LANDMARKER, folder / FACE_LANDMARKER), b"trusted copy")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(cli.main(["vendor-weights", "pin-asset", "unknown.task", "--models-dir", str(folder)]), 1)
            self.assertIn("등록되지 않은 자산", err.getvalue())

    def test_a_models_dir_that_is_not_a_folder_is_a_korean_error_exit_2(self) -> None:
        """R16-8 / R16-14 (round 16): pin-asset printed "[Errno 2] … assets.json.tmp"; --verify said "통과" with rc 0."""
        from deepfake_lens import cli

        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp).resolve() / "없는 폴더"
            a_file = Path(tmp).resolve() / "file.txt"
            a_file.write_text("x", encoding="utf-8")
            cases = [
                (["vendor-weights", "pin-asset", HAAR_FRONTALFACE, "--asset-file", str(BUNDLED_HAAR), "--models-dir", str(missing)], "모델 폴더가 없습니다"),
                (["vendor-weights", "--verify", "--models-dir", str(missing)], "모델 폴더가 없습니다"),
                (["vendor-weights", "--verify", "--format", "json", "--models-dir", str(missing)], "모델 폴더가 없습니다"),
                (["vendor-weights", "--models-dir", str(missing)], "모델 폴더가 없습니다"),
                (["vendor-weights", "--verify", "--models-dir", str(a_file)], "모델 폴더가 아니라 파일입니다"),
            ]
            for argv, reason in cases:
                with self.subTest(argv=argv[1:3]):
                    out, err = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        code = cli.main(argv)
                    self.assertEqual(code, 2)
                    self.assertIn(reason, err.getvalue())
                    self.assertNotIn("통과", out.getvalue())
                    for english in ("Errno", "No such file", ".tmp"):
                        self.assertNotIn(english, err.getvalue())
            self.assertFalse(missing.exists())  # nothing created

    def test_a_manifest_that_cannot_be_written_is_reported_without_the_temp_name(self) -> None:
        """R16-8: the write error is Korean, names assets.json, and the temp file is removed."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            (folder / FACE_LANDMARKER).write_bytes(b"copy")
            with mock.patch.object(model_assets.os, "replace", side_effect=PermissionError(13, "Permission denied")):
                with self.assertRaises(ValueError) as caught:
                    model_assets.pin_asset(FACE_LANDMARKER, models_dir=folder)
            message = str(caught.exception)
            self.assertIn("자산 매니페스트를 쓸 수 없습니다", message)
            self.assertIn("접근 권한이 없습니다", message)
            self.assertNotIn("Permission denied", message)
            self.assertNotIn(".assets-", message)
            self.assertEqual(sorted(path.name for path in folder.iterdir()), [FACE_LANDMARKER])  # no temp file left
            with self.assertRaises(ValueError) as missing:
                model_assets.pin_asset(FACE_LANDMARKER, models_dir=folder / "없음")
            self.assertEqual(str(missing.exception), f"모델 폴더가 없습니다: {folder / '없음'}")

    def test_pinning_an_asset_changes_the_scan_cache_context(self) -> None:
        from deepfake_lens.scan_cache import _cache_scan_context

        with _models_dir({}) as folder:
            before = _cache_scan_context(None, models_dir=folder)
        with _models_dir({FACE_LANDMARKER: _sha(b"x")}) as folder:
            after = _cache_scan_context(None, models_dir=folder)
        self.assertNotEqual(before, after)  # a row cached under the refusal is not replayed once pinned


if __name__ == "__main__":
    unittest.main()

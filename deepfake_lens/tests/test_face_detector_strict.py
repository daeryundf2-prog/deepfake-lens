"""R16-1 / R16-2 (round 16): a face detector that cannot run or raises is a failed check, never "얼굴 미검출".

The verifier found ``faceswap_seam`` and ``face_track`` on the lenient
``face._detect_faces`` (``[]`` on any detector error): a cascade refused
for its pin or that does not load, a MediaPipe crash or a ``cv2.error``
was recorded as skipped "얼굴 미검출" / "얼굴이 검출된 프레임이 0개", while
``face_manipulation`` (strict) said failed for the same image. The model
face crops (``requires_face`` / ``crop_faces``) had the same defect. And a
``DEEPFAKE_LENS_HAAR_CASCADE`` override with other bytes was silently
replaced by the bundled cascade (R16-2).

Each injection below — a pin mismatch (override), a broken asset
manifest, ``cv2.error`` in the detector, a MediaPipe crash — must leave the
check ``failed`` with the cause, in every face layer.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Iterator
from unittest import mock

from deepfake_lens.model_assets import HAAR_FRONTALFACE, PACKAGED_MANIFEST

HAVE_CV2 = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None
PACKAGE = Path(__file__).resolve().parents[1]
BUNDLED_HAAR = PACKAGE / "models" / HAAR_FRONTALFACE


class _Cascade:
    """Stands in for ``cv2.CascadeClassifier`` (OpenCV 5 wheels have none)."""

    faces: list[tuple[int, int, int, int]] = []
    raise_error = False
    opened: list[bytes] = []

    def __init__(self, path: str = "") -> None:
        data = Path(path).read_bytes() if os.path.isfile(path) else b""
        type(self).opened.append(data)
        self._empty = not data.lstrip().startswith(b"<?xml")

    def empty(self) -> bool:
        return self._empty

    def detectMultiScale(self, *args: Any) -> list:  # noqa: N802 - OpenCV's name
        if type(self).raise_error:
            import cv2

            raise cv2.error("주입된 검출 실패")
        return list(type(self).faces)


class _Capture:
    """Stands in for ``cv2.VideoCapture``: 40 grey frames at 25 fps."""

    def __init__(self, *args: Any) -> None:
        import numpy as np

        self._frame: Any = np.full((120, 160, 3), 128, np.uint8)
        self._left = 40
        self._pos = 0

    def isOpened(self) -> bool:  # noqa: N802
        return True

    def get(self, prop: int) -> float:
        import cv2

        return {cv2.CAP_PROP_FPS: 25.0, cv2.CAP_PROP_FRAME_COUNT: 40.0}.get(prop, 0.0)

    def set(self, prop: int, value: float) -> bool:
        self._pos = int(value)
        return True

    def grab(self) -> bool:
        if self._left <= 0:
            return False
        self._left -= 1
        return True

    def retrieve(self) -> tuple[bool, Any]:
        return True, self._frame.copy()

    def read(self) -> tuple[bool, Any]:
        if self._pos >= 40:
            return False, None
        self._pos += 1
        return True, self._frame.copy()

    def release(self) -> None:
        pass


class _FaceFixture(unittest.TestCase):
    """A fake Haar classifier, an image and a video stand-in (no tests of its own)."""

    def setUp(self) -> None:
        import cv2
        import numpy as np

        from deepfake_lens.native_path import imwrite_any

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        _Cascade.faces = []
        _Cascade.raise_error = False
        _Cascade.opened = []
        for patcher in (
            mock.patch.object(cv2, "CascadeClassifier", _Cascade, create=True),
            mock.patch.dict(os.environ, {"DEEPFAKE_LENS_HAAR_CASCADE": ""}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        rng = np.random.default_rng(16)
        self.image = self.root / "사진.png"
        self.assertTrue(imwrite_any(self.image, rng.integers(0, 255, (200, 200, 3), dtype=np.uint8)))
        self.video = self.root / "영상.mp4"
        self.video.write_bytes(b"\x00" * 64)


@unittest.skipUnless(HAVE_CV2, "opencv not installed")
class StrictFaceDetectionTest(_FaceFixture):
    # -- injections -----------------------------------------------------------------

    @contextlib.contextmanager
    def _override_with_other_bytes(self) -> Iterator[str]:
        other = self.root / "my_cascade.xml"
        other.write_bytes(BUNDLED_HAAR.read_bytes() + b"\n<!-- edited -->\n")
        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_HAAR_CASCADE": str(other)}):
            yield "미고정 모델: 재정의 cascade sha256 불일치"

    @contextlib.contextmanager
    def _broken_manifest(self) -> Iterator[str]:
        folder = self.root / "models"
        folder.mkdir()
        for profile in (PACKAGE / "models").glob("*.json"):
            (folder / profile.name).write_bytes(profile.read_bytes())
        (folder / "assets.json").write_text("{broken", encoding="utf-8")
        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_MODELS_DIR": str(folder)}):
            yield f"미고정 모델: {HAAR_FRONTALFACE}"

    @contextlib.contextmanager
    def _cv2_error(self) -> Iterator[str]:
        _Cascade.raise_error = True
        try:
            yield "error: 주입된 검출 실패"
        finally:
            _Cascade.raise_error = False

    @contextlib.contextmanager
    def _mediapipe_crash(self) -> Iterator[str]:
        from deepfake_lens import face

        with mock.patch.object(face, "_mediapipe_detect_faces", side_effect=face.FaceDetectionError("RuntimeError: 주입된 MediaPipe 충돌")):
            yield "RuntimeError: 주입된 MediaPipe 충돌"

    def _injections(self) -> list[tuple[str, Any]]:
        return [
            ("override pin mismatch", self._override_with_other_bytes),
            ("broken manifest", self._broken_manifest),
            ("cv2.error", self._cv2_error),
            ("MediaPipe crash", self._mediapipe_crash),
        ]

    # -- tests ----------------------------------------------------------------------

    def test_every_image_face_layer_is_failed_with_the_cause(self) -> None:
        from deepfake_lens import core

        for label, injection in self._injections():
            with self.subTest(injection=label), injection() as cause:
                layers = core._deep_image_layers(self.image)
                entries = {entry.check: entry for entry in layers.coverage}
                for check in ("face_manipulation", "faceswap_seam"):
                    self.assertEqual(entries[check].status.value, "failed", (check, entries[check]))
                    self.assertIn(cause, entries[check].reason)
                    self.assertNotIn("얼굴 미검출", entries[check].reason)

    def test_the_seam_layer_reports_the_detector_error(self) -> None:
        from deepfake_lens.face import FACE_DETECTION_ERROR
        from deepfake_lens.faceswap_seam import NO_FACE_NOTE, analyze_faceswap_seam

        with self._cv2_error():
            analysis = analyze_faceswap_seam(self.image)
        self.assertTrue(analysis.reference_note.startswith(FACE_DETECTION_ERROR), analysis.reference_note)
        self.assertNotEqual(analysis.reference_note, NO_FACE_NOTE)

    def test_a_real_run_without_a_face_is_still_no_face(self) -> None:
        import numpy as np

        from deepfake_lens import core, face
        from deepfake_lens.native_path import imwrite_any

        blank = self.root / "blank.png"
        self.assertTrue(imwrite_any(blank, np.full((200, 200, 3), 128, np.uint8)))
        with mock.patch.object(face, "_mediapipe_detect_faces", side_effect=face.FaceDetectorUnavailable("mediapipe 없음")):
            layers = core._deep_image_layers(blank)
        entries = {entry.check: entry for entry in layers.coverage}
        for check in ("face_manipulation", "faceswap_seam"):
            self.assertEqual((entries[check].status.value, entries[check].reason), ("skipped", "얼굴 미검출"))

    def test_the_face_track_layer_is_failed_with_the_cause(self) -> None:
        import cv2

        from deepfake_lens import core, face
        from deepfake_lens.face import FACE_DETECTION_ERROR
        from deepfake_lens.face_track import analyze_face_track

        for label, injection in self._injections():
            with self.subTest(injection=label), injection() as cause, mock.patch.object(cv2, "VideoCapture", _Capture), mock.patch.object(
                face, "face_detector_unavailable_reason", return_value=None
            ):
                track = analyze_face_track(self.video)
                self.assertFalse(track.available)
                self.assertTrue(track.reference_note.startswith(FACE_DETECTION_ERROR), track.reference_note)
                self.assertIn(cause, track.reference_note)
                layers = core._deep_video_layers(self.video)
                entries = {entry.check: entry for entry in layers.coverage}
                self.assertEqual(entries["face_track"].status.value, "failed", entries["face_track"])
                self.assertIn(cause, entries["face_track"].reason)
                self.assertNotIn("프레임이 0개", entries["face_track"].reason)

    def test_rppg_and_lipsync_fail_on_an_override_with_other_bytes(self) -> None:
        """R16-2: the override is the only cascade — the bundled one is never loaded in its place."""
        import cv2

        from deepfake_lens import core

        from deepfake_lens import lipsync

        with self._override_with_other_bytes() as cause, mock.patch.object(cv2, "VideoCapture", _Capture), mock.patch.object(
            lipsync, "_audio_envelope", return_value=([0.1, 0.2] * 40, 0.1)
        ), mock.patch.object(lipsync.shutil, "which", return_value="/usr/bin/ffmpeg"):
            layers = core._deep_video_layers(self.video)
        entries = {entry.check: entry for entry in layers.coverage}
        for check in ("rppg", "lipsync"):
            self.assertEqual(entries[check].status.value, "failed", entries[check])
            self.assertIn(cause, entries[check].reason)
        self.assertNotIn(BUNDLED_HAAR.read_bytes(), _Cascade.opened)

    def test_a_face_conditioned_model_member_is_failed_not_skipped(self) -> None:
        """R16-1: requires_face / crop_faces members used the lenient detector too."""
        from deepfake_lens import model_adapter
        from deepfake_lens.face import FACE_DETECTION_ERROR

        for flag in ("requires_face", "crop_faces"):
            with self.subTest(flag=flag), self._cv2_error() as cause:
                result = model_adapter._score_from_runtime_profile(
                    {"runtime": "onnx", "name": "face-member", "checkpoint": "missing.onnx", flag: True},
                    self.image, base_dir=self.root,
                )
                assert result is not None
                self.assertEqual(result.confidence, model_adapter.FAILED_CONFIDENCE)
                self.assertTrue(result.detail.startswith(FACE_DETECTION_ERROR), result.detail)
                self.assertIn(cause, result.detail)

    def test_the_override_is_the_only_candidate(self) -> None:
        """R16-2: set → only it (missing → error); unset → OpenCV's copy and the bundled one."""
        from deepfake_lens import face
        from deepfake_lens.native_path import CascadeLoadError

        with self._override_with_other_bytes():
            self.assertEqual(len(face.haar_cascade_candidates()), 1)
            with self.assertRaises(face.OverrideCascadePinError) as caught:
                face.load_face_cascade()
        self.assertTrue(str(caught.exception).startswith("미고정 모델: 재정의 cascade sha256 불일치("), str(caught.exception))
        self.assertEqual(_Cascade.opened, [])  # never handed to OpenCV
        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_HAAR_CASCADE": str(self.root / "없음.xml")}):
            self.assertEqual(face.haar_cascade_candidates(), [])
            with self.assertRaises(CascadeLoadError) as missing:
                face.load_face_cascade()
        self.assertIn("재정의 cascade 파일이 없습니다", str(missing.exception))
        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_HAAR_CASCADE": str(BUNDLED_HAAR)}):
            self.assertFalse(face.load_face_cascade().empty())  # an override holding the pinned bytes loads
        self.assertIn(str(BUNDLED_HAAR), face.haar_cascade_candidates())

    def test_an_unpinned_override_names_the_asset(self) -> None:
        from deepfake_lens import face
        from deepfake_lens.model_assets import AssetPinError

        with self._broken_manifest(), mock.patch.dict(os.environ, {"DEEPFAKE_LENS_HAAR_CASCADE": str(BUNDLED_HAAR)}):
            with self.assertRaises(AssetPinError) as caught:
                face.load_face_cascade()
        self.assertEqual(str(caught.exception), f"미고정 모델: {HAAR_FRONTALFACE}")
        self.assertNotIsInstance(caught.exception, face.OverrideCascadePinError)


def _mediapipe_without_facemesh(name: str = "mediapipe") -> Any:
    """R17-5: a mediapipe ≥ 0.10.30 — no ``solutions`` (FaceMesh), the Tasks API present."""
    import types

    return types.SimpleNamespace(__version__="0.10.30", __name__=name)


@unittest.skipUnless(HAVE_CV2, "opencv not installed")
class MeasuredLandmarksMissingTest(_FaceFixture):
    """R17-5 (round 17): with mediapipe ≥ 0.10.30 (no FaceMesh) and no face_landmarker.task, Haar found a face
    in every frame yet face_track said "얼굴이 검출된 프레임이 0개" (every landmark a box-ratio estimate);
    doctor showed mediapipe OK; a DEEPFAKE_LENS_FACE_LANDMARKER naming no file was ignored."""

    def setUp(self) -> None:
        super().setUp()
        from deepfake_lens import face

        _Cascade.faces = [(20, 20, 80, 80)]  # Haar "finds" a face in every frame
        for patcher in (
            mock.patch.object(face, "import_mediapipe", side_effect=_mediapipe_without_facemesh),
            mock.patch.object(face, "_FACE_LANDMARKER_ASSET", self.root / "models" / "face_landmarker.task"),
            mock.patch.dict(os.environ, {"DEEPFAKE_LENS_FACE_LANDMARKER": "", "DEEPFAKE_LENS_HAAR_CASCADE": str(BUNDLED_HAAR)}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_face_track_is_skipped_for_the_missing_detector(self) -> None:
        import cv2

        from deepfake_lens import core, face

        reason = face.face_detector_unavailable_reason(require_landmarks=True)
        self.assertEqual(reason, f"{face.MEASURED_LANDMARKS_MISSING}: mediapipe에 FaceMesh(solutions)가 없고 face_landmarker.task 자산도 없습니다")
        with mock.patch.object(cv2, "VideoCapture", _Capture):
            layers = core._deep_video_layers(self.video)
        entry = {item.check: item for item in layers.coverage}["face_track"]
        self.assertEqual((entry.status.value, entry.reason), ("skipped", f"의존성 부재: {reason}"))
        self.assertIsNone(face.face_detector_unavailable_reason())  # Haar / the heuristic still detect faces

    def test_an_override_naming_no_file_fails_the_checks(self) -> None:
        import cv2

        from deepfake_lens import core, face

        missing = self.root / "없는.task"
        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_FACE_LANDMARKER": str(missing)}):
            self.assertIsNone(face.face_detector_unavailable_reason(require_landmarks=True))  # the loader decides
            with mock.patch.object(cv2, "VideoCapture", _Capture):
                video = {item.check: item for item in core._deep_video_layers(self.video).coverage}
            image = {item.check: item for item in core._deep_image_layers(self.image).coverage}
        cause = "재정의 FaceLandmarker 파일이 없습니다(DEEPFAKE_LENS_FACE_LANDMARKER): '없는.task'"
        for entry in (video["face_track"], image["face_manipulation"], image["faceswap_seam"]):
            self.assertEqual(entry.status.value, "failed", entry)
            self.assertIn(cause, entry.reason)

    def test_doctor_warns_on_the_mediapipe_row(self) -> None:
        from deepfake_lens import doctor

        real = doctor._import_dependency

        def fake(name: str) -> Any:
            return _mediapipe_without_facemesh() if name == "mediapipe" else real(name)

        with mock.patch.object(doctor, "_import_dependency", side_effect=fake):
            report = doctor._run_diagnostics(None)
        row = next(check for check in report.dependencies if check.name == "mediapipe")
        self.assertEqual(row.status, "warn")
        self.assertIn("실측 랜드마크 검출기 없음", row.detail)
        self.assertNotIn("대체 경로", row.detail)


class PackagedManifestTest(unittest.TestCase):
    def test_the_bundled_cascade_is_pinned(self) -> None:
        payload = json.loads(PACKAGED_MANIFEST.read_text(encoding="utf-8"))
        self.assertTrue(any(entry["name"] == HAAR_FRONTALFACE and entry["sha256"] for entry in payload["assets"]))


if __name__ == "__main__":
    unittest.main()

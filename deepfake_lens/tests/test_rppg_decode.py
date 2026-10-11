"""R18-7 (round 18): rPPG on a video it cannot read is a failed check with the cause.

``rppg._face_roi_samples`` returned ``([], [], 0, 0)`` when OpenCV could not
open the video, so a 0-byte, damaged, moov-less or truncated mp4 was
recorded skipped "얼굴 영역을 충분히 추적하지 못했습니다 (획득 프레임 0, 최소
30)" — while face_track recorded the same file failed "영상을 디코딩할 수
없습니다" (the R16-1 kind of defect). Now :class:`rppg.VideoDecodeError` is
raised for a video that does not open or yields no frame, and the scan's
rppg entry is ``failed`` with it.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens import rppg
from deepfake_lens.result_types import CoverageStatus

HAVE_CV2 = importlib.util.find_spec("cv2") is not None and importlib.util.find_spec("numpy") is not None


def _haar_available() -> bool:
    if not HAVE_CV2:
        return False
    import cv2

    return hasattr(cv2, "CascadeClassifier")


class _Capture:
    def __init__(self, opened: bool, frames: int) -> None:
        self.opened, self.frames = opened, frames

    def isOpened(self) -> bool:  # noqa: N802 — the OpenCV name
        return self.opened

    def get(self, prop: int) -> float:
        return {1: 25.0, 2: float(self.frames)}.get(prop, 0.0)

    def set(self, prop: int, value: float) -> bool:
        return True

    def read(self) -> tuple[bool, Any]:
        return False, None

    def release(self) -> None:
        pass


def _fake_cv2(opened: bool, frames: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        VideoCapture=lambda _path: _Capture(opened, frames), CAP_PROP_FPS=1, CAP_PROP_FRAME_COUNT=2, CAP_PROP_POS_FRAMES=3,
    )


class RoiSamplingRaisesTest(unittest.TestCase):
    def test_a_video_that_does_not_open_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.mp4"
            path.write_bytes(b"")
            with mock.patch.dict(sys.modules, {"cv2": _fake_cv2(opened=False, frames=0)}):
                with self.assertRaises(rppg.VideoDecodeError) as caught:
                    rppg._face_roi_samples(path, max_frames=10)
        self.assertEqual(str(caught.exception), rppg.VIDEO_OPEN_FAILED)

    def test_a_video_without_a_decodable_frame_raises(self) -> None:
        for frames in (0, 120):  # no frame count (moov-less) / a count but no frame decodes (truncated)
            with self.subTest(frames=frames), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "cut.mp4"
                path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
                with mock.patch.dict(sys.modules, {"cv2": _fake_cv2(opened=True, frames=frames)}), mock.patch.object(rppg, "_face_cascade", return_value=object()):
                    with self.assertRaises(rppg.VideoDecodeError) as caught:
                        rppg._face_roi_samples(path, max_frames=10)
                self.assertEqual(str(caught.exception), rppg.VIDEO_NO_FRAME)


@unittest.skipUnless(_haar_available(), "opencv with CascadeClassifier not installed")
class ScanRecordsRppgFailedTest(unittest.TestCase):
    def test_unreadable_videos_fail_the_rppg_check(self) -> None:
        from deepfake_lens.core import _deep_video_layers

        cases = {
            "zero.mp4": b"",
            "garbage.mp4": bytes(range(256)) * 64,
            "no-moov.mp4": b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00\x00\x00\x08free",
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, data in cases.items():
                path = Path(tmp) / name
                path.write_bytes(data)
                with self.subTest(video=name):
                    entry = next(e for e in _deep_video_layers(path).coverage if e.check == "rppg")
                    self.assertEqual(entry.status, CoverageStatus.FAILED, entry)
                    self.assertTrue(entry.reason.startswith("VideoDecodeError: 영상을 디코딩할 수 없습니다"), entry.reason)

    def test_the_rppg_command_says_why(self) -> None:
        import contextlib
        import io
        import json

        from deepfake_lens.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "zero.mp4"
            path.write_bytes(b"")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                main(["rppg", str(path), "--format", "json"])
        self.assertIn(rppg.VIDEO_OPEN_FAILED, json.dumps(json.loads(out.getvalue()), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()

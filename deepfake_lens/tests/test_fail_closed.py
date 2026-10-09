"""Fail-closed behaviour (phase 0, WP-B, G1 — QA-OUT-2, QA-OUT-3).

A check that raises must be recorded as ``failed`` with the exception
class and leave the verdict ``undetermined``; a missing dependency is
``skipped`` with "의존성 부재"; a crash must never read as a clean result.
Every test injects the fault with ``unittest.mock`` — no weights needed.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from deepfake_lens.checks import FAILURE_MESSAGE_MAX_CHARS, CheckSkipped, run_check
from deepfake_lens.core import analyze_file, scan_directory
from deepfake_lens.result_types import CoverageStatus, RiskBand, Verdict

PACKAGE = Path(__file__).resolve().parents[1]
HAVE_C2PA = importlib.util.find_spec("c2pa") is not None
HAVE_CV2 = importlib.util.find_spec("cv2") is not None


def _png(path: Path, width: int = 128, height: int = 128, text: dict[str, str] | None = None) -> Path:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    rows = b"".join(
        b"\x00" + bytes(v for x in range(width) for v in ((x * 7 + y * 3) % 256, (x * 5) % 256, (y * 11) % 256))
        for y in range(height)
    )
    texts = b"".join(chunk(b"tEXt", k.encode() + b"\x00" + v.encode()) for k, v in (text or {}).items())
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + texts + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    )
    return path


A1111 = {"parameters": "cat\nNegative prompt: dog\nSteps: 20, Sampler: Euler, CFG scale: 7, Seed: 5"}


def _entries(result, check: str):
    return [entry for entry in result.coverage if entry.check == check or entry.check.startswith(check + ":")]


class FailClosedAssertions(unittest.TestCase):
    def assertFailedUndetermined(self, item, check: str, exc_name: str = "RuntimeError") -> None:
        self.assertEqual(item.status, "analyzed")
        result = item.result
        assert result is not None
        entries = _entries(result, check)
        self.assertTrue(entries, f"no coverage entry for {check}: {result.coverage}")
        failed = [entry for entry in entries if entry.status == CoverageStatus.FAILED]
        self.assertTrue(failed, f"{check} not failed: {entries}")
        self.assertTrue(failed[0].reason.startswith(exc_name), failed[0].reason)
        self.assertNotIn("의존성 부재", failed[0].reason)
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        self.assertEqual(result.band, RiskBand.UNKNOWN)
        self.assertIn("검사 실패", result.verdict)
        self.assertTrue(any("검사 실패" in lim for lim in result.limitations))


class RunCheckTest(unittest.TestCase):
    def test_success_is_ran(self) -> None:
        value, entry = run_check("x", lambda: 42)
        self.assertEqual((value, entry.status), (42, CoverageStatus.RAN))

    def test_import_error_is_skipped_with_module(self) -> None:
        def boom():
            raise ModuleNotFoundError("No module named 'torch'", name="torch")

        value, entry = run_check("x", boom)
        self.assertIsNone(value)
        self.assertEqual((entry.status, entry.reason), (CoverageStatus.SKIPPED, "의존성 부재: torch"))

    def test_other_exception_is_failed_with_class_name(self) -> None:
        def boom():
            raise ValueError("x" * 500)

        with self.assertLogs("deepfake_lens.checks", level="ERROR") as logs:
            _, entry = run_check("x", boom)
        self.assertEqual(entry.status, CoverageStatus.FAILED)
        self.assertTrue(entry.reason.startswith("ValueError: "))
        self.assertEqual(len(entry.reason), len("ValueError: ") + FAILURE_MESSAGE_MAX_CHARS)
        self.assertIn("Traceback", "\n".join(logs.output))

    def test_check_skipped_keeps_reason(self) -> None:
        def skip():
            raise CheckSkipped("얼굴 미검출")

        _, entry = run_check("face_manipulation", skip)
        self.assertEqual((entry.status, entry.reason), (CoverageStatus.SKIPPED, "얼굴 미검출"))

    def test_reraise_passes_through(self) -> None:
        def boom():
            raise OSError("disk")

        with self.assertRaises(OSError):
            run_check("x", boom, reraise=(OSError,))


class ModelFailClosedTest(FailClosedAssertions):
    """QA-OUT-2: inference exception -> "실패: <예외 유형>", verdict 판단 불가."""

    def _profile(self, root: Path) -> Path:
        checkpoint = root / "fake.pth"
        checkpoint.write_bytes(b"not a real checkpoint")
        profile = root / "fake-runtime.json"
        # G9 (WP-C): weights load only against a matching pin, so the fake
        # profile pins its fake checkpoint to reach the inference path.
        pin = {"sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
        profile.write_text(json.dumps({"name": "fake-aide", "runtime": "aide", "checkpoint": str(checkpoint), "modality": "image", "pin": pin}), encoding="utf-8")
        return profile

    def test_inference_runtime_error_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = _png(root / "photo.png")
            profile = self._profile(root)
            with mock.patch("deepfake_lens.model_adapter._run_aide", side_effect=RuntimeError("CUDA exploded")), \
                    self.assertLogs("deepfake_lens.model_adapter", level="ERROR"):
                item = analyze_file(image, model_path=profile)
        self.assertFailedUndetermined(item, "external_model")
        assert item.result is not None
        self.assertEqual(item.result.score, 0)
        self.assertFalse(item.result.score_is_calibrated)

    def test_failed_member_in_a_zoo_is_reported_per_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = _png(root / "photo.png")
            profile = self._profile(root)
            sidecar = root / "sidecar.json"
            sidecar.write_text(json.dumps({"type": "score-sidecar-v1", "name": "ok-member"}), encoding="utf-8")
            (root / "photo.png.model.json").write_text(json.dumps({"score": 0.2}), encoding="utf-8")
            with mock.patch("deepfake_lens.model_adapter._run_aide", side_effect=RuntimeError("boom")), \
                    self.assertLogs("deepfake_lens.model_adapter", level="ERROR"):
                item = analyze_file(image, model_path=[profile, sidecar])
        assert item.result is not None
        by_check = {entry.check: entry for entry in item.result.coverage}
        self.assertEqual(by_check["model:fake-aide"].status, CoverageStatus.FAILED)
        self.assertEqual(by_check["model:ok-member"].status, CoverageStatus.RAN)
        self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED)

    def test_missing_runtime_dependency_is_skipped_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = _png(root / "photo.png")
            profile = self._profile(root)
            with mock.patch("deepfake_lens.model_adapter._run_aide", side_effect=ModuleNotFoundError("No module named 'torch'", name="torch")):
                item = analyze_file(image, model_path=profile)
        assert item.result is not None
        [entry] = _entries(item.result, "external_model")
        self.assertEqual(entry.status, CoverageStatus.SKIPPED)
        self.assertTrue(entry.reason.startswith("의존성 부재: torch"), entry.reason)

    def test_adapter_crash_itself_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = _png(Path(tmp) / "photo.png")
            with mock.patch("deepfake_lens.core.analyze_external_model", side_effect=KeyError("profile")), \
                    self.assertLogs("deepfake_lens.checks", level="ERROR"):
                item = analyze_file(image, model_path=Path(tmp) / "x.json")
        self.assertFailedUndetermined(item, "external_model", "KeyError")

    def test_below_measured_range_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = _png(Path(tmp) / "thumb.png", 64, 64)
            with mock.patch("deepfake_lens.core.analyze_external_model") as adapter:
                item = analyze_file(image, model_path=Path(tmp) / "x.json")
        adapter.assert_not_called()
        assert item.result is not None
        [entry] = _entries(item.result, "external_model")
        self.assertEqual(entry.status, CoverageStatus.SKIPPED)
        self.assertTrue(entry.reason.startswith("측정 범위 밖: 해상도"), entry.reason)
        self.assertIsNone(item.result.model_analysis)


class ImageLayerFailClosedTest(FailClosedAssertions):
    def test_pixel_crash_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = _png(Path(tmp) / "photo.png")
            with mock.patch("deepfake_lens.core.analyze_image_pixels", side_effect=ZeroDivisionError("div")), \
                    self.assertLogs("deepfake_lens.checks", level="ERROR"):
                item = analyze_file(image, pixel_mode="deep")
        self.assertFailedUndetermined(item, "pixel", "ZeroDivisionError")

    @unittest.skipUnless(HAVE_C2PA, "c2pa-python required for the SDK path")
    def test_c2pa_crash_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = _png(Path(tmp) / "photo.png")
            with mock.patch("deepfake_lens.c2pa.validate_c2pa_manifest", side_effect=RuntimeError("jumbf")), \
                    self.assertLogs("deepfake_lens.checks", level="ERROR"):
                item = analyze_file(image)
        self.assertFailedUndetermined(item, "c2pa")

    def test_c2pa_missing_sdk_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = _png(Path(tmp) / "photo.png")
            with mock.patch.dict("sys.modules", {"c2pa": None}):
                item = analyze_file(image)
        assert item.result is not None
        [entry] = _entries(item.result, "c2pa")
        self.assertEqual((entry.status, entry.reason), (CoverageStatus.SKIPPED, "의존성 부재: c2pa"))

    def test_strong_deterministic_evidence_survives_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = _png(Path(tmp) / "gen.png", text=A1111)
            with mock.patch("deepfake_lens.core.analyze_image_pixels", side_effect=RuntimeError("boom")), \
                    self.assertLogs("deepfake_lens.checks", level="ERROR"):
                item = analyze_file(image, pixel_mode="deep")
        assert item.result is not None
        self.assertEqual(item.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        self.assertTrue(any(e.status == CoverageStatus.FAILED and e.check == "pixel" for e in item.result.coverage))

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_face_layer_crash_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = _png(Path(tmp) / "photo.png")
            with mock.patch("deepfake_lens.face.analyze_faces", side_effect=RuntimeError("landmarks")), \
                    self.assertLogs("deepfake_lens.checks", level="ERROR"):
                item = analyze_file(image, deep_signals=True)
        self.assertFailedUndetermined(item, "face_manipulation")

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_no_face_is_recorded_as_skip(self) -> None:
        """QA-OUT-3: "얼굴 검사 미실행: 얼굴 미검출", never a 'no manipulation' result."""
        with tempfile.TemporaryDirectory() as tmp:
            image = _png(Path(tmp) / "landscape.png")
            with mock.patch("deepfake_lens.face._detect_faces_strict", return_value=[]):
                item = analyze_file(image, deep_signals=True)
        assert item.result is not None
        [entry] = _entries(item.result, "face_manipulation")
        self.assertEqual((entry.status, entry.reason), (CoverageStatus.SKIPPED, "얼굴 미검출"))
        self.assertEqual(entry.describe(), "얼굴 검사 미실행: 얼굴 미검출")
        self.assertFalse(any(e.layer == "face" for e in item.result.evidence))
        self.assertNotEqual(item.result.verdict_code, Verdict.AUTHENTICITY_EVIDENCE)

    @unittest.skipUnless(HAVE_CV2, "opencv required")
    def test_missing_face_detector_is_not_reported_as_no_face(self) -> None:
        from deepfake_lens.face import FaceDetectorUnavailable

        with tempfile.TemporaryDirectory() as tmp:
            image = _png(Path(tmp) / "photo.png")
            with mock.patch("deepfake_lens.face._detect_faces_strict", side_effect=FaceDetectorUnavailable("none")):
                item = analyze_file(image, deep_signals=True)
        assert item.result is not None
        [entry] = _entries(item.result, "face_manipulation")
        self.assertEqual(entry.status, CoverageStatus.SKIPPED)
        self.assertTrue(entry.reason.startswith("의존성 부재"), entry.reason)

    def test_deep_layers_disabled_are_listed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            item = analyze_file(_png(Path(tmp) / "photo.png"))
        assert item.result is not None
        reasons = {e.check: e.reason for e in item.result.coverage}
        for check in ("face_manipulation", "inpaint", "faceswap_seam"):
            # N13: Korean reason (was the option identifier "(deep_signals=false)").
            self.assertEqual(reasons[check], "비활성화(심층 신호 검사를 켜지 않음)")


class OtherModalityFailClosedTest(FailClosedAssertions):
    def test_audio_crash_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "voice.wav"
            audio.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
            with mock.patch("deepfake_lens.core.analyze_audio", side_effect=RuntimeError("decoder")), \
                    self.assertLogs("deepfake_lens.checks", level="ERROR"):
                item = analyze_file(audio)
        self.assertFailedUndetermined(item, "audio_analysis")

    def test_audio_decode_failure_is_not_dependency(self) -> None:
        from deepfake_lens import audio as audio_mod

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "voice.wav"
            path.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64)
            with mock.patch.object(audio_mod, "_extract_features_with_reason", return_value=(None, "LibsndfileError: bad header")):
                item = analyze_file(path)
        self.assertFailedUndetermined(item, "audio_features", "LibsndfileError")

    def test_video_crash_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            with mock.patch("deepfake_lens.core.analyze_video_temporal", side_effect=RuntimeError("moov")), \
                    mock.patch.dict("sys.modules", {"cv2": mock.MagicMock()}), \
                    self.assertLogs("deepfake_lens.checks", level="ERROR"):
                item = analyze_file(video)
        self.assertFailedUndetermined(item, "video_analysis")

    def test_video_deep_layer_crash_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            with mock.patch("deepfake_lens.avatar.analyze_avatar", side_effect=IndexError("frame")), \
                    self.assertLogs("deepfake_lens.checks", level="ERROR"):
                item = analyze_file(video, deep_signals=True)
        self.assertFailedUndetermined(item, "avatar", "IndexError")

    def test_document_extractor_crash_is_failed_but_text_stays_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            doc = Path(tmp) / "memo.docx"
            doc.write_bytes(b"PK\x03\x04 not really")
            with mock.patch("deepfake_lens.core.extract_document_text", side_effect=RuntimeError("xml")), \
                    self.assertLogs("deepfake_lens.checks", level="ERROR"):
                item = analyze_file(doc)
        self.assertFailedUndetermined(item, "document_text")
        assert item.result is not None
        self.assertEqual(item.result.grade.value, "reference")

    def test_whole_analyzer_crash_is_a_failed_row_not_a_clean_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _png(Path(tmp) / "photo.png")
            with mock.patch("deepfake_lens.core.read_image_metadata_full", side_effect=MemoryError("oom")), \
                    self.assertLogs("deepfake_lens.core", level="ERROR"):
                summary, items = scan_directory(Path(tmp))
        self.assertEqual(items[0].status, "failed")
        self.assertIn("MemoryError", items[0].error or "")
        self.assertEqual(summary.low + summary.authenticity_evidence, 0)


class NoCleanResultWithoutEvidenceTest(unittest.TestCase):
    """QA-OUT-1 (weights absent): no LOW/authenticity without deterministic support."""

    def test_metadata_only_images_are_undetermined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(5):
                _png(root / f"photo{index}.png", 128 + index * 16, 128)
            (root / "garbage.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
            summary, items = scan_directory(root, pixel_mode="deep", deep_signals=True)
        self.assertEqual(summary.low, 0)
        self.assertEqual(summary.authenticity_evidence, 0)
        for item in items:
            assert item.result is not None
            self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED, item.path)


class BlindExceptGuardTest(unittest.TestCase):
    """No `except Exception: pass` in the four verdict-path files (G1).

    Mirrors the ruff BLE001/S110 gate in pyproject so the rule holds even
    where ruff is not run.
    """

    FILES = ("core.py", "model_adapter.py", "webapp_api.py", "api_server.py")

    def test_no_silent_broad_except(self) -> None:
        offenders: list[str] = []
        for name in self.FILES:
            tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                broad = node.type is None or (isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"})
                if broad and all(isinstance(stmt, (ast.Pass, ast.Continue)) for stmt in node.body):
                    offenders.append(f"{name}:{node.lineno}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()

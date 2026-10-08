"""QA-SYS-3 (WP-H, G29): doctor's "실행 가능" summary matches a real scan.

A temporary models dir holds:

- ``fake-image`` — a pinned ``onnx`` image profile whose checkpoint file was
  deleted (the "필수 모델 하나를 삭제한 환경");
- ``fake-audio`` — the same for an ``onnx-audio`` profile;
- ``score-map`` — a weightless score-map profile that genuinely runs.

doctor must mark the deleted-checkpoint profiles MISS and count exactly the
profiles a scan of that models dir reports as ``ran`` in coverage
(``model:<name>``). Runs without torch/onnxruntime or any weights.
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

from deepfake_lens.analysis_api import AnalysisOptions, scan_folder
from deepfake_lens.cli import main as cli_main
from deepfake_lens.doctor import format_report, run_diagnostics
from deepfake_lens.model_adapter import _profile_matches_modality
from deepfake_lens.result_types import CoverageStatus, ScanItem

HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
IMAGE_NAME = "probe.png"
AUDIO_NAME = "clip.wav"


def _write_wav(path: Path, *, seconds: float = 1.0, rate: int = 16000) -> Path:
    frames = b"".join(struct.pack("<h", int(0.3 * 32767 * math.sin(2 * math.pi * 220 * n / rate))) for n in range(int(rate * seconds)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return path


def _write_models_dir(models: Path) -> None:
    """Two pinned profiles whose checkpoints are deleted + one runnable score map."""
    for name, runtime, modality in (("fake-image", "onnx", "image"), ("fake-audio", "onnx-audio", "audio")):
        checkpoint = models / f"{name}.onnx"
        checkpoint.write_bytes(b"fake weights " + name.encode())
        digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        (models / f"{name}-runtime.json").write_text(
            json.dumps({
                "type": "deepfake-lens-runtime-profile-v1",
                "name": name,
                "runtime": runtime,
                "modality": modality,
                "checkpoint": checkpoint.name,
                "pin": {"sha256": digest},
                "supported": True,
            }),
            encoding="utf-8",
        )
        checkpoint.unlink()  # the "deleted required model"
    (models / "score-map-runtime.json").write_text(
        json.dumps({
            "type": "deepfake-lens-runtime-profile-v1",
            "name": "score-map",
            "score_map": {IMAGE_NAME: 40, AUDIO_NAME: 40},
            "supported": True,
        }),
        encoding="utf-8",
    )


def _ran_models(items: list[ScanItem], path: str) -> set[str]:
    [item] = [item for item in items if item.path == path]
    assert item.result is not None
    return {
        entry.check.split(":", 1)[1]
        for entry in item.result.coverage
        if entry.check.startswith("model:") and entry.status == CoverageStatus.RAN
    }


class QaSys3DoctorMatchesScanTest(unittest.TestCase):
    """QA-SYS-3: 필수 모델 하나를 삭제한 환경에서 doctor 실행 → 해당 모델 MISS, "실행 가능" 요약이 실제 검사 결과의 coverage와 일치."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.models = root / "models"
        self.models.mkdir()
        _write_models_dir(self.models)
        self.media = root / "media"
        self.media.mkdir()
        _write_wav(self.media / AUDIO_NAME)

    def _doctor_runnable_for(self, modality: str) -> set[str]:
        report = run_diagnostics(self.models)
        return {
            status.name for status in report.model_profiles
            if status.runnable and _profile_matches_modality(self.models / status.file, modality)
        }

    def test_doctor_marks_deleted_checkpoint_miss(self) -> None:
        """QA-SYS-3: the profiles whose checkpoint was deleted are MISS and not runnable."""
        report = run_diagnostics(self.models)
        by_name = {status.name: status for status in report.model_profiles}
        for name in ("fake-image", "fake-audio"):
            with self.subTest(profile=name):
                self.assertEqual(by_name[name].pin, "ok")
                self.assertEqual(by_name[name].checkpoint, "missing")
                self.assertFalse(by_name[name].runnable)
                self.assertEqual(by_name[name].summary_check().status, "missing")
        self.assertTrue(by_name["score-map"].runnable)
        self.assertEqual(report.runnable_profiles, ["score-map"])
        text = format_report(report)
        self.assertIn("실행 가능: 1/3", text)
        self.assertIn("[ MISS] fake-image", text)

    def test_cli_doctor_json_runnable_count(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli_main(["doctor", "--models-dir", str(self.models), "--format", "json"]), 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["runnable"], {"count": 1, "total": 3, "names": ["score-map"]})

    @unittest.skipUnless(HAVE_NUMPY, "numpy not installed (photo-like fixture generator)")
    def test_runnable_count_matches_image_scan_coverage(self) -> None:
        """QA-SYS-3: doctor's runnable image profiles == model:<name> ran on a 256 px image."""
        from deepfake_lens.tests.qa.test_qa_out import write_photo_like_png

        write_photo_like_png(self.media / IMAGE_NAME, seed=3, width=256, height=256)
        _, items, _ = scan_folder(self.media, AnalysisOptions(models_dir=self.models))
        ran = _ran_models(items, IMAGE_NAME)
        doctor_runnable = self._doctor_runnable_for("image")
        self.assertEqual(ran, doctor_runnable)
        self.assertEqual(len(ran), 1)

    def test_runnable_count_matches_audio_scan_coverage(self) -> None:
        """QA-SYS-3 (gate-free modality): doctor's runnable audio profiles == model:<name> ran."""
        _, items, _ = scan_folder(self.media, AnalysisOptions(models_dir=self.models))
        ran = _ran_models(items, AUDIO_NAME)
        self.assertEqual(ran, self._doctor_runnable_for("audio"))
        self.assertEqual(ran, {"score-map"})
        [item] = [item for item in items if item.path == AUDIO_NAME]
        assert item.result is not None
        statuses = {entry.check: entry.status for entry in item.result.coverage}
        self.assertNotEqual(statuses.get("model:fake-audio"), CoverageStatus.RAN)


if __name__ == "__main__":
    unittest.main()

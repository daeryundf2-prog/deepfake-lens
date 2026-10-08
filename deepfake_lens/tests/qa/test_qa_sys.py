"""QA-SYS scenarios (phase 0).

QA-SYS-1 and QA-SYS-2 (WP-C, G9): a weight whose pin does not match is
never loaded, the model check is recorded as failed and the verdict is
undetermined. The detector is a fake ``onnx-audio`` profile pointing at a
temporary "checkpoint" file; the runtime call is mocked, so no weights,
torch or onnxruntime are needed. Audio is used for the end-to-end scans
because it has no photo/non-photo gate in front of the model check.

QA-SYS-10 (WP-J): the test inventory never drops below the recorded
baseline, and every baseline test that is gone is documented in
docs/TEST-DELETIONS.md with the reason in its commit message.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from deepfake_lens.core import analyze_file
from deepfake_lens.model_adapter import FAILED_CONFIDENCE, analyze_external_model
from deepfake_lens.model_pins import MISMATCH_REASON, UNPINNED_REASON
from deepfake_lens.result_types import ClassificationResult, CoverageEntry, CoverageStatus, Verdict

from .traceability import BASELINE_PATH, DELETIONS_DOC, REPO_ROOT, documented_deletions, short_names, source_inventory

RUNTIME = "deepfake_lens.model_adapter._run_onnx_audio"
# Raw logits the fake runtime returns: softmax index 1 -> ~0.88.
FAKE_LOGITS = [0.0, 2.0]


def _write_wav(path: Path, *, seconds: float = 1.0, rate: int = 16000) -> Path:
    frames = b"".join(struct.pack("<h", int(0.3 * 32767 * math.sin(2 * math.pi * 220 * n / rate))) for n in range(int(rate * seconds)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _one_char_changed(digest: str) -> str:
    """The same digest with exactly one hex character altered."""
    replacement = "0" if digest[0] != "0" else "1"
    return replacement + digest[1:]


class _Fixture:
    """A temp dir with a fake checkpoint, its pinned profile and a WAV."""

    def __init__(self, root: Path, name: str = "fake-detector") -> None:
        self.root = root
        self.checkpoint = root / f"{name}.onnx"
        self.checkpoint.write_bytes(b"fake checkpoint v1 " + name.encode())
        self.profile = root / f"{name}-runtime.json"
        self.name = name
        self.write_profile(_sha256(self.checkpoint))
        self.media = _write_wav(root / "clip.wav")

    def write_profile(self, sha256: str) -> None:
        self.profile.write_text(
            json.dumps(
                {
                    "type": "deepfake-lens-runtime-profile-v1",
                    "name": self.name,
                    "runtime": "onnx-audio",
                    "modality": "audio",
                    "checkpoint": self.checkpoint.name,
                    "score_index": 1,
                    "score_activation": "softmax",
                    "pin": {"sha256": sha256},
                    "measured_on": None,
                }
            ),
            encoding="utf-8",
        )


def _model_entries(result: ClassificationResult) -> list[CoverageEntry]:
    return [entry for entry in result.coverage if entry.check == "external_model" or entry.check.startswith("model:")]


class _IntegrityAssertions(unittest.TestCase):
    def _assert_integrity_failure(self, result: ClassificationResult | None) -> None:
        assert result is not None
        [entry] = _model_entries(result)
        self.assertEqual(entry.status, CoverageStatus.FAILED)
        self.assertTrue(entry.reason.startswith(MISMATCH_REASON), entry.reason)
        self.assertNotIn("의존성 부재", entry.reason)
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
        # D6/QA-SYS-1: the conclusion itself names the integrity failure.
        self.assertIn("판단 불가: 모델 무결성 실패", result.verdict)
        self.assertTrue(any("검사 실패" in item and MISMATCH_REASON in item for item in result.limitations), result.limitations)
        self.assertEqual(result.score, 0)
        assert result.model_analysis is not None
        self.assertFalse(result.model_analysis.available)
        self.assertEqual(result.model_analysis.confidence, FAILED_CONFIDENCE)


class QaSys1PinTamperTest(_IntegrityAssertions):
    """QA-SYS-1: a profile pin that does not match its checkpoint is never loaded."""

    def test_pinned_profile_baseline_runs(self) -> None:
        """QA-SYS-1 (control): with the correct pin the fake runtime is
        dispatched and the model check is recorded as ran."""
        with tempfile.TemporaryDirectory() as tmp:
            fx = _Fixture(Path(tmp))
            with mock.patch(RUNTIME, return_value=FAKE_LOGITS) as runtime:
                item = analyze_file(fx.media, model_path=fx.profile)
        runtime.assert_called_once()
        assert item.result is not None
        [entry] = _model_entries(item.result)
        self.assertEqual(entry.status, CoverageStatus.RAN)
        self.assertIsNotNone(item.result.model_analysis)
        assert item.result.model_analysis is not None
        self.assertTrue(item.result.model_analysis.available)

    def test_sha256_changed_by_one_char_refuses_load(self) -> None:
        """QA-SYS-1: 프로필의 sha256을 한 글자 바꾸고 검사 → 모델 로드 거부, 결론 "판단 불가: 모델 무결성 실패"."""
        with tempfile.TemporaryDirectory() as tmp:
            fx = _Fixture(Path(tmp))
            tampered = _one_char_changed(_sha256(fx.checkpoint))
            self.assertEqual(sum(a != b for a, b in zip(tampered, _sha256(fx.checkpoint))), 1)
            fx.write_profile(tampered)
            with mock.patch(RUNTIME, return_value=FAKE_LOGITS) as runtime:
                item = analyze_file(fx.media, model_path=fx.profile)
        runtime.assert_not_called()  # the weights were never handed to a runtime
        self._assert_integrity_failure(item.result)

    def test_unpinned_profile_refuses_load(self) -> None:
        """QA-SYS-1 (variant): an empty pin is "미고정 프로필" — load refused, 판단 불가."""
        with tempfile.TemporaryDirectory() as tmp:
            fx = _Fixture(Path(tmp))
            fx.write_profile("")
            with mock.patch(RUNTIME, return_value=FAKE_LOGITS) as runtime:
                item = analyze_file(fx.media, model_path=fx.profile)
        runtime.assert_not_called()
        assert item.result is not None
        [entry] = _model_entries(item.result)
        self.assertEqual(entry.status, CoverageStatus.FAILED)
        self.assertTrue(entry.reason.startswith(UNPINNED_REASON), entry.reason)
        self.assertIn("판단 불가: 모델 무결성 실패", item.result.verdict)
        self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED)

    def test_tampered_member_of_a_zoo_is_reported_per_member(self) -> None:
        """QA-SYS-1 (zoo): the tampered member gets its own failed
        ``model:<name>`` entry; the intact member still runs; 판단 불가."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = _Fixture(root, "good-detector")
            bad = _Fixture(root, "bad-detector")
            bad.write_profile(_one_char_changed(_sha256(bad.checkpoint)))
            with mock.patch(RUNTIME, return_value=FAKE_LOGITS) as runtime:
                item = analyze_file(good.media, model_path=[good.profile, bad.profile])
        self.assertEqual(runtime.call_count, 1)  # only the intact member loaded
        assert item.result is not None
        by_check = {entry.check: entry for entry in _model_entries(item.result)}
        self.assertEqual(by_check["model:good-detector"].status, CoverageStatus.RAN)
        self.assertEqual(by_check["model:bad-detector"].status, CoverageStatus.FAILED)
        self.assertTrue(by_check["model:bad-detector"].reason.startswith(MISMATCH_REASON))
        self.assertIn("판단 불가: 모델 무결성 실패(외부 모델(bad-detector))", item.result.verdict)
        self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED)


class QaSys2CheckpointSwapTest(_IntegrityAssertions):
    """QA-SYS-2: a swapped weight file is never loaded."""

    def test_checkpoint_replaced_by_another_file_refuses_load(self) -> None:
        """QA-SYS-2: 가중치 파일을 다른 파일로 바꾸고 검사 → 동일.

        "동일" = as QA-SYS-1: 모델 로드 거부, 결론 "판단 불가: 모델 무결성 실패".
        """
        with tempfile.TemporaryDirectory() as tmp:
            fx = _Fixture(Path(tmp))
            other = Path(tmp) / "other.onnx"
            other.write_bytes(b"a different model file")
            other.replace(fx.checkpoint)
            with mock.patch(RUNTIME, return_value=FAKE_LOGITS) as runtime:
                item = analyze_file(fx.media, model_path=fx.profile)
        runtime.assert_not_called()
        self._assert_integrity_failure(item.result)

    def test_swap_between_two_scans_is_caught_on_the_second(self) -> None:
        """QA-SYS-2 (no stale cache): the first scan verifies and runs; the
        checkpoint is then swapped and the second scan in the same process
        must re-hash and refuse it."""
        with tempfile.TemporaryDirectory() as tmp:
            fx = _Fixture(Path(tmp))
            with mock.patch(RUNTIME, return_value=FAKE_LOGITS) as runtime:
                first = analyze_file(fx.media, model_path=fx.profile)
                fx.checkpoint.write_bytes(b"swapped after the first scan")
                second = analyze_file(fx.media, model_path=fx.profile)
        self.assertEqual(runtime.call_count, 1)
        assert first.result is not None
        self.assertEqual(_model_entries(first.result)[0].status, CoverageStatus.RAN)
        self._assert_integrity_failure(second.result)

    def test_image_runtime_swap_is_refused_at_the_adapter(self) -> None:
        """QA-SYS-2 (image runtime): the same refusal for an ``onnx`` image profile."""
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "img.onnx"
            checkpoint.write_bytes(b"image weights v1")
            profile = root / "img-runtime.json"
            profile.write_text(
                json.dumps({"name": "img", "runtime": "onnx", "modality": "image", "checkpoint": "img.onnx", "pin": {"sha256": _sha256(checkpoint)}}),
                encoding="utf-8",
            )
            image = root / "photo.png"
            Image.new("RGB", (128, 128), (120, 80, 40)).save(image)
            checkpoint.write_bytes(b"image weights v2")
            with mock.patch("deepfake_lens.model_adapter._run_onnx", return_value=[0.0]) as runtime:
                analysis = analyze_external_model(image, profile)
        runtime.assert_not_called()
        assert analysis is not None
        self.assertFalse(analysis.available)
        self.assertEqual(analysis.confidence, FAILED_CONFIDENCE)
        self.assertTrue(analysis.detail.startswith(MISMATCH_REASON), analysis.detail)

    def test_video_frames_inner_checkpoint_swap_is_refused(self) -> None:
        """QA-SYS-2 (video-frames): the outer pin covers the inner checkpoint."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = root / "inner.onnx"
            checkpoint.write_bytes(b"inner weights v1")
            profile = root / "vf-runtime.json"
            profile.write_text(
                json.dumps(
                    {
                        "name": "vf",
                        "runtime": "video-frames",
                        "modality": "video",
                        "inner": {"runtime": "onnx", "checkpoint": "inner.onnx"},
                        "pin": {"sha256": _sha256(checkpoint)},
                    }
                ),
                encoding="utf-8",
            )
            checkpoint.write_bytes(b"inner weights v2")
            fake_cv2 = mock.MagicMock()
            with mock.patch("deepfake_lens.model_adapter.importlib.import_module", return_value=fake_cv2), \
                    mock.patch("deepfake_lens.model_adapter._run_onnx", return_value=[0.0]) as runtime:
                analysis = analyze_external_model(root / "clip.mp4", profile, modality="video")
        runtime.assert_not_called()
        fake_cv2.VideoCapture.assert_not_called()  # refused before any frame is decoded
        assert analysis is not None
        self.assertEqual(analysis.confidence, FAILED_CONFIDENCE)
        self.assertTrue(analysis.detail.startswith(MISMATCH_REASON), analysis.detail)


# Test-count floor: the inventory right before WP-C removed tests (commit
# 35cbc8e, WP-A..E applied) — recorded in tests/qa/test_inventory_baseline.json.
WP_BASELINE_FLOOR = 739
PRE_PHASE0_COUNT = 633
DELETED_SECTION = "삭제"
RENAMED_SECTION = "이름 변경"


class QaSys10TestInventoryTest(unittest.TestCase):
    """QA-SYS-10: no baseline test disappears without a documented reason."""

    baseline: dict[str, object]
    baseline_names: set[str]
    pre_phase0: set[str]
    current: set[str]
    documented: dict[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        rows = cls.baseline["tests"]
        assert isinstance(rows, list)
        cls.baseline_names = {str(row["name"]).split("::", 1)[1] for row in rows}
        cls.pre_phase0 = {str(row["name"]).split("::", 1)[1] for row in rows if row["pre_phase0"]}
        cls.current = short_names(source_inventory())
        cls.documented = documented_deletions()

    def test_inventory_floor_and_documented_deletions(self) -> None:
        """QA-SYS-10: 기존 633개 테스트 중 유지 대상 전부 통과. 삭제된 테스트는 삭제 이유가 커밋 메시지에 기록.

        Here: the baseline has 633 pre-phase-0 / 739 WP-baseline tests; the
        current count is >= 739; every baseline test still exists or is
        listed in docs/TEST-DELETIONS.md (삭제/이름 변경); the reasons are in
        the commit messages (sibling test). "전부 통과" is the full-suite run
        that scripts/qa_phase0.py adds to this QA ID.
        """
        self.assertEqual(len(self.pre_phase0), PRE_PHASE0_COUNT)
        self.assertEqual(len(self.baseline_names), WP_BASELINE_FLOOR)
        self.assertGreaterEqual(len(self.current), WP_BASELINE_FLOOR, "test count dropped below the WP baseline")
        missing = sorted(self.baseline_names - self.current)
        undocumented = [name for name in missing if name not in self.documented]
        self.assertEqual(undocumented, [], "baseline tests removed without an entry in docs/TEST-DELETIONS.md")
        retained = self.pre_phase0 - set(self.documented)
        self.assertEqual(sorted(retained - self.current), [], "a retained pre-phase-0 test is gone")

    def test_deletion_document_is_accurate(self) -> None:
        """QA-SYS-10: every documented deletion was a baseline test and is really gone; renames exist under the new name."""
        self.assertTrue(self.documented, DELETIONS_DOC)
        for name, section in self.documented.items():
            with self.subTest(test=name):
                self.assertIn(section, {DELETED_SECTION, RENAMED_SECTION})
                self.assertIn(name, self.baseline_names, "only baseline tests can be listed")
                self.assertNotIn(name, self.current, "a listed test still exists")
        text = DELETIONS_DOC.read_text(encoding="utf-8")
        renamed_rows = [line for line in text.split(f"## {RENAMED_SECTION}", 1)[1].split("## ", 1)[0].splitlines() if line.startswith("| `")]
        for row in renamed_rows:
            new_name = row.strip("|").split("|")[-1].strip().strip("`")
            with self.subTest(renamed_to=new_name):
                self.assertIn(new_name, self.current)

    def test_deletion_reasons_are_in_commit_messages(self) -> None:
        """QA-SYS-10: each deleted test's commit message names it (needs git history; skipped on a shallow clone)."""
        git = shutil.which("git")
        if git is None:
            self.skipTest("git not available")
        text = DELETIONS_DOC.read_text(encoding="utf-8")
        deleted = [name for name, section in self.documented.items() if section == DELETED_SECTION]
        self.assertTrue(deleted)
        for name in deleted:
            row = next(line for line in text.splitlines() if f"`{name}`" in line)
            commit = row.strip("|").split("|")[1].strip().split()[0]
            done = subprocess.run([git, "log", "-1", "--format=%B", commit], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
            if done.returncode != 0:
                self.skipTest(f"commit {commit} not in this clone (shallow checkout)")
            with self.subTest(test=name, commit=commit):
                self.assertIn(name.split(".", 1)[1], done.stdout, "the deletion is not explained in its commit message")


if __name__ == "__main__":
    unittest.main()

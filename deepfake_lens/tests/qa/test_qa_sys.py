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

Moved here from ``test_qa_sys_doctor.py`` (W2, verify round 4 — one file per QA area):

QA-SYS-3 (WP-H, G29): doctor's "실행 가능" summary matches a real scan.

A temporary models dir holds:

- ``fake-image`` — a pinned ``onnx`` image profile whose checkpoint file was
  deleted (the "필수 모델 하나를 삭제한 환경");
- ``fake-audio`` — the same for an ``onnx-audio`` profile;
- ``score-map`` — a weightless score-map profile that genuinely runs.

doctor must mark the deleted-checkpoint profiles MISS and count exactly the
profiles a scan of that models dir reports as ``ran`` in coverage
(``model:<name>``). Runs without torch/onnxruntime or any weights.

Moved here from ``test_qa_sys_gate.py`` (W2, verify round 4 — one file per QA area):

QA-SYS-9 — measurement gate (phase 0, WP-I: G26/G28).

The gate must reject any runtime profile that is active (``supported`` true
or absent) without a test-split measurement record meeting the thresholds,
independently of which profiles ship in ``deepfake_lens/models``.

Moved here from ``test_qa_sys_integrity.py`` (W2, verify round 4 — one file per QA area):

QA-SYS-6 / QA-SYS-7 — report signature coverage and read-root confinement (WP-G: G30, G31).

Runs without network weights: the scans use ``no_default_engine`` and the
web tests drive the stdlib server (``webapp.build_server``) on an ephemeral
loopback port. ``deepfake-lens security`` runs these QA-SYS-6/7 classes as its
behavioral check set (security.SECURITY_QA_MODULES).

Moved here from ``test_qa_traceability.py`` (W2, verify round 4 — one file per QA area):

The QA traceability table is consistent with the tests (WP-J).

- every automated QA ID has exactly one test whose docstring first line is
  "<QA ID>: <통과 기준 원문>" (the canonical test scripts/qa_phase0.py reports);
- every QA ID a test is tagged with exists in traceability.json;
- every QA ID a requirement row names is defined, and every automated QA ID
  backs at least one requirement;
- every manual QA ID has its checklist section in docs/QA-MANUAL.md.
"""

from __future__ import annotations

import ast
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterator
from unittest import mock
from unittest.mock import patch

from deepfake_lens import webapp_api
from deepfake_lens.analysis_api import AnalysisOptions, scan_folder
from deepfake_lens.cli import main as cli_main
from deepfake_lens.core import analyze_file, scan_directory, scan_to_json
from deepfake_lens.corpus_manifest import SCHEMA, item_id, manifest_sha256
from deepfake_lens.doctor import format_report, run_diagnostics
from deepfake_lens.measurement_gate import (
    MIN_AUROC_CI_LOW,
    MIN_PER_CLASS,
    check_models_dir,
    check_profile,
    gate_report_lines,
)
from deepfake_lens.model_adapter import FAILED_CONFIDENCE, _profile_matches_modality, analyze_external_model
from deepfake_lens.model_pins import MISMATCH_REASON, UNPINNED_REASON
from deepfake_lens.reports import HEATMAP_PLACEHOLDER, _heatmap_img, extract_signed_report
from deepfake_lens.result_types import ClassificationResult, CoverageEntry, CoverageStatus, ScanItem, Verdict
from deepfake_lens.signing import REPORT_KEY_ENV, sign_report, verify_report

from .traceability import (
    AUXILIARY_PREFIX,
    BASELINE_PATH,
    DELETIONS_DOC,
    QA_DIR,
    REPO_ROOT,
    TESTS_DIR,
    automated_criteria,
    canonical_tests,
    documented_deletions,
    first_line,
    iter_tests,
    load_traceability,
    qa_tag,
    short_names,
    source_inventory,
)


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
        """QA-SYS-1: 보조 검사 — control: with the correct pin the fake runtime is
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
        """QA-SYS-1: 보조 검사 — variant: an empty pin is "미고정 프로필" — load refused, 판단 불가."""
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
        """QA-SYS-1: 보조 검사 — zoo: the tampered member gets its own failed
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
        """QA-SYS-2: 보조 검사 — no stale cache: the first scan verifies and runs; the
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
        """QA-SYS-2: 보조 검사 — image runtime: the same refusal for an ``onnx`` image profile."""
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
        """QA-SYS-2: 보조 검사 — video-frames: the outer pin covers the inner checkpoint."""
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
        """QA-SYS-10: 보조 검사 — every documented deletion was a baseline test and is really gone; renames exist under the new name."""
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
        """QA-SYS-10: 보조 검사 — each deleted test's commit message names it (needs git history; skipped on a shallow clone)."""
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


# ============================================================================
# Moved from deepfake_lens/tests/qa/test_qa_sys_doctor.py (W2): module docstring above.
# ============================================================================


HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
IMAGE_NAME = "probe.png"
AUDIO_NAME = "clip.wav"


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

    def test_deleted_model_is_miss_and_runnable_summary_matches_scan(self) -> None:
        """QA-SYS-3: 필수 모델 하나를 삭제한 환경에서 doctor 실행 → 해당 모델 MISS, "실행 가능" 요약이 실제 검사 결과의 coverage와 일치.

        Both halves on the audio scan (no photo gate in front of the model
        check): the deleted-checkpoint profile is MISS in doctor and not
        "ran" in coverage; doctor's runnable set equals the ran set.
        """
        report = run_diagnostics(self.models)
        by_name = {status.name: status for status in report.model_profiles}
        self.assertEqual(by_name["fake-audio"].checkpoint, "missing")
        self.assertFalse(by_name["fake-audio"].runnable)
        self.assertIn("[ MISS] fake-audio", format_report(report))
        _, items, _ = scan_folder(self.media, AnalysisOptions(models_dir=self.models))
        ran = _ran_models(items, AUDIO_NAME)
        self.assertEqual(ran, self._doctor_runnable_for("audio"))
        self.assertNotIn("fake-audio", ran)

    def test_doctor_marks_deleted_checkpoint_miss(self) -> None:
        """QA-SYS-3: 보조 검사 — the profiles whose checkpoint was deleted are MISS and not runnable."""
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
        """QA-SYS-3: 보조 검사 — doctor --format json의 실행 가능 요약은 핀·의존성·체크포인트가 모두 OK인 프로필만 센다."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli_main(["doctor", "--models-dir", str(self.models), "--format", "json"]), 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["runnable"], {"count": 1, "total": 3, "names": ["score-map"]})

    @unittest.skipUnless(HAVE_NUMPY, "numpy not installed (photo-like fixture generator)")
    def test_runnable_count_matches_image_scan_coverage(self) -> None:
        """QA-SYS-3: 보조 검사 — doctor's runnable image profiles == model:<name> ran on a 256 px image."""
        from deepfake_lens.tests.qa.test_qa_out import write_photo_like_png

        write_photo_like_png(self.media / IMAGE_NAME, seed=3, width=256, height=256)
        _, items, _ = scan_folder(self.media, AnalysisOptions(models_dir=self.models))
        ran = _ran_models(items, IMAGE_NAME)
        doctor_runnable = self._doctor_runnable_for("image")
        self.assertEqual(ran, doctor_runnable)
        self.assertEqual(len(ran), 1)

    def test_runnable_count_matches_audio_scan_coverage(self) -> None:
        """QA-SYS-3: 보조 검사 — gate-free modality: doctor's runnable audio profiles == model:<name> ran."""
        _, items, _ = scan_folder(self.media, AnalysisOptions(models_dir=self.models))
        ran = _ran_models(items, AUDIO_NAME)
        self.assertEqual(ran, self._doctor_runnable_for("audio"))
        self.assertEqual(ran, {"score-map"})
        [item] = [item for item in items if item.path == AUDIO_NAME]
        assert item.result is not None
        statuses = {entry.check: entry.status for entry in item.result.coverage}
        self.assertNotEqual(statuses.get("model:fake-audio"), CoverageStatus.RAN)


# ============================================================================
# Moved from deepfake_lens/tests/qa/test_qa_sys_gate.py (W2): module docstring above.
# ============================================================================


GATE_SCRIPT = REPO_ROOT / "scripts" / "check_measurement_gate.py"
CORPUS_ID = "t-img-test"
# Test-split class counts of the fixture manifest (>= every n used below).
MANIFEST_POS, MANIFEST_NEG = 240, 260
# Path of the fixture manifest relative to the models dir (D16).
MANIFEST_REL = "corpus/manifest.json"


def _manifest_payload(n_pos: int = MANIFEST_POS, n_neg: int = MANIFEST_NEG, corpus_id: str = CORPUS_ID) -> dict[str, object]:
    """A corpus-manifest-v1 document (no media files needed for the gate)."""
    items: list[dict[str, object]] = []
    for label, count in (("synthetic", n_pos), ("real", n_neg)):
        for index in range(count):
            relpath = f"{label}/{index:04d}.png"
            items.append({
                "id": item_id(relpath), "relpath": relpath, "sha256": f"{index:064x}", "modality": "image",
                "label": label, "generator": "sdxl" if label == "synthetic" else None, "variant": "original",
                "split": "test", "source_note": "qa fixture", "derived_from": None,
            })
    items.sort(key=lambda item: str(item["id"]))
    return {"schema": SCHEMA, "corpus_id": corpus_id, "created": "2026-10-01T00:00:00Z", "items": items, "manifest_sha256": manifest_sha256(items)}


FIXTURE_MANIFEST = _manifest_payload()
MANIFEST_SHA = str(FIXTURE_MANIFEST["manifest_sha256"])


def _measured(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "corpus_id": CORPUS_ID,
        "manifest_sha256": MANIFEST_SHA,
        "manifest_path": MANIFEST_REL,
        "split": "test",
        "n_pos": 240,
        "n_neg": 260,
        "auroc": 0.93,
        "auroc_ci": [0.90, 0.95],
        "fpr_at_threshold": 0.01,
        "recall_at_threshold": 0.7,
        "measured_at": "2026-11-02T10:00:00Z",
    }
    record.update(overrides)
    return record


def _image_profile(**fields: object) -> dict[str, object]:
    profile: dict[str, object] = {"type": "deepfake-lens-runtime-profile-v1", "runtime": "onnx", "modality": "image", "checkpoint": "x.onnx"}
    profile.update(fields)
    return profile


def _write(directory: Path, name: str, payload: object) -> Path:
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class MeasurementGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.models = Path(self._tmp.name)
        self.manifest = self.models / MANIFEST_REL
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text(json.dumps(FIXTURE_MANIFEST), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_script(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(GATE_SCRIPT), "--models-dir", str(self.models)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_qa_sys_9_unmeasured_profile_fails_ci(self) -> None:
        """QA-SYS-9: 측정 기록 없는 프로필을 models/에 추가하고 PR → CI 실패."""
        _write(self.models, "good-runtime.json", _image_profile(supported=True, measured_on=_measured()))
        _write(self.models, "unmeasured-runtime.json", _image_profile())  # supported absent → active
        done = self._run_script()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("[실패] unmeasured-runtime.json", done.stdout)
        self.assertIn("measured_on", done.stdout)
        self.assertNotIn("[실패] good-runtime.json", done.stdout)

    def test_passing_dir_exits_zero(self) -> None:
        """QA-SYS-9: 보조 검사 — 측정 기록을 갖춘 supported 프로필과 비활성 프로필만 있는 폴더는 게이트를 통과한다(종료 코드 0)."""
        _write(self.models, "good-runtime.json", _image_profile(supported=True, measured_on=_measured()))
        _write(self.models, "parked-runtime.json", _image_profile(supported=False, measured_on=None))
        done = self._run_script()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("측정 게이트 통과", done.stdout)

    def test_missing_dir_exits_two(self) -> None:
        """QA-SYS-9: 보조 검사 — 없는 프로필 폴더는 사용 오류(종료 코드 2)."""
        done = subprocess.run(
            [sys.executable, str(GATE_SCRIPT), "--models-dir", str(self.models / "nope")],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(done.returncode, 2)

    def test_threshold_boundaries(self) -> None:
        """QA-SYS-9: 보조 검사 — n_pos·n_neg 하한, 정수 여부, AUROC CI 하한, split, manifest_sha256 형식 위반을 각각 실패로 보고한다."""
        cases = {
            "n-pos-low": (_measured(n_pos=MIN_PER_CLASS - 1), "n_pos"),
            "n-neg-low": (_measured(n_neg=MIN_PER_CLASS - 1), "n_neg"),
            "n-float": (_measured(n_pos=250.0), "정수"),
            "ci-low": (_measured(auroc_ci=[MIN_AUROC_CI_LOW - 0.001, 0.99]), "신뢰구간 하한"),
            "ci-bad": (_measured(auroc_ci="0.9"), "auroc_ci"),
            "val-split": (_measured(split="val"), "split"),
            "bad-sha": (_measured(manifest_sha256="ABC"), "manifest_sha256"),
            "upper-sha": (_measured(manifest_sha256="A" * 64), "manifest_sha256"),
        }
        for name, (record, needle) in cases.items():
            with self.subTest(name):
                path = _write(self.models, f"{name}-runtime.json", _image_profile(measured_on=record))
                problems = check_profile(path)
                self.assertTrue(problems)
                self.assertTrue(any(needle in problem for problem in problems), problems)
        exact = _write(self.models, "exact-runtime.json", _image_profile(measured_on=_measured(n_pos=MIN_PER_CLASS, n_neg=MIN_PER_CLASS, auroc_ci=[MIN_AUROC_CI_LOW, 0.9])))
        self.assertEqual(check_profile(exact), [])

    def test_measured_on_must_point_at_an_existing_matching_manifest(self) -> None:
        """QA-SYS-9: 보조 검사 — D16: a well-formed but invented record (all-zero manifest hash, no
        file) fails; so do a missing/edited/other-corpus manifest and class
        counts the manifest's test split cannot supply."""
        edited = json.loads(json.dumps(FIXTURE_MANIFEST))
        edited["items"][0]["source_note"] = "edited after hashing"  # changed after its hash was taken
        (self.models / "corpus" / "edited.json").write_text(json.dumps(edited), encoding="utf-8")
        other = _manifest_payload(corpus_id="other-corpus")
        (self.models / "corpus" / "other.json").write_text(json.dumps(other), encoding="utf-8")
        small = _manifest_payload(n_pos=MIN_PER_CLASS, n_neg=MIN_PER_CLASS)
        (self.models / "corpus" / "small.json").write_text(json.dumps(small), encoding="utf-8")
        (self.models / "corpus" / "not-a-manifest.json").write_text('{"schema": "x"}', encoding="utf-8")
        cases = {
            "forged-zero": (_measured(manifest_sha256="0" * 64, manifest_path="corpus/missing.json"), "파일이 없습니다"),
            "zero-sha-real-file": (_measured(manifest_sha256="0" * 64), "해시"),
            "no-path": (_measured(manifest_path=""), "manifest_path"),
            "edited": (_measured(manifest_path="corpus/edited.json"), "편집된 매니페스트"),
            "other-corpus": (_measured(manifest_path="corpus/other.json", manifest_sha256=str(other["manifest_sha256"])), "corpus_id"),
            "too-few": (_measured(manifest_path="corpus/small.json", manifest_sha256=str(small["manifest_sha256"])), "항목 수"),
            "not-manifest": (_measured(manifest_path="corpus/not-a-manifest.json"), "corpus-manifest-v1"),
        }
        for name, (record, needle) in cases.items():
            with self.subTest(name):
                path = _write(self.models, f"{name}-runtime.json", _image_profile(measured_on=record))
                problems = check_profile(path)
                self.assertTrue(any(needle in problem for problem in problems), problems)
        absolute = _write(self.models, "abs-runtime.json", _image_profile(measured_on=_measured(manifest_path=str(self.manifest))))
        self.assertEqual(check_profile(absolute), [])
        missing_key = _measured()
        del missing_key["manifest_path"]
        partial = _write(self.models, "nopath-runtime.json", _image_profile(measured_on=missing_key))
        self.assertTrue(any("manifest_path" in problem for problem in check_profile(partial)))

    def test_missing_measured_on_key(self) -> None:
        """QA-SYS-9: 보조 검사 — measured_on에 필수 키(measured_at)가 빠지면 실패로 보고한다."""
        record = _measured()
        del record["measured_at"]
        path = _write(self.models, "partial-runtime.json", _image_profile(measured_on=record))
        self.assertTrue(any("measured_at" in problem for problem in check_profile(path)))

    def test_text_profile_rule(self) -> None:
        """QA-SYS-9: 보조 검사 — 텍스트 프로필은 AUROC 하한 대신 recall_at_fpr_0_01과 표본 수로 검사한다."""
        text = {"type": "deepfake-lens-runtime-profile-v1", "runtime": "hf-text-classifier", "hub_model": "x/y"}
        # No AUROC floor for text, but recall@FPR1% must be reported.
        low_auc = _measured(auroc=0.6, auroc_ci=[0.5, 0.7])
        without = _write(self.models, "text-a-runtime.json", {**text, "measured_on": low_auc})
        self.assertTrue(any("recall_at_fpr_0_01" in problem for problem in check_profile(without)))
        with_recall = _write(self.models, "text-b-runtime.json", {**text, "measured_on": {**low_auc, "recall_at_fpr_0_01": 0.1}})
        self.assertEqual(check_profile(with_recall), [])
        few = _write(self.models, "text-c-runtime.json", {**text, "measured_on": {**low_auc, "recall_at_fpr_0_01": 0.1, "n_neg": 10}})
        self.assertTrue(check_profile(few))

    def test_unsupported_and_unreadable(self) -> None:
        """QA-SYS-9: 보조 검사 — supported:false 프로필은 건너뛰고 읽을 수 없는 프로필은 실패로 보고한다."""
        parked = _write(self.models, "parked-runtime.json", _image_profile(supported=False))
        self.assertEqual(check_profile(parked), [])
        broken = self.models / "broken-runtime.json"
        broken.write_text("{not json", encoding="utf-8")
        self.assertTrue(check_profile(broken))
        listy = _write(self.models, "list-runtime.json", [1, 2])
        self.assertTrue(check_profile(listy))
        results = check_models_dir(self.models)
        self.assertEqual(set(results), {"parked-runtime.json", "broken-runtime.json", "list-runtime.json"})
        lines, passed = gate_report_lines(results)
        self.assertFalse(passed)
        self.assertIn("측정 게이트 실패", lines[-1])


# ============================================================================
# Moved from deepfake_lens/tests/qa/test_qa_sys_integrity.py (W2): module docstring above.
# ============================================================================


KEY = b"qa-sys-6-report-key"
PIN_SHA = "0123456789abcdef" * 4
SECRET = b"QA-SYS-7-SECRET-BYTES-do-not-serve"


def _write_generator_png(path: Path) -> None:
    """A PNG whose A1111 `parameters` chunk is strong deterministic evidence."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("parameters", "a portrait photo\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, Model: sd_xl_base_1.0")
    image = Image.new("RGB", (160, 160))
    image.putdata([((x * 7) % 256, (y * 5) % 256, (x * y) % 256) for y in range(160) for x in range(160)])
    image.save(path, pnginfo=info)


def _write_secret_png(path: Path) -> None:
    """A real PNG followed by marker bytes — the bytes a leak would carry."""
    from PIL import Image

    Image.new("RGB", (32, 32), (200, 10, 10)).save(path)
    with path.open("ab") as handle:
        handle.write(SECRET)


# The web forensic PDF needs pymupdf (Korean font). Without it /api/report
# answers a Korean JSON error with HTTP 501 — there is no Latin-1 fallback
# PDF any more (B8).
HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None


def _pdf_text(pdf: bytes) -> str:
    """Text of a rendered PDF (pymupdf extraction; only called with pymupdf)."""
    from deepfake_lens.pdf_backend import import_pymupdf

    with import_pymupdf().open(stream=pdf, filetype="pdf") as doc:
        return "".join(page.get_text() for page in doc)


def _assert_korean_pdf_error(test: unittest.TestCase, rendered: object) -> None:
    """B8: the web PDF without pymupdf is exactly the Korean error body (501), no PDF."""
    test.assertIsInstance(rendered, dict, "without pymupdf no PDF bytes may be produced")
    assert isinstance(rendered, dict)
    test.assertEqual(rendered, {"error": webapp_api.PDF_REPORT_UNAVAILABLE_ERROR})
    test.assertEqual(rendered["error"], "PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다(설치: pip install pymupdf).")
    test.assertEqual(webapp_api.report_error_status(rendered), 501)


def _leaves(node: Any, prefix: tuple[Any, ...] = ()) -> Iterator[tuple[tuple[Any, ...], Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, prefix + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaves(value, prefix + (index,))
    else:
        yield prefix, node


def _flip(value: Any) -> Any:
    """Change exactly one character (or the smallest unit) of a leaf."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if value is None:
        return "x"
    text = str(value)
    if not text:
        return "x"
    return ("b" if text[0] == "a" else "a") + text[1:]


def _set(node: Any, path: tuple[Any, ...], value: Any) -> None:
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


class QaSys6SignatureCoversWholeReportTest(unittest.TestCase):
    """QA-SYS-6: 보고서 JSON의 임의 필드(결론, 근거, note, 모델 해시) 한 글자 변경 후 검증 → 모든 경우 "변조됨"."""

    report: dict[str, Any]
    signed: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_generator_png(root / "generated.png")
            (root / "memo.txt").write_text("회의 메모: 다음 주 일정 확인", encoding="utf-8")
            summary, items = scan_directory(root)
        cls.report = scan_to_json(summary, items)
        cls.signed = sign_report(cls.report, KEY, model_pins=[
            {"profile": "aide-runtime", "pin": {"sha256": PIN_SHA}},
            {"profile": "sbi-effnet-runtime", "pin": None},
        ])

    def test_signed_report_verifies(self) -> None:
        """QA-SYS-6: 보조 검사 — an untouched signed report verifies."""
        result = verify_report(json.loads(json.dumps(self.signed)), KEY)
        self.assertTrue(result.verified, result)
        self.assertEqual(result.reason, "검증됨")

    def test_named_fields_are_inside_the_signature(self) -> None:
        """QA-SYS-6: 보고서 JSON의 임의 필드(결론, 근거, note, 모델 해시) 한 글자 변경 후 검증 → 모든 경우 "변조됨".

        Verdict, evidence, note, model pin, item sha256 and tool version
        each flip to 변조됨; test_every_field_is_inside_the_signature covers
        every other leaf.
        """
        generated = next(i for i, item in enumerate(self.signed["items"]) if item["name"] == "generated.png")
        item = self.signed["items"][generated]
        self.assertEqual(item["result"]["verdict_code"], "manipulation_evidence")
        self.assertTrue(item["result"]["evidence"])
        self.assertTrue(item["sha256"])
        named = {
            "결론(verdict_code)": ("items", generated, "result", "verdict_code"),
            "결론 문장(verdict)": ("items", generated, "result", "verdict"),
            "근거 제목": ("items", generated, "result", "evidence", 0, "title"),
            "근거 종류": ("items", generated, "result", "evidence", 0, "kind"),
            "note(signature_note)": ("signature_note",),
            "모델 해시(model_pins sha256)": ("model_pins", 0, "pin", "sha256"),
            "모델 프로필 이름": ("model_pins", 1, "profile"),
            "파일 해시(item sha256)": ("items", generated, "sha256"),
            "도구 버전": ("tool_version",),
        }
        for label, path in named.items():
            with self.subTest(field=label):
                tampered = json.loads(json.dumps(self.signed))
                node: Any = tampered
                for key in path:
                    node = node[key]
                _set(tampered, path, _flip(node))
                result = verify_report(tampered, KEY)
                self.assertEqual(result.status, "tampered")
                self.assertEqual(result.reason, "변조됨")

    def test_every_field_is_inside_the_signature(self) -> None:
        """QA-SYS-6: 보조 검사 — a one-character change to any leaf except signature/key id is 변조됨."""
        leaves = [path for path, _ in _leaves(self.signed) if path[0] not in {"signature", "signature_key_id"}]
        self.assertGreater(len(leaves), 50)
        for path in leaves:
            tampered = json.loads(json.dumps(self.signed))
            node: Any = tampered
            for key in path:
                node = node[key]
            _set(tampered, path, _flip(node))
            result = verify_report(tampered, KEY)
            self.assertEqual(result.reason, "변조됨", path)

    def test_key_id_swap_is_its_own_reason(self) -> None:
        """QA-SYS-6: 보조 검사 — a swapped key id is reported as 키 ID 불일치, distinct from 변조됨."""
        swapped = dict(self.signed, signature_key_id="hmac-sha256-v1:000000000000")
        self.assertEqual(verify_report(swapped, KEY).reason, "키 ID 불일치")

    def test_signed_json_file_from_cli_detects_note_edit(self) -> None:
        """QA-SYS-6: 보조 검사 — `scan --sign --json-out` file with one note character changed is 변조됨."""
        from deepfake_lens.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            root.mkdir()
            (root / "note.txt").write_text("plain note", encoding="utf-8")
            out = Path(tmp) / "report.json"
            with patch.dict(os.environ, {REPORT_KEY_ENV: KEY.decode()}), patch("sys.stdout"):
                self.assertEqual(main(["scan", str(root), "--no-default-engine", "--json-out", str(out), "--sign", "--format", "json"]), 0)
            self.assertTrue(verify_report(out, KEY).verified)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("model_pins", payload)
            self.assertIn("tool_version", payload)
            payload["signature_note"] = _flip(payload["signature_note"])
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.assertEqual(verify_report(out, KEY).reason, "변조됨")

    def test_web_report_signed_with_env_key_and_tamper_detected(self) -> None:
        """QA-SYS-6: 보조 검사 — /api/report HTML and JSON are signed with DEEPFAKE_LENS_REPORT_KEY."""
        body = json.dumps({"items": self.report["items"]}).encode("utf-8")
        with patch.object(webapp_api, "_READ_ROOTS", OrderedDict()), patch.dict(os.environ, {REPORT_KEY_ENV: KEY.decode()}):
            html = webapp_api._report_payload(body, "html")
            signed_json = webapp_api._report_payload(body, "json")
        assert isinstance(html, bytes) and isinstance(signed_json, dict)
        embedded: Any = extract_signed_report(html.decode("utf-8"))
        assert embedded is not None
        self.assertTrue(verify_report(embedded, KEY).verified)
        self.assertIn("보고서 서명: HMAC-SHA256 서명됨", html.decode("utf-8"))
        self.assertTrue(verify_report(signed_json, KEY).verified)
        self.assertIn("model_pins", signed_json)
        embedded["items"][0]["result"]["verdict_code"] = _flip(embedded["items"][0]["result"]["verdict_code"])
        self.assertEqual(verify_report(embedded, KEY).reason, "변조됨")

    def test_web_report_without_key_says_unsigned(self) -> None:
        """QA-SYS-6: 보조 검사 — without a key the web report states 서명 없음 explicitly."""
        body = json.dumps({"items": self.report["items"]}).encode("utf-8")
        env = {k: v for k, v in os.environ.items() if k != REPORT_KEY_ENV}
        with patch.object(webapp_api, "_READ_ROOTS", OrderedDict()), patch.dict(os.environ, env, clear=True):
            html = webapp_api._report_payload(body, "html")
            pdf = webapp_api._report_payload(body, "pdf")
        assert isinstance(html, bytes)
        self.assertIn("서명 없음", html.decode("utf-8"))
        embedded: Any = extract_signed_report(html.decode("utf-8"))
        assert embedded is not None
        self.assertIsNone(embedded["signature"])
        self.assertEqual(verify_report(embedded, KEY).reason, "서명 없음")
        if HAVE_PYMUPDF:
            assert isinstance(pdf, bytes), pdf
            text = _pdf_text(pdf)
            self.assertIn("보고서 서명: 서명 없음", text)
            self.assertNotIn("UNSIGNED", text)
        else:
            # B8: without pymupdf no (English, Latin-1) PDF is produced.
            _assert_korean_pdf_error(self, pdf)

    def test_web_pdf_report_is_korean_and_its_signature_verifies(self) -> None:
        """QA-SYS-6: 보조 검사 — /api/report?format=pdf is the Korean forensic PDF carrying a verifying signature (pymupdf), else the Korean 501 error.

        With pymupdf (API venv) the PDF prints the HMAC signature and the
        signed body's SHA-256 of exactly the body /api/report signed, and that
        body verifies with the key. Without pymupdf the route returns only
        the Korean error — never a Latin-1 English PDF (B8).
        """
        from deepfake_lens import reports
        from deepfake_lens.signing import signed_body_sha256

        body = json.dumps({"items": self.report["items"]}).encode("utf-8")
        captured: list[dict[str, Any]] = []
        real_sign = reports.signed_report_body

        def _capture(*args: Any, **kwargs: Any) -> dict[str, object]:
            signed_body = real_sign(*args, **kwargs)
            captured.append(dict(signed_body))
            return signed_body

        with patch.object(webapp_api, "_READ_ROOTS", OrderedDict()), patch.dict(os.environ, {REPORT_KEY_ENV: KEY.decode()}), \
                patch.object(reports, "signed_report_body", _capture):
            pdf = webapp_api._report_payload(body, "pdf")
        if not HAVE_PYMUPDF:
            _assert_korean_pdf_error(self, pdf)
            return
        assert isinstance(pdf, bytes), pdf
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertEqual(len(captured), 1)
        signed_body = captured[0]
        self.assertEqual(signed_body["report_format"], "pdf")
        self.assertTrue(verify_report(json.loads(json.dumps(signed_body)), KEY).verified)
        text = _pdf_text(pdf)
        self.assertIn("디지털포렌식 감정센터", text)
        self.assertIn(f"보고서 서명: HMAC-SHA256 서명됨 — 키 ID {signed_body['signature_key_id']}", text)
        self.assertIn(str(signed_body["signature"]), text.replace("\n", ""))
        self.assertIn(f"서명 본문 SHA-256: {signed_body_sha256(signed_body)}", text)
        for english in ("UNSIGNED", "Signature:", "Latin-1", "UNDETERMINED"):
            self.assertNotIn(english, text)

    def test_web_pdf_without_pymupdf_is_korean_501(self) -> None:
        """QA-SYS-6: 보조 검사 — with pymupdf made unimportable, /api/report?format=pdf on the live web server is HTTP 501 + the Korean error and no PDF (B8)."""
        from deepfake_lens import webapp
        from deepfake_lens.webapp import CLIENT_HEADER

        def _missing() -> Any:
            raise ImportError("pymupdf")

        with patch.object(webapp_api, "_READ_ROOTS", OrderedDict()), patch("deepfake_lens.pdf_backend.import_pymupdf", _missing):
            self.assertEqual(webapp_api._report_payload(json.dumps({"items": self.report["items"]}).encode("utf-8"), "pdf"),
                             {"error": webapp_api.PDF_REPORT_UNAVAILABLE_ERROR})
            with tempfile.TemporaryDirectory() as tmp:
                server = webapp.build_server("127.0.0.1", 0, default_folder=Path(tmp))
                thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
                thread.start()
                try:
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{server.server_address[1]}/api/report?format=pdf",
                        data=json.dumps({"items": self.report["items"]}).encode("utf-8"),
                        headers={CLIENT_HEADER: "qa", "Content-Type": "application/json"},
                        method="POST",
                    )
                    with self.assertRaises(urllib.error.HTTPError) as ctx:
                        urllib.request.urlopen(req, timeout=30)
                    status, ctype, payload = ctx.exception.code, ctx.exception.headers.get("Content-Type", ""), ctx.exception.read()
                finally:
                    server.shutdown()
                    server.server_close()
        self.assertEqual(status, 501)
        self.assertIn("application/json", ctype)
        self.assertFalse(payload.startswith(b"%PDF"))
        self.assertEqual(json.loads(payload.decode("utf-8")), {"error": "PDF 보고서를 만들려면 pymupdf 패키지가 필요합니다(설치: pip install pymupdf)."})


class _ServerFixture(unittest.TestCase):
    """A live stdlib web server whose only read root is ``self.root``."""

    def setUp(self) -> None:
        from deepfake_lens import webapp

        roots: Any = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.root = base / "case"
        self.outside = base / "elsewhere"
        self.root.mkdir()
        self.outside.mkdir()
        # Heatmaps go to the tool-owned output root (R-IN-1), kept in the
        # temp dir here instead of ~/.cache.
        self.heatmaps = base / "heatmap-root"
        env = patch.dict(os.environ, {"DEEPFAKE_LENS_HEATMAP_DIR": str(self.heatmaps)})
        env.start()
        self.addCleanup(env.stop)
        (self.root / "memo.txt").write_text("사건 메모", encoding="utf-8")
        _write_secret_png(self.outside / "secret.png")
        (self.outside / "secret.txt").write_bytes(SECRET)
        server = webapp.build_server("127.0.0.1", 0, default_folder=self.root)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    def request(self, path: str, body: dict[str, object] | None = None) -> tuple[int, bytes]:
        from deepfake_lens.webapp import CLIENT_HEADER

        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {CLIENT_HEADER: "qa"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def assertNoSecret(self, body: bytes) -> None:
        self.assertNotIn(SECRET, body)
        self.assertNotIn(base64.b64encode(SECRET)[:24], body)
        png_head = (self.outside / "secret.png").read_bytes()[:64]
        self.assertNotIn(base64.b64encode(png_head)[:40], body)

    def assertDenied(self, status: int, body: bytes) -> None:
        self.assertEqual(status, 403, body[:200])
        self.assertNoSecret(body)


class QaSys7ReadRootConfinementTest(_ServerFixture):
    """QA-SYS-7: /api/scan?folder=/ 등 등록되지 않은 경로로 요청. heatmap_path를 외부 파일로 지정한 report 요청 → 모두 403, 응답에 파일 내용 0바이트."""

    def _scan_rows_with_heatmap(self) -> list[dict[str, Any]]:
        _write_generator_png(self.root / "photo.png")
        _, items = scan_directory(self.root, pixel_mode="deep", heatmaps=True)
        rows: list[dict[str, Any]] = [item.to_json() for item in items]
        with_heatmap = [row for row in rows if (((row.get("result") or {}).get("pixel_analysis") or {}).get("heatmap_path"))]
        self.assertTrue(with_heatmap, "fixture must produce a heatmap")
        for row in with_heatmap:
            heatmap = Path(row["result"]["pixel_analysis"]["heatmap_path"])
            self.assertTrue(heatmap.is_relative_to(self.heatmaps), "heatmaps never land in the evidence folder (R-IN-1)")
        return rows

    def test_unregistered_scan_and_outside_heatmap_report_are_403_with_no_bytes(self) -> None:
        """QA-SYS-7: /api/scan?folder=/ 등 등록되지 않은 경로로 요청. heatmap_path를 외부 파일로 지정한 report 요청 → 모두 403, 응답에 파일 내용 0바이트.

        Both halves in one test; the sibling QA-SYS-7 tests add traversal,
        async scans, analyze-file, preview/heatmap and the api-serve file
        endpoints (test_servers.ApiServerFilePathConfinementTest).
        """
        for query in ("folder=/", f"folder={self.outside}", f"folder={self.outside}&async=1"):
            with self.subTest(scan=query):
                status, body = self.request("/api/scan?" + query + "&no_default_engine=true")
                self.assertDenied(status, body)
                self.assertEqual(json.loads(body), {"error": "허용되지 않은 경로"})
        rows = self._scan_rows_with_heatmap()
        for row in rows:
            pixel = ((row.get("result") or {}).get("pixel_analysis") or {})
            if pixel.get("heatmap_path"):
                pixel["heatmap_path"] = str(self.outside / "secret.png")
        for fmt in ("html", "pdf", "json"):
            with self.subTest(report=fmt):
                self.assertDenied(*self.request(f"/api/report?format={fmt}", {"items": rows}))

    def test_scan_of_filesystem_root_is_403(self) -> None:
        """QA-SYS-7: 보조 검사 — GET /api/scan?folder=/ → 403 {"error": "허용되지 않은 경로"}."""
        status, body = self.request("/api/scan?folder=/&no_default_engine=true")
        self.assertDenied(status, body)
        self.assertEqual(json.loads(body)["error"], "허용되지 않은 경로")

    def test_scan_outside_and_traversal_and_async_are_403(self) -> None:
        """QA-SYS-7: 보조 검사 — outside folder, ../ traversal and async scans are all 403."""
        for query in (
            f"folder={self.outside}",
            f"folder={self.root}/../{self.outside.name}",
            f"folder={self.outside}&async=1",
        ):
            with self.subTest(query=query):
                self.assertDenied(*self.request("/api/scan?" + query + "&no_default_engine=true"))

    def test_request_cannot_register_a_root(self) -> None:
        """QA-SYS-7: 보조 검사 — a refused scan registers nothing; the outside file stays unreadable."""
        self.request(f"/api/scan?folder={self.outside}&no_default_engine=true")
        self.assertEqual(list(webapp_api._READ_ROOTS), [self.root])
        self.assertDenied(*self.request(f"/api/heatmap?path={self.outside / 'secret.png'}&root={self.outside}"))
        self.assertDenied(*self.request(f"/api/preview?path={self.outside / 'secret.png'}&root={self.outside}"))

    def test_scan_inside_root_still_works(self) -> None:
        """QA-SYS-7: 보조 검사 — control: the registered root scans normally."""
        status, body = self.request(f"/api/scan?folder={self.root}&no_default_engine=true")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["summary"]["total"], 1)

    def test_analyze_file_outside_root_is_403(self) -> None:
        """QA-SYS-7: 보조 검사 — /api/analyze-file for a file outside the roots → 403."""
        self.assertDenied(*self.request(f"/api/analyze-file?file={self.outside / 'secret.txt'}"))

    def test_report_with_outside_heatmap_is_403_in_every_format(self) -> None:
        """QA-SYS-7: 보조 검사 — report POST whose heatmap_path points outside → 403 for html, pdf and json."""
        rows = self._scan_rows_with_heatmap()
        for row in rows:
            pixel = ((row.get("result") or {}).get("pixel_analysis") or {})
            if pixel.get("heatmap_path"):
                pixel["heatmap_path"] = str(self.outside / "secret.png")
        for fmt in ("html", "pdf", "json"):
            with self.subTest(format=fmt):
                self.assertDenied(*self.request(f"/api/report?format={fmt}", {"items": rows}))

    def test_report_with_inside_heatmap_renders_it(self) -> None:
        """QA-SYS-7: 보조 검사 — control: a heatmap under the root is still inlined."""
        rows = self._scan_rows_with_heatmap()
        status, body = self.request("/api/report?format=html", {"items": rows})
        self.assertEqual(status, 200)
        self.assertIn(b"data:image/png;base64", body)
        self.assertNoSecret(body)

    def test_tool_owned_heatmap_is_served_but_nothing_else_there(self) -> None:
        """QA-SYS-7: 보조 검사 — heatmap root: /api/heatmap serves a tool-written *.heatmap.png
        from the heatmap output root; any other file placed there, and the same
        name outside it, stays 403."""
        rows = self._scan_rows_with_heatmap()
        heatmap = next(Path(row["result"]["pixel_analysis"]["heatmap_path"]) for row in rows
                       if ((row.get("result") or {}).get("pixel_analysis") or {}).get("heatmap_path"))
        status, body = self.request(f"/api/heatmap?path={heatmap}&root={self.root}")
        self.assertEqual(status, 200)
        self.assertEqual(body, heatmap.read_bytes())
        planted = heatmap.parent / "planted.png"
        planted.write_bytes((self.outside / "secret.png").read_bytes())
        self.assertDenied(*self.request(f"/api/heatmap?path={planted}&root={self.root}"))
        renamed = self.outside / heatmap.name
        renamed.write_bytes((self.outside / "secret.png").read_bytes())
        self.assertDenied(*self.request(f"/api/heatmap?path={renamed}&root={self.root}"))

    def test_report_does_not_hash_outside_files(self) -> None:
        """QA-SYS-7: 보조 검사 — an item path outside the roots is never read — its sha256 stays null."""
        # N11: the row carries the contract's required "result" (null for a
        # failed row) — /api/report refuses rows outside the item contract.
        rows = [{"path": str(self.outside / "secret.txt"), "name": "secret.txt", "kind": "text", "status": "failed", "size_bytes": 0, "result": None, "sha256": "f" * 64}]
        status, body = self.request("/api/report?format=json", {"items": rows})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIsNone(payload["items"][0]["sha256"])
        self.assertNoSecret(body)


class QaSys7ReadRootUnitTest(unittest.TestCase):
    """QA-SYS-7: read-root registry rules behind the HTTP checks."""

    def setUp(self) -> None:
        roots: Any = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)

    def test_no_registered_root_means_default_folder_only(self) -> None:
        """QA-SYS-7: 보조 검사 — with nothing registered only the default folder is readable — never everything."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            self.assertEqual(webapp_api._require_read_root(folder, folder), folder)
            with self.assertRaises(webapp_api.ReadRootDenied):
                webapp_api._require_read_root(Path("/"), folder)
            with self.assertRaises(webapp_api.ReadRootDenied):
                webapp_api._scan_payload("folder=/", default_folder=folder)

    def test_oldest_root_is_evicted_first(self) -> None:
        """QA-SYS-7: 보조 검사 — the registry evicts the oldest registration, not an arbitrary one."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            dirs = [base / f"r{index:03d}" for index in range(webapp_api._READ_ROOTS_MAX + 1)]
            for directory in dirs:
                directory.mkdir()
                webapp_api._register_read_root(directory)
            registered = list(webapp_api._READ_ROOTS)
        self.assertEqual(len(registered), webapp_api._READ_ROOTS_MAX)
        self.assertNotIn(dirs[0], registered)
        self.assertEqual(registered[0], dirs[1])
        self.assertEqual(registered[-1], dirs[-1])

    def test_allow_root_cli_flag_is_repeatable(self) -> None:
        """QA-SYS-7: 보조 검사 — `web --allow-root A --allow-root B` registers both at server setup."""
        from deepfake_lens.cli_parser import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["web", "--folder", "/case", "--allow-root", "/a", "--allow-root", "/b"])
        self.assertEqual(args.allow_root, [Path("/a"), Path("/b")])
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            (base / "a").mkdir()
            (base / "b").mkdir()
            webapp_api.configure_read_roots(base / "a", [base / "b"])
            self.assertEqual(list(webapp_api._READ_ROOTS), [base / "a", base / "b"])

    def test_heatmap_outside_allowed_paths_is_a_placeholder(self) -> None:
        """QA-SYS-7: 보조 검사 — reports._heatmap_img never reads a rejected heatmap path."""
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "secret.png"
            _write_secret_png(secret)
            html = _heatmap_img(str(secret), allow_path=lambda _path: False)
            self.assertNotIn("base64", html)
            self.assertNotIn("secret", html)
            self.assertIn(HEATMAP_PLACEHOLDER, html)
            self.assertIn("base64", _heatmap_img(str(secret), allow_path=lambda _path: True))


# ============================================================================
# Moved from deepfake_lens/tests/qa/test_qa_traceability.py (W2): module docstring above.
# ============================================================================


class TraceabilityTest(unittest.TestCase):
    data: dict[str, Any]
    criteria: dict[str, str]
    tests: list[unittest.TestCase]

    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_traceability()
        cls.criteria = automated_criteria(cls.data)
        suite = unittest.TestLoader().discover(str(TESTS_DIR), top_level_dir=str(REPO_ROOT))
        cls.tests = [test for test in iter_tests(suite) if not test.id().startswith("unittest.loader")]

    def test_loader_found_the_suite(self) -> None:
        """QA-SYS-10: 보조 검사 — QA 하네스 로더가 739개 넘는 테스트를 찾는다."""
        self.assertGreater(len(self.tests), 739)

    def test_exactly_one_canonical_test_per_automated_qa_id(self) -> None:
        """QA-SYS-10: 보조 검사 — 자동 QA ID마다 통과 기준 원문을 docstring 첫 줄로 가진 기준 테스트가 정확히 하나다."""
        found = canonical_tests(self.tests, self.criteria)
        self.assertEqual(len(self.criteria), 20)
        for qa_id, ids in sorted(found.items()):
            with self.subTest(qa=qa_id):
                self.assertEqual(len(ids), 1, f"{qa_id}: {ids or 'no test'} has the criterion as its docstring first line")

    def test_every_qa_test_docstring_starts_with_its_qa_id(self) -> None:
        """QA-SYS-10: 보조 검사 — G11: tests/qa의 모든 테스트 docstring 첫 줄은 "<QA-ID>: <통과 기준 원문>" 또는 "<QA-ID>: 보조 검사 — …"이다.

        Round 5 found 36 of 109 tests without a QA ID in that line (27 with
        no docstring at all). The QA ID must be an automated one from
        traceability.json; only the canonical test carries the criterion.
        """
        offenders: list[str] = []
        checked = 0
        for path in sorted(QA_DIR.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                for method in node.body:
                    if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) or not method.name.startswith("test"):
                        continue
                    checked += 1
                    where = f"{path.name}::{node.name}.{method.name}"
                    line = first_line(ast.get_docstring(method))
                    match = re.match(r"^(QA-[A-Z]+-\d+): (.+)$", line)
                    if match is None:
                        offenders.append(f"{where}: first line {line[:60]!r} has no QA ID")
                        continue
                    qa_id, rest = match.groups()
                    if qa_id not in self.criteria:
                        offenders.append(f"{where}: {qa_id} is not an automated QA ID")
                    elif rest != self.criteria[qa_id] and not rest.startswith(AUXILIARY_PREFIX):
                        offenders.append(f"{where}: neither the {qa_id} criterion nor {AUXILIARY_PREFIX!r}")
        self.assertGreaterEqual(checked, 110)
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_qa_package_is_exactly_the_four_area_files(self) -> None:
        """QA-SYS-10: 보조 검사 — W2 (WP-J): tests/qa holds exactly test_qa_in.py, test_qa_out.py, test_qa_adv.py and test_qa_sys.py."""
        names = sorted(path.name for path in (TESTS_DIR / "qa").glob("test*.py"))
        self.assertEqual(names, ["test_qa_adv.py", "test_qa_in.py", "test_qa_out.py", "test_qa_sys.py"])
        self.assertEqual(
            self.data["test_files"],
            {f"QA-{area}-*": f"deepfake_lens/tests/qa/test_qa_{area.lower()}.py" for area in ("IN", "OUT", "ADV", "SYS")},
        )

    def test_canonical_test_lives_in_its_area_file(self) -> None:
        """QA-SYS-10: 보조 검사 — W2: each automated QA ID's canonical test is in the file traceability.json names (IN-* → test_qa_in.py, …)."""
        found = canonical_tests(self.tests, self.criteria)
        for qa_id, ids in sorted(found.items()):
            with self.subTest(qa=qa_id):
                entry = self.data["qa"][qa_id]
                area = qa_id.split("-")[1].lower()
                self.assertEqual(entry["file"], f"deepfake_lens/tests/qa/test_qa_{area}.py")
                module = entry["file"].removesuffix(".py").replace("/", ".")
                self.assertEqual([test_id.rsplit(".", 2)[0] for test_id in ids], [module], ids)

    def test_tags_name_known_qa_ids(self) -> None:
        """QA-SYS-10: 보조 검사 — 테스트 태그는 traceability.json에 있는 QA ID만 쓴다."""
        known = set(self.data["qa"])
        for test in self.tests:
            tag = qa_tag(test)
            if tag is not None:
                with self.subTest(test=test.id()):
                    self.assertIn(tag, known)
                    self.assertEqual(self.data["qa"][tag]["mode"], "automated", "only automated QA IDs have tests")

    def test_requirement_rows_are_consistent(self) -> None:
        """QA-SYS-10: 보조 검사 — 요구사항·갭·QA ID 표의 행이 서로 일관된다."""
        known = set(self.data["qa"])
        backed: set[str] = set()
        for row in self.data["requirements"]:
            with self.subTest(requirement=row["id"]):
                self.assertTrue(set(row["qa"]) <= known, row)
                self.assertTrue(row["qa"] or row.get("phase1"), "a row needs a QA ID or a phase-1 note")
                self.assertTrue(all(gap.startswith("G") for gap in row["gaps"]))
                backed.update(row["qa"])
        self.assertEqual(sorted(set(self.criteria) - backed), [])

    def test_manual_checklists_exist(self) -> None:
        """QA-SYS-10: 보조 검사 — 수동 QA 항목마다 docs/QA-MANUAL.md 체크리스트가 있다."""
        manual_doc = (REPO_ROOT / "docs" / "QA-MANUAL.md").read_text(encoding="utf-8")
        for qa_id, entry in self.data["qa"].items():
            if entry["mode"] == "manual":
                with self.subTest(qa=qa_id):
                    self.assertIn(f"## {qa_id}", manual_doc)
                    self.assertTrue(entry["checklist"].startswith("docs/QA-MANUAL.md#"))
                    # W2: the record state is explicit — "미실시(기록 없음)" with an
                    # empty record table until someone performs the procedure.
                    section = manual_doc.split(f"## {qa_id}", 1)[1].split("\n## ", 1)[0]
                    self.assertIn("기록 상태:", section)
                    self.assertIn("| 수행자 |", section)
                    if "미실시(기록 없음)" in section:
                        self.assertIn(f"| {qa_id} | {entry['title']} | 미실시(기록 없음) |", manual_doc)


class HarnessLogicTest(unittest.TestCase):
    """scripts/qa_phase0.py result folding (no suite run)."""

    @classmethod
    def setUpClass(cls) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("qa_phase0_harness", REPO_ROOT / "scripts" / "qa_phase0.py")
        assert spec is not None and spec.loader is not None
        cls.harness = importlib.util.module_from_spec(spec)
        # dataclasses resolve annotations through sys.modules[__module__].
        sys.modules[spec.name] = cls.harness
        spec.loader.exec_module(cls.harness)
        cls.data = load_traceability()

    harness: Any
    data: dict[str, Any]

    def _records(self, **statuses: str) -> dict[str, Any]:
        records: dict[str, Any] = {}
        for name, status in statuses.items():
            qa_id = "QA-" + name.split("__")[0].replace("_", "-")
            records[name] = self.harness.TestRecord(name, qa_id, status=status)
        return records

    def test_failure_of_a_tagged_test_fails_the_qa_id_and_sys10(self) -> None:
        """QA-SYS-10: 보조 검사 — 태그된 테스트 하나가 실패하면 그 QA ID와 QA-SYS-10이 실패로 접힌다."""
        records = self._records(IN_1__canon="passed", IN_1__other="failed", SYS_10__canon="passed")
        outcomes = self.harness.qa_outcomes(records, {"QA-IN-1": ["IN_1__canon"], "QA-SYS-10": ["SYS_10__canon"]}, full_suite=True)
        self.assertEqual(outcomes["QA-IN-1"]["result"], "실패")
        self.assertEqual(outcomes["QA-SYS-10"]["result"], "실패", "any suite failure fails QA-SYS-10")

    def test_canonical_count_must_be_one_and_skips_are_not_passes(self) -> None:
        """QA-SYS-10: 보조 검사 — 기준 테스트는 정확히 하나여야 하고 건너뜀은 통과가 아니다."""
        records = self._records(IN_2__a="passed", IN_2__b="passed", IN_4__canon="skipped")
        outcomes = self.harness.qa_outcomes(records, {"QA-IN-2": ["IN_2__a", "IN_2__b"], "QA-IN-4": ["IN_4__canon"], "QA-IN-5": []}, full_suite=True)
        self.assertEqual(outcomes["QA-IN-2"]["result"], "실패")
        self.assertEqual(outcomes["QA-IN-4"]["result"], "건너뜀")
        self.assertEqual(outcomes["QA-IN-5"]["result"], "실패")

    def test_summary_and_rows_cover_every_requirement(self) -> None:
        """QA-SYS-10: 보조 검사 — 적합성 표의 요약과 행이 모든 요구사항을 덮는다."""
        criteria = automated_criteria(self.data)
        outcomes = {qa_id: {"result": "통과"} for qa_id in criteria}
        counts = self.harness.summary_counts(self.data, outcomes)
        self.assertEqual(self.harness.summary_line(counts), "20 통과 / 0 실패 / 4 수동 / 12 1단계")
        logs = {qa_id: REPO_ROOT / "build" / "qa-logs" / f"{qa_id}.log" for qa_id in criteria}
        rows = self.harness.conformance_rows(self.data, outcomes, logs)
        self.assertEqual({row[0] for row in rows} - {"—"}, {row["id"] for row in self.data["requirements"]})
        self.assertIn(("R-TXT-3", "G2, G25", "QA-SYS-9 (게이트)", "통과", "`build/qa-logs/QA-SYS-9.log`"), rows)
        self.assertIn(("R-IN-4", "—", "QA-IN-3", "수동", "`docs/QA-MANUAL.md#qa-in-3`"), rows)
        # QA-OUT-5 is a structural check in phase 0 and says so on every row.
        out5 = [row for row in rows if row[2].startswith("QA-OUT-5")]
        self.assertTrue(out5)
        self.assertTrue(all("구조 검사(0단계에 보정 모델 없음)" in row[2] for row in out5), out5)

    def test_recorded_run_requires_fastapi(self) -> None:
        """QA-SYS-10: 보조 검사 — D6/QA-OUT-4: without fastapi the harness exits 1 ("fastapi 필요")
        before running anything, instead of recording a skipped API leg."""
        import contextlib
        import io
        from unittest import mock

        real_find_spec = self.harness.importlib.util.find_spec

        def no_fastapi(name: str, *args: Any, **kwargs: Any) -> Any:
            return None if name in {"fastapi", "httpx"} else real_find_spec(name, *args, **kwargs)

        stderr = io.StringIO()
        with mock.patch.object(self.harness.importlib.util, "find_spec", no_fastapi), \
                mock.patch.object(self.harness, "load_suite", side_effect=AssertionError("suite must not run")), \
                contextlib.redirect_stderr(stderr):
            code = self.harness.main(["--out", str(REPO_ROOT / "build" / "never-written.md")])
        self.assertEqual(code, 1)
        self.assertIn("fastapi 필요", stderr.getvalue())
        self.assertIn("pip install fastapi httpx uvicorn", stderr.getvalue())

    def test_dirty_tree_is_refused_without_allow_dirty(self) -> None:
        """QA-SYS-10: 보조 검사 — 커밋되지 않은 변경이 있으면 --allow-dirty 없이는 기록 실행을 거부한다."""
        import contextlib
        import io
        from unittest import mock

        def fake_git(*args: str) -> str:
            return {"rev-parse": "a" * 40, "status": " M deepfake_lens/core.py"}[args[0]]

        stderr = io.StringIO()
        with mock.patch.object(self.harness.importlib.util, "find_spec", return_value=object()), \
                mock.patch.object(self.harness, "_git", fake_git), \
                mock.patch.object(self.harness, "load_suite", side_effect=AssertionError("suite must not run")), \
                contextlib.redirect_stderr(stderr):
            code = self.harness.main(["--out", str(REPO_ROOT / "build" / "never-written.md")])
        self.assertEqual(code, 1)
        self.assertIn("--allow-dirty", stderr.getvalue())

    def test_verify_record_checks_commit_and_later_changes(self) -> None:
        """QA-SYS-10: 보조 검사 — --verify-record가 기록의 커밋과 그 뒤 변경을 확인한다."""
        import contextlib
        import io
        import tempfile
        from unittest import mock

        from pathlib import Path

        parent, head = "b" * 40, "c" * 40
        out_rel = "docs/CONFORMANCE.md"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "CONFORMANCE.md"
            out.write_text(f"# x\n\n{self.harness.COMMIT_LINE_PREFIX}{parent}` — 변경 없는 작업 트리에서 실행.\n", encoding="utf-8")

            def run(diff: str) -> int:
                answers = {"HEAD": head, "HEAD^": parent}

                def fake_git(*args: str) -> str:
                    if args[0] == "rev-parse":
                        return answers[args[1]]
                    return diff

                with mock.patch.object(self.harness, "_git", fake_git), \
                        mock.patch.object(self.harness, "_rel", return_value=out_rel), \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    return int(self.harness.verify_record(out))

            self.assertEqual(run(out_rel), 0)
            self.assertEqual(run(f"{out_rel}\ndeepfake_lens/core.py"), 1)
            out.write_text(f"{self.harness.COMMIT_LINE_PREFIX}{'d' * 40}`\n", encoding="utf-8")
            self.assertEqual(run(out_rel), 1, "recorded commit is neither HEAD nor its parent")


if __name__ == "__main__":
    unittest.main()

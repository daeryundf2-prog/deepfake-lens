"""QA-SYS-9 — measurement gate (phase 0, WP-I: G26/G28).

The gate must reject any runtime profile that is active (``supported`` true
or absent) without a test-split measurement record meeting the thresholds,
independently of which profiles ship in ``deepfake_lens/models``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from deepfake_lens.measurement_gate import MIN_AUROC_CI_LOW, MIN_PER_CLASS, check_models_dir, check_profile, gate_report_lines

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_SCRIPT = REPO_ROOT / "scripts" / "check_measurement_gate.py"
MANIFEST_SHA = "a" * 64


def _measured(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "corpus_id": "t-img-test",
        "manifest_sha256": MANIFEST_SHA,
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
        _write(self.models, "good-runtime.json", _image_profile(supported=True, measured_on=_measured()))
        _write(self.models, "parked-runtime.json", _image_profile(supported=False, measured_on=None))
        done = self._run_script()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("측정 게이트 통과", done.stdout)

    def test_missing_dir_exits_two(self) -> None:
        done = subprocess.run(
            [sys.executable, str(GATE_SCRIPT), "--models-dir", str(self.models / "nope")],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
        self.assertEqual(done.returncode, 2)

    def test_threshold_boundaries(self) -> None:
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

    def test_missing_measured_on_key(self) -> None:
        record = _measured()
        del record["measured_at"]
        path = _write(self.models, "partial-runtime.json", _image_profile(measured_on=record))
        self.assertTrue(any("measured_at" in problem for problem in check_profile(path)))

    def test_text_profile_rule(self) -> None:
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


if __name__ == "__main__":
    unittest.main()

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

from deepfake_lens.corpus_manifest import SCHEMA, item_id, manifest_sha256
from deepfake_lens.measurement_gate import MIN_AUROC_CI_LOW, MIN_PER_CLASS, check_models_dir, check_profile, gate_report_lines

REPO_ROOT = Path(__file__).resolve().parents[3]
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

    def test_measured_on_must_point_at_an_existing_matching_manifest(self) -> None:
        """D16: a well-formed but invented record (all-zero manifest hash, no
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

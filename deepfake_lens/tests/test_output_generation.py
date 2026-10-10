"""R15-7 / R16-5: the output-format generation must change whenever the golden output does.

``scan_cache.OUTPUT_FORMAT_GENERATION`` keeps an older commit's cached row
from being replayed (R14-3); it was bumped by hand only. The golden output —
the normalised scan JSON of a fixed fixture folder, scanned by a stdlib-only
interpreter (``stdlib``) and by one with the site packages (``full-extras``)
— and the user-facing Korean constants of ``result_text``/``core`` are
hashed and recorded with their generation in ``golden_output.json``
(``golden_output.py``). A changed hash under the same generation fails; so
does a generation raised without recording the new golden (with its reason),
and a recorded generation whose hashes were later rewritten (checked against
the file's git history).

R16-5 (round 16): the stdlib-only golden alone let a changed
``core.NON_PHOTO_NOTICE`` through (no image is classified without
numpy/opencv), and it refused a raised generation whose output had not
changed. The tests below change ``NON_PHOTO_NOTICE`` in a copy of the
package and require both the constants hash and the full-extras golden to
see it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from deepfake_lens import scan_cache
from deepfake_lens.tests import golden_output
from deepfake_lens.tests.golden_output import (
    FULL_EXTRAS_SET,
    GOLDEN_PATH,
    REPO,
    STDLIB_SET,
    check,
    digest,
    history_problems,
    load_record,
    missing_full_extras,
    recorded_sets,
)

CHANGED_NOTICE = "사진 아님 — 생성 탐지 비적용(변경)"


def _copy_with_changed_notice(root: Path) -> Path:
    """A copy of the package (no models/tests) whose core.NON_PHOTO_NOTICE says something else."""
    target = root / "repo"
    shutil.copytree(REPO / "deepfake_lens", target / "deepfake_lens", ignore=shutil.ignore_patterns("__pycache__", "tests", "*.pth", "*.onnx", "*.task", "*.model"))
    core = target / "deepfake_lens" / "core.py"
    text = core.read_text(encoding="utf-8")
    original = f"NON_PHOTO_NOTICE = {json.dumps(golden_output_notice(), ensure_ascii=False)}"
    if original not in text:
        raise AssertionError("core.NON_PHOTO_NOTICE assignment not found")
    core.write_text(text.replace(original, f"NON_PHOTO_NOTICE = {json.dumps(CHANGED_NOTICE, ensure_ascii=False)}", 1), encoding="utf-8")
    return target


def golden_output_notice() -> str:
    from deepfake_lens.core import NON_PHOTO_NOTICE

    return NON_PHOTO_NOTICE


@unittest.skipIf(os.name == "nt", "golden output recorded on POSIX (path and OS error wording)")
class OutputGenerationMetaTest(unittest.TestCase):
    payload: Any
    digest: str
    constants: dict[str, Any]
    environment: dict[str, str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = golden_output.golden_payload(STDLIB_SET)
        cls.digest = digest(cls.payload)
        cls.constants = golden_output.user_constants()
        cls.environment = golden_output.environment()

    def _last(self) -> dict[str, Any]:
        return load_record()["history"][-1]

    def test_the_golden_output_belongs_to_the_current_generation(self) -> None:
        history = load_record()["history"]
        current = {STDLIB_SET: self.digest, "constants": digest(self.constants)}
        self.assertEqual(check(current, scan_cache.OUTPUT_FORMAT_GENERATION, history), "")
        # R16-5: the current generation's entry carries every hash (none is left out of the comparison).
        self.assertEqual(sorted(recorded_sets(history[-1])), sorted(golden_output.GOLDEN_SETS))
        self.assertTrue(history[-1].get("constants_sha256"))

    def test_the_full_extras_golden_belongs_to_the_current_generation(self) -> None:
        """R16-5: compared in the environment the full-extras golden was recorded in (described in the record)."""
        last = self._last()
        recorded = last.get("full_extras_environment")
        if self.environment != recorded:
            difference = "; ".join(
                f"{module}: {(recorded or {}).get(module, '없음')} → {self.environment.get(module, '없음')}"
                for module in sorted(set(recorded or {}) | set(self.environment))
                if (recorded or {}).get(module) != self.environment.get(module)
            )
            self.skipTest(f"full-extras 골든은 다른 환경에서 기록됨: {difference}")
        current = digest(golden_output.golden_payload(FULL_EXTRAS_SET))
        self.assertEqual(check({FULL_EXTRAS_SET: current}, scan_cache.OUTPUT_FORMAT_GENERATION, load_record()["history"]), "")

    def test_the_fixture_exercises_rows_of_every_kind(self) -> None:
        items = self.payload["items"]
        self.assertGreaterEqual(len(items), 13)
        verdicts = {(item.get("result") or {}).get("verdict_code") for item in items}
        self.assertTrue({"manipulation_evidence", "undetermined"} <= verdicts, verdicts)
        statuses = {entry["status"] for item in items for entry in (item.get("result") or {}).get("coverage", [])}
        self.assertTrue({"ran", "skipped", "failed"} <= statuses, statuses)
        self.assertTrue(any(item.get("container") for item in items))  # archive members

    def test_the_constants_hash_covers_the_user_facing_wording(self) -> None:
        """R16-5: NON_PHOTO_NOTICE and the other Hangul constants of result_text and core are hashed."""
        self.assertIn("deepfake_lens.core.NON_PHOTO_NOTICE", self.constants)
        self.assertIn("deepfake_lens.result_text.TEXT_LEGAL_LIMITATION", self.constants)
        self.assertEqual(self.constants["deepfake_lens.core.NON_PHOTO_NOTICE"], golden_output_notice())
        self.assertGreaterEqual(len(self.constants), 40)
        with tempfile.TemporaryDirectory() as tmp:
            copy = _copy_with_changed_notice(Path(tmp).resolve())
            changed = golden_output.user_constants(copy)
        self.assertEqual(changed["deepfake_lens.core.NON_PHOTO_NOTICE"], CHANGED_NOTICE)
        history = load_record()["history"]
        problem = check({"constants": digest(changed)}, scan_cache.OUTPUT_FORMAT_GENERATION, history)
        self.assertIn(f"OUTPUT_FORMAT_GENERATION이 {scan_cache.OUTPUT_FORMAT_GENERATION} 그대로입니다", problem)

    def test_the_full_extras_golden_sees_a_changed_non_photo_notice(self) -> None:
        """R16-5: the full-extras scan classifies the gradient fixture, so its golden changes with NON_PHOTO_NOTICE."""
        missing = ", ".join(missing_full_extras(self.environment))
        if missing:
            self.skipTest(f"full-extras 모듈 없음: {missing}")
        payload = golden_output.golden_payload(FULL_EXTRAS_SET)
        gradient = [item for item in payload["items"] if item.get("display_name") == "gradient.png"]
        self.assertEqual(len(gradient), 1)
        self.assertIn(golden_output_notice(), json.dumps(gradient[0], ensure_ascii=False))
        with tempfile.TemporaryDirectory() as tmp:
            copy = _copy_with_changed_notice(Path(tmp).resolve())
            changed = golden_output.golden_payload(FULL_EXTRAS_SET, repo=copy)
        self.assertNotEqual(digest(changed), digest(payload))
        self.assertIn(CHANGED_NOTICE, json.dumps(changed, ensure_ascii=False))
        # The stdlib golden cannot see it (no image is classified without numpy/opencv) — the defect R16-5 closed.
        self.assertNotIn(golden_output_notice(), json.dumps(self.payload, ensure_ascii=False))

    def test_the_check_fails_a_changed_output_under_the_same_generation(self) -> None:
        history = [{"generation": 7, "reason": "r", "sha256": "a" * 64, "sets": {STDLIB_SET: "a" * 64, FULL_EXTRAS_SET: "c" * 64}, "constants_sha256": "d" * 64}]
        changed = check({STDLIB_SET: "b" * 64}, 7, history)
        self.assertIn("OUTPUT_FORMAT_GENERATION이 7 그대로입니다", changed)
        self.assertIn("OUTPUT_FORMAT_GENERATION이 7 그대로입니다", check({FULL_EXTRAS_SET: "e" * 64}, 7, history))
        self.assertIn("OUTPUT_FORMAT_GENERATION이 7 그대로입니다", check({"constants": "e" * 64}, 7, history))
        self.assertIn("기록하십시오", check({STDLIB_SET: "b" * 64}, 8, history))  # raised, golden not recorded
        # R16-5: raised without an output change — not refused, but it must be recorded (with its reason) first.
        # (R15-7 treated it as an error: "출력은 그대로인데 세대가 7 → 8" — that blocked a legitimate raise.)
        self.assertIn("--reason", check({STDLIB_SET: "a" * 64}, 8, history))
        self.assertEqual(check({STDLIB_SET: "a" * 64, FULL_EXTRAS_SET: "c" * 64, "constants": "d" * 64}, 7, history), "")
        self.assertNotEqual(check({STDLIB_SET: "a" * 64}, 6, history), "")
        payload = json.loads(json.dumps(self.payload))
        payload["items"][0]["status"] = payload["items"][0]["status"] + "x"  # any change of any row
        self.assertNotEqual(digest(payload), self.digest)

    def test_the_history_only_grows(self) -> None:
        record = load_record()
        self.assertEqual(history_problems(record["history"]), [])
        full = {"sets": {STDLIB_SET: "a" * 64, FULL_EXTRAS_SET: "b" * 64}, "constants_sha256": "c" * 64}
        # R16-5: a later entry needs its reason and all three hashes; an unchanged hash is allowed.
        self.assertEqual(
            history_problems([{"generation": 3, "sha256": "a" * 64}, {"generation": 3, "sha256": "b" * 64}]),
            ["세대가 증가하지 않음: 3 → 3", "세대 3에 사유(reason)가 기록되지 않음", "세대 3에 빠진 골든: stdlib, full-extras, constants"],
        )
        self.assertEqual(history_problems([{"generation": 3, "sha256": "a" * 64}, {"generation": 4, "reason": "캐시 무효화", "sha256": "a" * 64, **full}]), [])

    def test_the_update_script_requires_a_reason_for_a_new_generation(self) -> None:
        """R16-5: scripts/update_golden_output.py records a raise only with --reason."""
        import contextlib
        import importlib.util
        import io
        from unittest import mock

        spec = importlib.util.spec_from_file_location("update_golden_output", REPO / "scripts" / "update_golden_output.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "golden_output.json"
            target.write_text(GOLDEN_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            with mock.patch.object(module, "GOLDEN_PATH", target), mock.patch.object(module, "OUTPUT_FORMAT_GENERATION", scan_cache.OUTPUT_FORMAT_GENERATION + 1):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(module.main([]), 2)
                self.assertIn("--reason", err.getvalue())
                if missing_full_extras(self.environment):
                    return
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(module.main(["--reason", "시험: 출력 변화 없는 세대 올림"]), 0)
            written = json.loads(target.read_text(encoding="utf-8"))["history"][-1]
        self.assertEqual(written["generation"], scan_cache.OUTPUT_FORMAT_GENERATION + 1)
        self.assertEqual(written["reason"], "시험: 출력 변화 없는 세대 올림")
        self.assertEqual(written["sets"][STDLIB_SET], self._last()["sets"][STDLIB_SET])  # unchanged output, raise recorded

    def test_no_recorded_generation_was_rewritten(self) -> None:
        git = shutil.which("git")
        if git is None:
            self.skipTest("git not available")
        inside = subprocess.run([git, "rev-parse", "--is-inside-work-tree"], cwd=REPO, capture_output=True, text=True, check=False)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            self.skipTest("not a git work tree")
        relative = GOLDEN_PATH.relative_to(REPO).as_posix()
        commits = subprocess.run([git, "log", "--format=%H", "HEAD", "--", relative], cwd=REPO, capture_output=True, text=True, check=False).stdout.split()
        current = {entry["generation"]: entry for entry in load_record()["history"]}
        for commit in commits:
            shown = subprocess.run([git, "show", f"{commit}:{relative}"], cwd=REPO, capture_output=True, text=True, check=False)
            if shown.returncode != 0:
                continue
            for entry in json.loads(shown.stdout)["history"]:
                with self.subTest(commit=commit[:12], generation=entry["generation"]):
                    # R16-5: the whole entry (sets, constants, reason), not only the stdlib sha256.
                    self.assertEqual(current.get(entry["generation"]), entry, "a recorded generation's golden was rewritten")


if __name__ == "__main__":
    unittest.main()

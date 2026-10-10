"""R15-7 (round 15): the output-format generation must change whenever the golden scan output does.

``scan_cache.OUTPUT_FORMAT_GENERATION`` keeps an older commit's cached row
from being replayed (R14-3); it was bumped by hand only. The golden output —
the normalised scan JSON of a fixed fixture folder, scanned by a stdlib-only
interpreter so it is the same in every environment (``golden_output.py``) —
is hashed and recorded with its generation in ``golden_output.json``. A
changed hash under the same generation fails; so does a generation bumped
without recording the new golden, and a recorded generation whose hash was
later rewritten (checked against the file's git history).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from typing import Any

from deepfake_lens import scan_cache
from deepfake_lens.tests import golden_output
from deepfake_lens.tests.golden_output import GOLDEN_PATH, REPO, check, digest, history_problems, load_record


@unittest.skipIf(os.name == "nt", "golden output recorded on POSIX (path and OS error wording)")
class OutputGenerationMetaTest(unittest.TestCase):
    payload: Any
    digest: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = golden_output.golden_payload()
        cls.digest = digest(cls.payload)

    def test_the_golden_output_belongs_to_the_current_generation(self) -> None:
        history = load_record()["history"]
        self.assertEqual(check(self.digest, scan_cache.OUTPUT_FORMAT_GENERATION, history), "")

    def test_the_fixture_exercises_rows_of_every_kind(self) -> None:
        items = self.payload["items"]
        self.assertGreaterEqual(len(items), 12)
        verdicts = {(item.get("result") or {}).get("verdict_code") for item in items}
        self.assertTrue({"manipulation_evidence", "undetermined"} <= verdicts, verdicts)
        statuses = {entry["status"] for item in items for entry in (item.get("result") or {}).get("coverage", [])}
        self.assertTrue({"ran", "skipped", "failed"} <= statuses, statuses)
        self.assertTrue(any(item.get("container") for item in items))  # archive members

    def test_the_check_fails_a_changed_output_under_the_same_generation(self) -> None:
        history = [{"generation": 7, "sha256": "a" * 64}]
        changed = check("b" * 64, 7, history)
        self.assertIn("OUTPUT_FORMAT_GENERATION이 7 그대로입니다", changed)
        self.assertIn("기록하십시오", check("b" * 64, 8, history))  # bumped, golden not recorded
        self.assertNotEqual(check("a" * 64, 8, history), "")  # bumped without an output change
        self.assertEqual(check("a" * 64, 7, history), "")
        payload = json.loads(json.dumps(self.payload))
        payload["items"][0]["status"] = payload["items"][0]["status"] + "x"  # any change of any row
        self.assertNotEqual(digest(payload), self.digest)

    def test_the_history_only_grows(self) -> None:
        record = load_record()
        self.assertEqual(history_problems(record["history"]), [])
        self.assertEqual(history_problems([{"generation": 3, "sha256": "a" * 64}, {"generation": 3, "sha256": "b" * 64}]), ["세대가 증가하지 않음: 3 → 3"])

    def test_no_recorded_generation_was_rewritten(self) -> None:
        git = shutil.which("git")
        if git is None:
            self.skipTest("git not available")
        inside = subprocess.run([git, "rev-parse", "--is-inside-work-tree"], cwd=REPO, capture_output=True, text=True, check=False)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            self.skipTest("not a git work tree")
        relative = GOLDEN_PATH.relative_to(REPO).as_posix()
        commits = subprocess.run([git, "log", "--format=%H", "HEAD", "--", relative], cwd=REPO, capture_output=True, text=True, check=False).stdout.split()
        current = {entry["generation"]: entry["sha256"] for entry in load_record()["history"]}
        for commit in commits:
            shown = subprocess.run([git, "show", f"{commit}:{relative}"], cwd=REPO, capture_output=True, text=True, check=False)
            if shown.returncode != 0:
                continue
            for entry in json.loads(shown.stdout)["history"]:
                with self.subTest(commit=commit[:12], generation=entry["generation"]):
                    self.assertEqual(current.get(entry["generation"]), entry["sha256"], "a recorded generation's golden was rewritten")


if __name__ == "__main__":
    unittest.main()

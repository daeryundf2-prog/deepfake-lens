"""R18-3 (round 18): a cancelled scan accounts for every file it did not analyze.

The verifier ran ``webapp_api._scan_payload`` on a 300-file folder with a
cancel after five checks: summary.total 3, unrecorded_files.total_files 1 —
the other 297 files had neither a row nor an unrecorded reason
(``core._scan_specs``: ``if should_stop(): break``). The job said "done", the
GUI "분석 완료 — 3개 파일", and an exported report read like a complete
folder report. X1 (round 7): no file of the folder may be left out silently.

Now the files a cancel left unanalyzed are ``summary.files_cancelled`` with
``summary.cancelled`` true, the unrecorded reason ``cancelled`` ("사용자
취소"), every summary line and unrecorded headline says "부분 결과", the web
job's status is ``cancelled`` and the stream's ``cancelled`` event carries
the partial payload.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import time
import unittest
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens import core, webapp_api
from deepfake_lens.analysis_api import AnalysisOptions, scan_folder_run, scan_payload
from deepfake_lens.result_text import CANCELLED_PARTIAL_RESULT, summary_line, unrecorded_files
from deepfake_lens.result_types import Verdict

HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None
HAVE_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None
FILES = 30


def _folder(root: Path, count: int = FILES) -> Path:
    folder = root / "case"
    folder.mkdir()
    for index in range(count):
        (folder / f"note-{index:03d}.txt").write_text(f"메모 {index}", encoding="utf-8")
    return folder


def _stop_after(checks: int) -> Any:
    calls: list[int] = []

    def should_stop() -> bool:
        calls.append(1)
        return len(calls) > checks

    return should_stop


def _cancelled_reason(payload: dict[str, Any]) -> int:
    reasons = {entry["code"]: entry["count"] for entry in payload["unrecorded_files"]["reasons"]}
    return int(reasons["cancelled"])


class CancelledFilesAreCountedTest(unittest.TestCase):
    def _check(self, workers: int) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _folder(Path(tmp))
            options = AnalysisOptions(no_default_engine=True, workers=workers)
            run = scan_folder_run(folder, options, should_stop=_stop_after(5))
            payload = scan_payload(run.summary, run.items, run.thresholds, options)
        summary = payload["summary"]
        self.assertTrue(summary["cancelled"])
        self.assertGreater(summary["files_cancelled"], 0)
        # Every file of the folder is a row or a cancelled count — none vanishes.
        self.assertEqual(summary["total"] + summary["files_cancelled"], FILES)
        self.assertEqual(_cancelled_reason(payload), summary["files_cancelled"])
        self.assertEqual(payload["unrecorded_files"]["total_files"], summary["files_cancelled"])
        self.assertIn(CANCELLED_PARTIAL_RESULT, payload["unrecorded_files"]["lines"][0])
        self.assertTrue(any(line.startswith("사용자 취소:") for line in payload["unrecorded_files"]["lines"]))
        self.assertTrue(summary_line(run.summary).startswith(f"[{CANCELLED_PARTIAL_RESULT}: 분석하지 않은 파일 {summary['files_cancelled']}개]"))
        # No row says "검사가 취소되었습니다" any more (the parallel path made skipped rows of them).
        self.assertFalse(any(item.status == "skipped" for item in run.items))
        if HAVE_JSONSCHEMA:
            from deepfake_lens.tests.test_json_contract import _errors

            self.assertEqual(_errors(payload), [])

    def test_sequential_scan(self) -> None:
        self._check(workers=1)

    def test_parallel_scan(self) -> None:
        self._check(workers=4)

    def test_a_scan_that_finished_is_not_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = _folder(Path(tmp), count=3)
            summary, items = core.scan_directory(folder, should_stop=lambda: False)
        self.assertFalse(summary.cancelled)
        self.assertEqual(summary.files_cancelled, 0)
        self.assertNotIn("cancelled", summary_line(summary))
        self.assertNotIn(CANCELLED_PARTIAL_RESULT, unrecorded_files(items, summary).headline())

    def test_cancelled_archive_members_keep_the_container_from_authenticity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "case"
            folder.mkdir()
            with zipfile.ZipFile(folder / "bundle.zip", "w") as archive:
                for index in range(4):
                    archive.writestr(f"m{index}.txt", f"구성 {index}")
            summary, items = core.scan_directory(folder, should_stop=_stop_after(1))
        self.assertTrue(summary.cancelled)
        self.assertEqual(summary.files_cancelled, 3)
        container = next(item for item in items if item.kind == "archive")
        assert container.result is not None
        self.assertNotEqual(container.result.verdict_code, Verdict.AUTHENTICITY_EVIDENCE)
        self.assertIn(core.CANCELLED_MEMBERS_WARNING.format(n=3), container.result.limitations)


class WebJobIsCancelledTest(unittest.TestCase):
    def test_the_async_job_ends_cancelled_with_the_partial_result(self) -> None:
        gate = threading.Event()
        real = core.analyze_file

        def first_file_waits(*args: Any, **kwargs: Any) -> Any:
            gate.wait(30)
            return real(*args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict()):
            folder = _folder(Path(tmp).resolve(), count=6)
            webapp_api.configure_read_roots(folder)
            with mock.patch.object(core, "analyze_file", side_effect=first_file_waits):
                job = webapp_api._scan_job_start(f"folder={folder}&no_default_engine=true", default_folder=folder)
                job_id = str(job["job_id"])
                self.assertTrue(webapp_api._scan_cancel_payload(f"job={job_id}")["cancelled"])
                gate.set()
                deadline = time.monotonic() + 60
                while True:
                    state = webapp_api._scan_status_payload(f"job={job_id}")
                    if state["status"] != "running" or time.monotonic() > deadline:
                        break
                    time.sleep(0.05)
        self.assertEqual(state["status"], "cancelled")  # was "done"
        result = state["result"]
        assert isinstance(result, dict)
        self.assertTrue(result["summary"]["cancelled"])
        self.assertEqual(result["summary"]["total"] + result["summary"]["files_cancelled"], 6)
        # R19-3 (round 19): a report naming this job gets the job's count, whatever the client posts.
        empty = core.summarize([], capped=False)
        marked, source = webapp_api._with_posted_cancel(empty, {"cancelled": True, "files_cancelled": 10**18}, job_id, folder)
        self.assertEqual((source, marked.files_cancelled), ("server", result["summary"]["files_cancelled"]))
        self.assertFalse(marked.files_cancelled_unverified)
        # Another folder, or an unknown job: unverified.
        other, source = webapp_api._with_posted_cancel(empty, {"cancelled": True, "files_cancelled": 10**18}, job_id, folder.parent)
        self.assertEqual((source, other.files_cancelled, other.files_cancelled_unverified), ("unverified", 0, True))

    def test_a_report_of_a_cancelled_scan_says_partial(self) -> None:
        """R19-3 (round 19): the posted files_cancelled (7 here) used to be signed as given — 10**18 was. Without a
        server job to check it against the count is 0 and the summary line says it is unverified."""
        summary = core.summarize([], capped=False)
        marked, source = webapp_api._with_posted_cancel(summary, {"cancelled": True, "files_cancelled": 7})
        self.assertTrue(marked.cancelled)
        self.assertEqual((source, marked.files_cancelled, marked.files_cancelled_unverified), ("unverified", 0, True))
        self.assertIn("분석하지 않은 파일 수는 서버가 확인하지 못했습니다", summary_line(marked))
        self.assertNotIn("7", summary_line(marked).split("]")[0])
        self.assertNotIn("files_cancelled_unverified", marked.to_json())  # the summary JSON is unchanged
        # A posted summary can only add the caveat.
        self.assertEqual(webapp_api._with_posted_cancel(summary, {"cancelled": False, "files_cancelled": 7}), (summary, None))
        self.assertEqual(webapp_api._with_posted_cancel(summary, {"cancelled": True, "files_cancelled": -3})[0].files_cancelled, 0)

    def test_html_report_and_evidence_statement_say_partial(self) -> None:
        from deepfake_lens.evidence_statement import build_evidence_statement
        from deepfake_lens.reports import write_html_report

        with tempfile.TemporaryDirectory() as tmp:
            folder = _folder(Path(tmp), count=5)
            summary, items = core.scan_directory(folder, should_stop=_stop_after(2))
            out = Path(tmp) / "report.html"
            write_html_report(out, summary, items)
            html = out.read_text(encoding="utf-8")
        self.assertIn(CANCELLED_PARTIAL_RESULT, html)
        self.assertIn("사용자 취소", html)
        statement = build_evidence_statement(items, summary=summary)
        self.assertIn(CANCELLED_PARTIAL_RESULT, statement.to_markdown())


class GuiShowsPartialTest(unittest.TestCase):
    def test_gui_banner_toast_and_exports_use_the_partial_note(self) -> None:
        source = (Path(core.__file__).resolve().parent / "gui.js").read_text(encoding="utf-8")
        self.assertIn("function partialResultNote(summary)", source)
        self.assertIn("summary.cancelled !== true", source)
        self.assertIn("const cancelledNote = partialResultNote(lastProvenance.summary);", source)  # banner
        self.assertIn("if (partialResultNote(data.summary)) toast(`검사 취소 — 부분 결과", source)  # not "분석 완료"
        self.assertIn("partial_result: partial", source)  # JSON export
        self.assertIn("(partial ? `# ${partial}\\n` : '')", source)  # CSV export


@unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed — streaming API")
class StreamCancelCarriesThePartialResultTest(unittest.TestCase):
    def test_cancelled_event_counts_the_files_not_started(self) -> None:
        import uuid

        from fastapi.testclient import TestClient

        from deepfake_lens.api_server import create_app

        real = core.analyze_file
        fixed = uuid.UUID(int=0x0123456789AB << 80)
        job_id = fixed.hex[:12]
        headers = {"host": "localhost", "X-Deepfake-Lens-Client": "r18"}
        calls: list[int] = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict()):
            folder = _folder(Path(tmp).resolve(), count=8)
            webapp_api.configure_read_roots(folder)
            app = create_app(default_folder=folder)

            def cancel_after_third(*args: Any, **kwargs: Any) -> Any:
                calls.append(1)
                if len(calls) == 3:
                    response = TestClient(app).post(f"/api/jobs/{job_id}/cancel", headers=headers)
                    assert response.status_code == 200, response.text
                return real(*args, **kwargs)

            with mock.patch.object(core, "analyze_file", side_effect=cancel_after_third), mock.patch("uuid.uuid4", return_value=fixed):
                with TestClient(app).stream("POST", "/api/scan/stream", params={"directory": str(folder)}, headers=headers) as response:
                    body = "".join(response.iter_text())
        events = []
        for block in body.split("\n\n"):
            lines = block.strip().splitlines()
            if lines and lines[0].startswith("event: "):
                events.append((lines[0][len("event: "):], json.loads("".join(line[len("data: "):] for line in lines[1:] if line.startswith("data: ")))))
        name, final = events[-1]
        self.assertEqual(name, "cancelled")
        self.assertEqual(final["files_cancelled"], 5)
        partial = final["partial"]
        self.assertTrue(partial["summary"]["cancelled"])
        self.assertEqual(partial["summary"]["total"], 3)
        self.assertEqual(_cancelled_reason(partial), 5)


if __name__ == "__main__":
    unittest.main()

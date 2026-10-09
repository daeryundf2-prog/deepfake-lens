"""N7/N8: Korean statuses, help and API errors; operational CLI/API behaviour.

N7:
- the CLI table's 결론 column shows 건너뜀/미지원/실패/중복, never the raw
  status code; the face-manipulation type reads in Korean;
- the API's 401 bodies are Korean; ``scan --help`` (and the other listed
  commands) describe their arguments in Korean.

N8:

- ``--key-file`` naming an empty (or unreadable) file stops the run with
  exit 2 (verify-report: 4) and "서명 키가 비어 있습니다" before any scan.
- A non-recursive scan reports the subfolders it did not enter
  (``summary.subfolders_skipped`` + one table line), never omits them silently.
- The stream's ``cancelled`` event reports ``total`` = rows planned and
  ``done`` = rows processed.
- Routine corrupt-file tracebacks go to the log file; stderr gets one Korean
  line; ``--verbose`` shows them.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens.cli import main as cli_main

HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None
HAVE_PIL = importlib.util.find_spec("PIL") is not None


def _run(argv: list[str], env: dict[str, str] | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli_main(argv)
    return code, out.getvalue(), err.getvalue()


class KoreanStatusAndHelpTest(unittest.TestCase):
    """N7."""

    def test_table_shows_korean_status_for_rows_without_a_verdict(self) -> None:
        from deepfake_lens.cli_render import _print_table
        from deepfake_lens.core import summarize
        from deepfake_lens.result_types import ScanItem

        items = [
            ScanItem("a.xyz", "a.xyz", "unsupported", "unsupported", 3, error="지원 형식이 아닙니다."),
            ScanItem("b.jpg", "b.jpg", "image", "failed", 0, error="분석 오류: RuntimeError: 디코더 실패"),
            ScanItem("c.lnk", "c.lnk", "unknown", "skipped", 0, error="심볼릭 링크 — 링크를 따라가지 않으므로 분석하지 않았습니다"),
            ScanItem("d.txt", "d.txt", "duplicate", "duplicate", 3, error="중복 내용(동일 해시)"),
        ]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            _print_table(summarize(items, capped=False), items, include_low=True)
        rows = {line.rstrip().split("  #")[0].split()[-1]: line for line in out.getvalue().splitlines() if "  # " in line}
        expected = {"a.xyz": "미지원", "b.jpg": "실패", "c.lnk": "건너뜀", "d.txt": "중복"}
        for path, label in expected.items():
            with self.subTest(row=path):
                self.assertTrue(rows[path].startswith(label), rows[path])
        for raw in ("unsupported ", "failed ", "skipped ", "duplicate "):
            self.assertFalse(any(line.startswith(raw) for line in out.getvalue().splitlines()), raw)

    def test_face_manipulation_type_reads_in_korean(self) -> None:
        from types import SimpleNamespace

        from deepfake_lens import core
        from deepfake_lens.face import FACE_STATUS_ANALYZED, MANIPULATION_TYPE_LABELS, manipulation_type_label

        self.assertEqual(manipulation_type_label("face_swap"), "얼굴 교체(face swap) 추정")
        self.assertEqual(set(MANIPULATION_TYPE_LABELS), {"face_swap", "reenactment", "face_paste", "unknown"})
        fake = SimpleNamespace(status=FACE_STATUS_ANALYZED, face_count=1, signals=[1, 2], manipulation_type="face_swap", score=40, limitations=[], reference_note="")
        with mock.patch("deepfake_lens.face.analyze_faces", return_value=fake), mock.patch.dict("sys.modules", {"cv2": mock.MagicMock()}):
            layers = core._deep_image_layers(Path("x.png"))
        details = [signal.detail for signal in layers.reference]
        self.assertTrue(any("유형 추정: 얼굴 교체(face swap) 추정" in detail for detail in details), details)
        self.assertFalse(any("유형 추정 face_swap" in detail for detail in details), details)

    def test_listed_commands_have_korean_argument_help(self) -> None:
        import re

        from deepfake_lens.cli_parser import build_parser

        _, parsers = build_parser()
        hangul = re.compile(r"[가-힣]")
        for command in ("scan", "web", "api-serve", "evidence-statement", "verify-report", "corpus", "doctor", "vendor-weights"):
            parser = parsers[command]
            with self.subTest(command=command):
                for action in parser._actions:
                    if action.help in (None, "==SUPPRESS==") or action.dest == "help":
                        continue
                    self.assertRegex(action.help, hangul, f"{command} {action.option_strings or action.dest}: {action.help}")
        corpus_sub = next(a for a in parsers["corpus"]._actions if a.dest == "corpus_command")
        choices: dict[str, Any] = dict(corpus_sub.choices or {})
        for name, sub in choices.items():
            for action in sub._actions:
                if action.dest == "help":
                    continue
                with self.subTest(command=f"corpus {name}", arg=action.dest):
                    self.assertRegex(action.help or "", hangul)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), self.assertRaises(SystemExit):
            cli_main(["scan", "--help"])
        self.assertIn("하위 폴더까지 검사", buffer.getvalue())
        self.assertNotIn("scan recursively instead of direct children only", buffer.getvalue())

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
    def test_api_401_bodies_are_korean(self) -> None:
        from fastapi.testclient import TestClient

        from deepfake_lens.api_server import create_app

        with_token = TestClient(create_app(token="s3cret"))
        response = with_token.get("/api/scan", headers={"host": "localhost"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["message"], "인증 실패: API 토큰이 없거나 일치하지 않습니다")
        without_header = TestClient(create_app())
        response = without_header.get("/api/scan", headers={"host": "localhost"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("헤더가 필요합니다", response.json()["message"])
        self.assertNotIn("missing", response.json()["message"])


class EmptyKeyFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.case = self.root / "case"
        self.case.mkdir()
        (self.case / "note.txt").write_text("사건 메모", encoding="utf-8")
        self.empty_key = self.root / "empty.key"
        self.empty_key.write_bytes(b"  \n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scan_sign_with_empty_key_file_exits_2_before_scanning(self) -> None:
        from deepfake_lens import cli

        with mock.patch.object(cli, "scan_folder", side_effect=AssertionError("scan must not start")):
            code, out, err = _run(["scan", str(self.case), "--json-out", str(self.root / "r.json"), "--sign", "--key-file", str(self.empty_key)])
        self.assertEqual(code, 2)
        self.assertIn("오류: 서명 키가 비어 있습니다", err)
        self.assertFalse((self.root / "r.json").exists())

    def test_other_commands_refuse_an_empty_key_file(self) -> None:
        scan_json = self.root / "scan.json"
        self.assertEqual(_run(["scan", str(self.case), "--json-out", str(scan_json)])[0], 0)
        for argv, expected in (
            (["evidence-statement", str(scan_json), "--key-file", str(self.empty_key)], 2),
            (["legal-report", str(self.case / "note.txt"), "--key-file", str(self.empty_key)], 2),
            (["verify-report", str(scan_json), "--key-file", str(self.empty_key)], 4),
        ):
            with self.subTest(command=argv[0]):
                code, _, err = _run(argv)
                self.assertEqual(code, expected)
                self.assertIn("서명 키가 비어 있습니다", err)

    def test_unreadable_key_file_is_named(self) -> None:
        code, _, err = _run(["scan", str(self.case), "--sign", "--json-out", str(self.root / "r.json"), "--key-file", str(self.root / "missing.key")])
        self.assertEqual(code, 2)
        self.assertIn("서명 키 파일을 읽을 수 없습니다: missing.key", err)
        self.assertNotIn(str(self.root), err)

    def test_a_real_key_still_signs(self) -> None:
        key = self.root / "real.key"
        key.write_bytes(b"real-key\n")
        out = self.root / "signed.json"
        self.assertEqual(_run(["scan", str(self.case), "--json-out", str(out), "--sign", "--key-file", str(key)])[0], 0)
        self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["signature"])


class SubfoldersSkippedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.case = Path(self.tmp.name) / "case"
        (self.case / "sub-a").mkdir(parents=True)
        (self.case / "sub-b" / "deeper").mkdir(parents=True)
        (self.case / "top.txt").write_text("최상위 메모", encoding="utf-8")
        (self.case / "sub-a" / "inner.txt").write_text("하위 메모", encoding="utf-8")
        if hasattr(os, "symlink"):
            with contextlib.suppress(OSError):
                (self.case / "linked-dir").symlink_to(self.case / "sub-a", target_is_directory=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_non_recursive_scan_counts_and_prints_skipped_subfolders(self) -> None:
        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder, scan_payload

        options = AnalysisOptions()
        summary, items, thresholds = scan_folder(self.case, options)
        self.assertEqual(summary.subfolders_skipped, 2)  # sub-a, sub-b; the symlinked dir is its own row
        payload_summary: Any = scan_payload(summary, items, thresholds, options)["summary"]
        self.assertEqual(payload_summary["subfolders_skipped"], 2)
        self.assertNotIn("sub-a/inner.txt", [item.path for item in items])
        code, out, _ = _run(["scan", str(self.case)])
        self.assertEqual(code, 0)
        self.assertIn("참고: 하위 폴더 2개는 검사하지 않았습니다", out)
        self.assertIn("--recursive", out)

    def test_recursive_scan_reports_none(self) -> None:
        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder

        summary, items, _ = scan_folder(self.case, AnalysisOptions(recursive=True))
        self.assertEqual(summary.subfolders_skipped, 0)
        self.assertIn("sub-a/inner.txt", [item.path for item in items])
        _, out, _ = _run(["scan", str(self.case), "--recursive"])
        self.assertNotIn("하위 폴더", out)


@unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed — streaming API")
class StreamCancelCountsTest(unittest.TestCase):
    def test_cancelled_event_reports_planned_total_and_done(self) -> None:
        """Cancel after the third file: total is the 8 planned rows, done the rows reported."""
        import uuid
        from collections import OrderedDict

        from fastapi.testclient import TestClient

        from deepfake_lens import core, webapp_api
        from deepfake_lens.api_server import create_app

        real = core.analyze_file
        fixed = uuid.UUID(int=0x0123456789AB << 80)
        job_id = fixed.hex[:12]
        headers = {"host": "localhost", "X-Deepfake-Lens-Client": "n8"}
        calls: list[int] = []

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict()):
            folder = Path(tmp).resolve() / "case"
            folder.mkdir()
            for index in range(8):
                (folder / f"note-{index}.txt").write_text(f"메모 {index}", encoding="utf-8")
            webapp_api.configure_read_roots(folder)
            app = create_app(default_folder=folder)

            def cancel_after_third(*args: Any, **kwargs: Any) -> Any:
                calls.append(1)
                if len(calls) == 3:
                    # The examiner presses "cancel" while the third file is analyzed.
                    response = TestClient(app).post(f"/api/jobs/{job_id}/cancel", headers=headers)
                    assert response.status_code == 200, response.text
                return real(*args, **kwargs)

            with mock.patch.object(core, "analyze_file", side_effect=cancel_after_third), mock.patch("uuid.uuid4", return_value=fixed):
                with TestClient(app).stream("POST", "/api/scan/stream", params={"directory": str(folder)}, headers=headers) as response:
                    body = "".join(response.iter_text())
        events: list[tuple[str, dict[str, Any]]] = []
        for block in body.split("\n\n"):
            lines = block.strip().splitlines()
            if lines and lines[0].startswith("event: "):
                events.append((lines[0][len("event: "):], json.loads("".join(line[len("data: "):] for line in lines[1:] if line.startswith("data: ")))))
        name, final = events[-1]
        self.assertEqual(name, "cancelled", [event for event, _ in events])
        processed = [data for event, data in events if event == "progress" and data.get("stage") == "scan"]
        self.assertEqual(final["total"], 8)  # planned rows, not the rows that finished
        self.assertEqual(final["done"], len(processed))
        self.assertEqual(final["done"], 3)
        self.assertEqual(final["processed"], final["done"])


@unittest.skipUnless(HAVE_PIL, "Pillow not installed")
class TracebacksGoToTheLogFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.case = root / "case"
        self.case.mkdir()
        (self.case / "empty.jpg").write_bytes(b"")
        (self.case / "fake.gif").write_text("not a gif", encoding="utf-8")
        (self.case / "broken.docx").write_bytes(b"not a zip")
        self.logs = root / "logs"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_stderr_gets_one_korean_line_and_the_log_the_traceback(self) -> None:
        code, out, err = _run(["scan", str(self.case)], env={"DEEPFAKE_LENS_LOG_DIR": str(self.logs)})
        self.assertEqual(code, 0)
        self.assertNotIn("Traceback", err)
        summary_lines = [line for line in err.splitlines() if line.startswith("참고: 판독 불가·손상 파일 등 처리 오류")]
        self.assertEqual(len(summary_lines), 1, err)
        self.assertIn(str(self.logs / "deepfake-lens.log"), summary_lines[0])
        log_text = (self.logs / "deepfake-lens.log").read_text(encoding="utf-8")
        self.assertIn("Traceback", log_text)
        self.assertNotIn("Traceback", out)

    def test_verbose_shows_the_tracebacks(self) -> None:
        code, _, err = _run(["scan", str(self.case), "--verbose"], env={"DEEPFAKE_LENS_LOG_DIR": str(self.logs)})
        self.assertEqual(code, 0)
        self.assertIn("Traceback", err)
        self.assertNotIn("참고: 판독 불가·손상 파일 등 처리 오류", err)

    def test_logging_is_restored_after_the_run(self) -> None:
        import logging

        package = logging.getLogger("deepfake_lens")
        before = (list(package.handlers), package.propagate, package.level)
        _run(["scan", str(self.case)], env={"DEEPFAKE_LENS_LOG_DIR": str(self.logs)})
        self.assertEqual((list(package.handlers), package.propagate, package.level), before)


if __name__ == "__main__":
    unittest.main()

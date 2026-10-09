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

        # N15: the CLI scans through scan_folder_run (scan_folder is the 2-tuple API).
        with mock.patch.object(cli, "scan_folder_run", side_effect=AssertionError("scan must not start")):
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
        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder_run, scan_payload

        options = AnalysisOptions()
        run = scan_folder_run(self.case, options)
        summary, items, thresholds = run.summary, run.items, run.thresholds
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

        summary, items = scan_folder(self.case, AnalysisOptions(recursive=True))
        self.assertEqual(summary.subfolders_skipped, 0)
        self.assertIn("sub-a/inner.txt", [item.path for item in items])
        _, out, _ = _run(["scan", str(self.case), "--recursive"])
        self.assertNotIn("하위 폴더", out)

    def test_scan_folder_returns_summary_and_items(self) -> None:
        """N15: scan_folder/scan_file return the spec's (summary, items) 2-tuple
        (they returned (summary, items, thresholds)); scan_folder_run /
        scan_file_run carry the threshold profile load_thresholds resolves."""
        import json

        from deepfake_lens.analysis_api import (
            AnalysisOptions, ScanRun, load_thresholds, scan_file, scan_file_run, scan_folder, scan_folder_run,
        )
        from deepfake_lens.core import BatchScanSummary, _thresholds_json

        models = Path(self.tmp.name) / "models"
        models.mkdir()
        (models / "thresholds.json").write_text(json.dumps({
            "version": "layer-thresholds-v1", "values": {"image": 70}, "in_sample": True, "samples": 10,
        }), encoding="utf-8")
        options = AnalysisOptions(models_dir=models, no_default_engine=True)
        self.assertIsNotNone(load_thresholds(options))
        result = scan_folder(self.case, options)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        summary, items = result
        self.assertIsInstance(summary, BatchScanSummary)
        run = scan_folder_run(self.case, options)
        self.assertIsInstance(run, ScanRun)
        self.assertEqual([item.path for item in run.items], [item.path for item in items])
        self.assertEqual(_thresholds_json(run.thresholds), _thresholds_json(load_thresholds(options)))
        file_result = scan_file(self.case / "top.txt", options)
        self.assertEqual(len(file_result), 2)
        file_run = scan_file_run(self.case / "top.txt", options)
        self.assertEqual([item.path for item in file_run.items], [item.path for item in file_result[1]])
        self.assertEqual(_thresholds_json(file_run.thresholds), _thresholds_json(load_thresholds(options)))


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


class ScanFolderErrorTest(unittest.TestCase):
    """S4: a folder that cannot be scanned is a Korean reason and exit 2 — never a bare path."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()

    def _assert_error(self, argv: list[str], expected: str) -> None:
        code, out, err = _run(argv)
        self.assertEqual(code, 2, err)
        self.assertEqual(out, "")
        self.assertIn(f"오류: {expected}", err.splitlines())
        self.assertNotIn("Traceback", err)
        if argv[0] != "evidence-statement":
            # The reason is the only output — no engine list or threshold warning first.
            self.assertEqual(err.splitlines(), [f"오류: {expected}"])

    def test_missing_folder(self) -> None:
        missing = self.base / "없는 폴더"
        for argv in (["scan", str(missing)], [str(missing)], ["scan", str(missing), "--format", "json"]):
            with self.subTest(argv=argv[:-1] if len(argv) > 2 else argv):
                self._assert_error(argv, f"폴더를 찾을 수 없습니다: {missing}")

    def test_file_instead_of_folder(self) -> None:
        file = self.base / "a.txt"
        file.write_text("메모", encoding="utf-8")
        self._assert_error(["scan", str(file)], f"폴더가 아니라 파일입니다: {file} (단일 파일은 forensic/classify를 사용)")

    def test_unreadable_folder(self) -> None:
        folder = self.base / "locked"
        folder.mkdir()
        real_scandir = os.scandir

        def denied(path: Any = ".") -> Any:
            if Path(path) == folder:
                raise PermissionError(13, "Permission denied", str(path))
            return real_scandir(path)

        # Root ignores 0o000, so the refusal is injected at the listing call.
        with mock.patch("os.scandir", denied):
            self._assert_error(["scan", str(folder)], f"폴더를 읽을 수 없습니다: {folder} (권한이 없습니다)")
            self._assert_error(["evidence-statement", str(folder)], f"폴더를 읽을 수 없습니다: {folder} (권한이 없습니다)")
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            folder.chmod(0)
            self.addCleanup(folder.chmod, 0o755)
            self._assert_error(["scan", str(folder)], f"폴더를 읽을 수 없습니다: {folder} (권한이 없습니다)")

    def test_library_and_web_report_the_same_reason(self) -> None:
        from deepfake_lens import webapp_api
        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder
        from deepfake_lens.core import ScanFolderError

        missing = self.base / "gone"
        with self.assertRaises(ScanFolderError) as caught:
            scan_folder(missing, AnalysisOptions())
        self.assertIsInstance(caught.exception, NotADirectoryError)
        self.assertEqual(str(caught.exception), f"폴더를 찾을 수 없습니다: {missing}")
        from collections import OrderedDict

        with mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict()):
            webapp_api.configure_read_roots(self.base)
            payload = webapp_api._scan_payload(f"folder={missing}", default_folder=self.base)
        self.assertEqual(payload, {"error": f"폴더를 찾을 수 없습니다: {missing}"})

    def test_exit_codes_are_documented(self) -> None:
        doc = (Path(__file__).resolve().parents[2] / "docs" / "deepfake-lens-cli.md").read_text(encoding="utf-8")
        self.assertIn("| 명령 | 0 | 1 | 2 | 3 | 4 |", doc)
        for command in ("`scan`", "`verify-report`", "`evidence-statement`", "`api-serve`"):
            self.assertTrue(any(line.startswith(f"  | {command} |") for line in doc.splitlines()), command)
        for message in ("폴더를 찾을 수 없습니다", "폴더가 아니라 파일입니다", "폴더를 읽을 수 없습니다"):
            self.assertIn(message, doc)


def _write_tone_wav(path: Path, seconds: float = 0.5, rate: int = 16000) -> Path:
    import math
    import wave

    frames = b"".join(int(8000 * math.sin(2 * math.pi * 220 * n / rate)).to_bytes(2, "little", signed=True) for n in range(int(seconds * rate)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return path


def _profiles(node: Any) -> list[str]:
    """Every models[].profile value in a JSON tree (not the signed body's model_pins names)."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "models" and isinstance(value, list):
                found.extend(str(m["profile"]) for m in value if isinstance(m, dict) and "profile" in m)
            found.extend(_profiles(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_profiles(value))
    return found


class RedactInstallPathsTest(unittest.TestCase):
    """S3: --redact-paths reduces models[].profile (and any other install-path value) to the bare file name."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.case = self.base / "case"
        self.case.mkdir()
        _write_tone_wav(self.case / "tone.wav")
        (self.case / "memo.txt").write_text("회의 메모: 다음 주 일정 확인", encoding="utf-8")
        from deepfake_lens.serialization import install_roots

        self.roots = install_roots()

    def _leaks(self, text: str) -> list[str]:
        return [root for root in self.roots if root in text] + (["site-packages"] if "site-packages" in text else [])

    def test_html_report_and_signed_body_name_profiles_by_file_name_only(self) -> None:
        from deepfake_lens.reports import extract_signed_report

        code, out, _ = _run(["scan", str(self.case), "--format", "json"])
        self.assertEqual(code, 0)
        full = _profiles(json.loads(out))
        self.assertTrue(full, "the wav row lists the audio model profiles")
        # Without --redact-paths the JSON keeps the profile path.
        self.assertTrue(all(Path(profile).is_absolute() for profile in full), full)

        redacted_html, plain_html = self.base / "redacted.html", self.base / "plain.html"
        pdf, forensic_pdf = self.base / "r.pdf", self.base / "r-forensic.pdf"
        code, _, err = _run(["scan", str(self.case), "--redact-paths", "--html-out", str(redacted_html), "--pdf-out", str(pdf), "--forensic-pdf-out", str(forensic_pdf)])
        if code == 2 and "PDF" in err:
            code, _, err = _run(["scan", str(self.case), "--redact-paths", "--html-out", str(redacted_html)])
        self.assertEqual(code, 0, err)
        html = redacted_html.read_text(encoding="utf-8")
        self.assertEqual(self._leaks(html), [])
        body = extract_signed_report(html)
        assert body is not None
        profiles = _profiles(body)
        self.assertEqual(sorted(profiles), sorted(Path(profile).name for profile in full))
        self.assertTrue(all("/" not in profile and "\\" not in profile for profile in profiles), profiles)

        self.assertEqual(_run(["scan", str(self.case), "--html-out", str(plain_html)])[0], 0)
        plain_body = extract_signed_report(plain_html.read_text(encoding="utf-8"))
        assert plain_body is not None
        self.assertEqual(sorted(_profiles(plain_body)), sorted(full), "non-redacted reports keep the path")

    def test_redact_install_paths_rewrites_every_install_path_value(self) -> None:
        from dataclasses import replace

        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder
        from deepfake_lens.serialization import redact_install_paths

        _, items = scan_folder(self.case, AnalysisOptions())
        wav = next(item for item in items if item.path == "tone.wav")
        assert wav.result is not None and wav.result.model_analysis is not None
        root = self.roots[0]
        planted = replace(wav, result=replace(wav.result, limitations=[*wav.result.limitations, f"프로필 {root}/deepfake_lens/models/x-runtime.json 확인"]))
        [redacted] = redact_install_paths([planted])
        text = json.dumps(redacted.to_json(), ensure_ascii=False)
        self.assertEqual(self._leaks(text), [])
        self.assertIn("프로필 x-runtime.json 확인", text)
        self.assertEqual(redacted.result.verdict_code, wav.result.verdict_code)  # type: ignore[union-attr]
        # The input row is untouched (the JSON output keeps full paths).
        self.assertTrue(all(Path(p).is_absolute() for p in _profiles(wav.to_json())))


if __name__ == "__main__":
    unittest.main()


class InputErrorExitCodesTest(unittest.TestCase):
    """R10-5 (round 10): an unusable input exits with the documented code, never 1.

    A JSON nested past the recursion limit made ``verify-report`` exit 1 (its
    "변조됨" code) and ``evidence-statement`` exit 1 (documented: 2); ``web``/
    ``api-serve --port -1|99999`` reached the socket (OverflowError, exit 1);
    and a command that failed before producing any row still printed "처리
    오류 1건 — 각 행의 검사 범위(coverage)에 사유가 기록".
    """

    ROW_NOTE = "참고: 판독 불가·손상 파일 등 처리 오류"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.deep = self.root / "deep.json"
        self.deep.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
        self.deep_object = self.root / "deep_object.json"
        self.deep_object.write_text('{"items": ' + "[" * 100000 + "]" * 100000 + "}", encoding="utf-8")
        self.env = {"DEEPFAKE_LENS_LOG_DIR": str(self.root / "logs"), "HOME": str(self.root)}

    def test_deeply_nested_json_is_an_input_error(self) -> None:
        for argv, expected in (
            (["verify-report", str(self.deep)], 4),
            (["verify-report", str(self.deep_object)], 4),
            (["evidence-statement", str(self.deep)], 2),
            (["evidence-statement", str(self.deep_object)], 2),
            (["feedback", str(self.deep)], 2),
        ):
            with self.subTest(argv=argv[0], file=Path(argv[1]).name):
                code, _, err = _run(argv, env=self.env)
                self.assertEqual(code, expected, err)
                self.assertIn("JSON 중첩이 너무 깊습니다", err)
                self.assertNotIn(self.ROW_NOTE, err)
                self.assertNotIn("Traceback", err)

    def test_port_outside_1_to_65535_is_a_usage_error(self) -> None:
        for command in ("web", "api-serve"):
            for port in ("-1", "0", "65536", "99999", "x"):
                with self.subTest(command=command, port=port):
                    err = io.StringIO()
                    with mock.patch.dict(os.environ, self.env), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                        with self.assertRaises(SystemExit) as raised:
                            cli_main([command, "--port", port])
                    self.assertEqual(raised.exception.code, 2)
                    self.assertIn("인수 --port: 포트는 1–65535 범위의 정수여야 합니다", err.getvalue())
                    self.assertNotIn("argument", err.getvalue())
        from deepfake_lens.cli_parser import port_number

        self.assertEqual((port_number("1"), port_number("65535")), (1, 65535))

    def test_value_or_recursion_error_of_a_command_uses_the_input_error_code(self) -> None:
        for exc in (RecursionError("maximum recursion depth exceeded"), ValueError("bad value")):
            for argv, expected in ((["verify-report", str(self.deep)], 4), (["doctor"], 2)):
                with self.subTest(exc=type(exc).__name__, command=argv[0]):
                    with mock.patch("deepfake_lens.cli._run_command", side_effect=exc), \
                            mock.patch("deepfake_lens.cli_inputs.read_json_input", return_value={}):
                        code, _, err = _run(argv, env=self.env)
                    self.assertEqual(code, expected, err)
                    self.assertIn("오류: 입력을 처리할 수 없습니다", err)
                    self.assertIn(str(self.root / "logs" / "deepfake-lens.log"), err)
                    self.assertNotIn(self.ROW_NOTE, err)  # no row was produced

    def test_unexpected_error_names_the_log_and_claims_no_rows(self) -> None:
        with mock.patch("deepfake_lens.cli._run_command", side_effect=RuntimeError("boom")):
            code, _, err = _run(["doctor"], env=self.env)
        self.assertEqual(code, 1)
        self.assertIn("예기치 않은 오류가 발생했습니다(RuntimeError)", err)
        self.assertIn(str(self.root / "logs" / "deepfake-lens.log"), err)
        self.assertNotIn(self.ROW_NOTE, err)

"""R11-1 (round 11): a file name that is not UTF-8 never breaks a scan.

On POSIX a file name is bytes. A CP949 ``증거사진``-style name copied from a
Korean Windows disk (``b"\\xc1\\xf5\\xb0\\xc5.png"``) reaches Python as a
``str`` with lone surrogates (PEP 383). Before R11-1 one such file made
``scan --json-out`` exit 2 ("입력을 처리할 수 없습니다(UnicodeEncodeError)")
leaving a 0-byte JSON and no HTML, the web server's ``/api/scan`` answer 400
with the English codec message, and the API server answer 500.

Now every JSON body writes a surrogate as the escape ``\\udcXX``
(``json_text.json_dumps`` — ``json.loads`` gives the same ``str`` back and
``os.fsencode`` the original bytes), every text rendering shows it through
``display_name`` (visibly, as ``\\udcc1``), and the row is analysed like
any other. The fixtures are real non-UTF-8 names made with
``os.fsencode`` — no mock.
"""

from __future__ import annotations

import contextlib
import csv
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from deepfake_lens import cli, webapp_api
from deepfake_lens.json_text import escape_surrogates, json_bytes, json_dumps
from deepfake_lens.result_text import display_name, markdown_cell
from deepfake_lens.result_types import VERDICT_LABELS, Verdict

HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None
HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None
A1111 = Path(__file__).resolve().parents[2] / "fixtures" / "benchmark" / "a1111-metadata-marker.png"
MANIPULATION = VERDICT_LABELS[Verdict.MANIPULATION_EVIDENCE]
# "증거" in CP949 — not valid UTF-8.
CP949_NAME = b"\xc1\xf5\xb0\xc5.png"
NAME = os.fsdecode(CP949_NAME)  # "\udcc1\udcf5\udcb0\udcc5.png"
SHOWN = display_name(NAME)  # "\\udcc1\\udcf5\\udcb0\\udcc5.png"
NON_UTF8_SKIP = "파일 시스템이 UTF-8이 아닌 파일 이름을 허용하지 않음(Windows·macOS)"


def make_case(root: Path) -> Path:
    """A folder with the CP949-named A1111 PNG and a plainly named one.

    Raises SkipTest where the file system refuses non-UTF-8 names.
    """
    folder = root / "case"
    folder.mkdir()
    data = A1111.read_bytes()
    try:
        with open(os.fsencode(folder) + b"/" + CP949_NAME, "wb") as handle:
            handle.write(data)
    except (OSError, UnicodeError):
        raise unittest.SkipTest(NON_UTF8_SKIP)
    if NAME not in os.listdir(folder):
        raise unittest.SkipTest(NON_UTF8_SKIP)
    (folder / "plain.png").write_bytes(data)
    return folder


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


class JsonTextUnitTest(unittest.TestCase):
    def test_surrogates_become_json_escapes_that_round_trip(self) -> None:
        payload = {"path": NAME, NAME: [NAME, "한글"], "n": 1}
        text = json_dumps(payload, indent=2)
        self.assertNotRegex(text, "[\ud800-\udfff]")
        self.assertIn('"\\udcc1\\udcf5\\udcb0\\udcc5.png"', text)
        self.assertIn("한글", text)  # non-ASCII stays readable (ensure_ascii=False)
        self.assertEqual(json.loads(text), payload)
        self.assertEqual(os.fsencode(json.loads(text)["path"]), CP949_NAME)
        self.assertEqual(json.loads(json_bytes(payload).decode("utf-8")), payload)

    def test_text_without_surrogates_is_unchanged(self) -> None:
        payload = {"경로": "a\\b \"q\" ☃ \U0001f600", "x": [1.5, None, True]}
        for kwargs in ({}, {"indent": 2}, {"sort_keys": True, "separators": (",", ":")}):
            self.assertEqual(json_dumps(payload, **kwargs), json.dumps(payload, ensure_ascii=False, **kwargs))
        self.assertEqual(escape_surrogates("plain"), "plain")


class _EnvMixin(unittest.TestCase):
    def _isolate_env(self, root: Path) -> None:
        home = root / "home"
        home.mkdir()
        saved = {key: os.environ.get(key) for key in ("HOME", "DEEPFAKE_LENS_LOG_DIR", "DEEPFAKE_LENS_REPORT_KEY")}
        os.environ["HOME"] = str(home)
        os.environ["DEEPFAKE_LENS_LOG_DIR"] = str(home / "logs")
        os.environ.pop("DEEPFAKE_LENS_REPORT_KEY", None)

        def restore() -> None:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)


class NonUtf8NameCliTest(_EnvMixin):
    """R11-1: CLI scan JSON/CSV/HTML/stdout, cache, signing and the evidence statement."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        self.folder = make_case(root)
        self.out = root / "out"
        self.out.mkdir()
        self._isolate_env(root)

    def _assert_rows(self, items: list[dict[str, Any]]) -> None:
        rows = {item["path"]: item for item in items}
        self.assertEqual(sorted(rows), sorted([NAME, "plain.png"]))
        self.assertEqual(os.fsencode(rows[NAME]["path"]), CP949_NAME)
        for item in rows.values():
            self.assertEqual(item["status"], "analyzed", item["path"])
            self.assertEqual(item["result"]["verdict_code"], Verdict.MANIPULATION_EVIDENCE.value, item["path"])
        self.assertEqual(rows[NAME]["sha256"], rows["plain.png"]["sha256"])

    def test_scan_writes_every_report(self) -> None:
        json_out, csv_out, html_out = self.out / "r.json", self.out / "r.csv", self.out / "r.html"
        cache = self.out / "cache.json"
        code, stdout, stderr = _run([
            "scan", str(self.folder), "--include-low", "--json-out", str(json_out), "--csv-out", str(csv_out),
            "--html-out", str(html_out), "--cache", str(cache),
        ])
        self.assertEqual(code, 0, stderr)
        self.assertNotIn("UnicodeEncodeError", stderr)
        # CLI table: the name shown escaped, its row concluding like the other.
        rows = [line for line in stdout.splitlines() if SHOWN in line]
        self.assertEqual(len(rows), 1, stdout)
        self.assertTrue(rows[0].startswith(MANIPULATION), rows[0])
        # JSON: valid UTF-8, the escape round-trips to the file's bytes.
        raw = json_out.read_bytes()
        self.assertIn(b'"\\udcc1\\udcf5\\udcb0\\udcc5.png"', raw)
        self._assert_rows(json.loads(raw.decode("utf-8"))["items"])
        # CSV: the name through display_name.
        records = list(csv.reader(line for line in csv_out.read_text(encoding="utf-8").splitlines() if not line.startswith("#")))
        path_col = records[0].index("path")
        self.assertEqual(sorted(record[path_col] for record in records[1:]), sorted([SHOWN, "plain.png"]))
        # HTML: written, the name shown escaped, no raw surrogate.
        html = html_out.read_text(encoding="utf-8")
        self.assertIn(SHOWN, html)
        # Cache: written and replayed for both rows.
        code, stdout, stderr = _run(["scan", str(self.folder), "--include-low", "--format", "json", "--cache", str(cache)])
        self.assertEqual(code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["summary"]["cached"], 2)
        self._assert_rows(payload["items"])

    def test_stdout_json_keeps_the_name_exactly(self) -> None:
        code, stdout, stderr = _run(["scan", str(self.folder), "--include-low", "--format", "json"])
        self.assertEqual(code, 0, stderr)
        self.assertIn('"\\udcc1\\udcf5\\udcb0\\udcc5.png"', stdout)
        self._assert_rows(json.loads(stdout)["items"])

    def test_signed_report_verifies(self) -> None:
        key = self.out / "k.key"
        key.write_text("r11-1-key-0123456789abcdef", encoding="utf-8")
        report = self.out / "signed.json"
        code, _, stderr = _run(["scan", str(self.folder), "--include-low", "--json-out", str(report), "--sign", "--key-file", str(key)])
        self.assertEqual(code, 0, stderr)
        code, stdout, stderr = _run(["verify-report", str(report), "--key-file", str(key)])
        self.assertEqual(code, 0, stdout + stderr)
        # Tampering with the escaped name is still detected.
        tampered = self.out / "tampered.json"
        tampered.write_text(report.read_text(encoding="utf-8").replace("\\udcc1", "\\udcc2"), encoding="utf-8")
        code, _, _ = _run(["verify-report", str(tampered), "--key-file", str(key)])
        self.assertNotEqual(code, 0)

    def test_evidence_statement_markdown_and_json(self) -> None:
        md_out, json_out = self.out / "s.md", self.out / "s.json"
        code, stdout, stderr = _run(["evidence-statement", str(self.folder), "--md-out", str(md_out), "--json-out", str(json_out)])
        self.assertEqual(code, 0, stderr)
        self.assertIn(SHOWN, stdout)
        markdown = md_out.read_text(encoding="utf-8")
        # R12-5 (round 12): the Markdown source doubles every backslash so the
        # rendered cell reads exactly SHOWN — the source holds markdown_cell(SHOWN).
        self.assertIn(markdown_cell(SHOWN), markdown)
        rows = [line for line in markdown.splitlines() if line.startswith("| **")]
        self.assertEqual(len(rows), 2, rows)
        for row in rows:
            self.assertIn(f"[자동 분석 결론: {MANIPULATION} /", row)
        # The signed statement JSON is valid UTF-8 holding the escaped name.
        raw = json_out.read_text(encoding="utf-8")
        self.assertIn("\\udcc1\\udcf5\\udcb0\\udcc5", raw)
        json.loads(raw)

    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf required for PDF generation")
    def test_pdf_reports(self) -> None:
        from deepfake_lens.pdf_backend import import_pymupdf

        pymupdf = import_pymupdf()
        forensic, statement = self.out / "f.pdf", self.out / "s.pdf"
        code, _, stderr = _run(["scan", str(self.folder), "--include-low", "--forensic-pdf-out", str(forensic)])
        self.assertEqual(code, 0, stderr)
        code, _, stderr = _run(["evidence-statement", str(self.folder), "--pdf-out", str(statement)])
        self.assertEqual(code, 0, stderr)
        for path in (forensic, statement):
            with pymupdf.open(str(path)) as document:
                text = "".join(page.get_text() for page in document).replace("\n", "")
            self.assertIn("\\udcc1\\udcf5\\udcb0\\udcc5", text, path.name)


def _multipart(field: str, filename: bytes, data: bytes) -> tuple[bytes, str]:
    boundary = "----r11" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"".encode("ascii")
        + filename + b"\"\r\nContent-Type: application/octet-stream\r\n\r\n" + data + f"\r\n--{boundary}--\r\n".encode("ascii")
    )
    return body, f"multipart/form-data; boundary={boundary}"


class NonUtf8NameServersTest(_EnvMixin):
    """R11-1: /api/scan, /api/report and uploads on the web server and api-serve."""

    HEADERS = {"X-Deepfake-Lens-Client": "gui"}

    def setUp(self) -> None:
        roots: Any = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        self.folder = make_case(root)
        self._isolate_env(root)

    def _web(self) -> Callable[..., tuple[int, bytes]]:
        from deepfake_lens.webapp import build_server

        server = build_server("127.0.0.1", 0, default_folder=self.folder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def call(method: str, path: str, body: bytes | None = None, ctype: str = "application/json") -> tuple[int, bytes]:
            headers = dict(self.HEADERS, **({"Content-Type": ctype} if body is not None else {}))
            request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()

        return call

    def _api(self) -> Callable[..., tuple[int, bytes]]:
        from fastapi.testclient import TestClient

        from deepfake_lens import api_server

        client = TestClient(api_server.create_app(default_folder=self.folder), raise_server_exceptions=False)

        def call(method: str, path: str, body: bytes | None = None, ctype: str = "application/json") -> tuple[int, bytes]:
            headers = {"host": "localhost", **self.HEADERS}
            if body is not None:
                headers["content-type"] = ctype
            response = client.request(method, path, content=body, headers=headers)
            return response.status_code, response.content

        return call

    def _check_leg(self, name: str, call: Callable[..., tuple[int, bytes]]) -> None:
        from urllib.parse import quote

        status, body = call("GET", "/api/scan?folder=" + quote(str(self.folder)))
        self.assertEqual(status, 200, (name, body[:300]))
        scan = json.loads(body.decode("utf-8"))
        rows = {item["path"]: item for item in scan["items"]}
        self.assertEqual(sorted(rows), sorted([NAME, "plain.png"]), name)
        self.assertEqual(os.fsencode(rows[NAME]["path"]), CP949_NAME)
        for item in rows.values():
            self.assertEqual(item["result"]["verdict_code"], Verdict.MANIPULATION_EVIDENCE.value, (name, item["path"]))
        for fmt in ("json", "html"):
            request = json.dumps({"items": scan["items"], "scan_root": scan["scan_root"], "format": fmt}).encode("utf-8")
            status, body = call("POST", "/api/report?format=" + fmt, request)
            self.assertEqual(status, 200, (name, fmt, body[:300]))
            text = body.decode("utf-8")
            if fmt == "json":
                report_rows = {item["path"] for item in json.loads(text)["items"]}
                self.assertIn(NAME, report_rows, name)
            else:
                self.assertIn(SHOWN, text, name)
        if HAVE_PYMUPDF:
            request = json.dumps({"items": scan["items"], "scan_root": scan["scan_root"]}).encode("utf-8")
            status, body = call("POST", "/api/report?format=pdf", request)
            self.assertEqual(status, 200, (name, body[:300]))
            self.assertTrue(body.startswith(b"%PDF"), name)
        # Upload of a file whose multipart file name is the CP949 bytes.
        upload, ctype = _multipart("files", CP949_NAME, A1111.read_bytes())
        status, body = call("POST", "/api/analyze-upload", upload, ctype)
        self.assertEqual(status, 200, (name, body[:300]))
        items = json.loads(body.decode("utf-8"))["items"]
        self.assertEqual(len(items), 1, items)
        self.assertEqual(items[0]["result"]["verdict_code"], Verdict.MANIPULATION_EVIDENCE.value, (name, items[0]))

    def test_web_server(self) -> None:
        self._check_leg("web", self._web())

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
    def test_api_server(self) -> None:
        self._check_leg("api", self._api())


if __name__ == "__main__":
    unittest.main()

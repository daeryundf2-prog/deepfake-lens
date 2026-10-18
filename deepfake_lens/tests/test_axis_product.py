"""P0R-3 (user decision 2026-10-17, risk-based exit): the path axes as one product table (HANDOFF §8.1, §8.3).

R23-1, R24-3, R25-4, R26-2 and R26-3 were all the same kind of regression: a fix tested one axis (a relative path,
a deep working folder, a non-ASCII name, four workers, an archive member) and broke another combination. Each test
fixed one axis. This table scans one case folder under every combination of

- the folder argument: absolute, relative to the working folder, or ``.`` (the working folder is the case folder) —
  an absolute name longer than PATH_MAX is not a usable name, so that one combination must be refused (exit 2,
  Korean reason) instead of scanned;
- the working folder: shallow, or deeper than PATH_MAX (entered one folder at a time; POSIX only);
- the case folder's name: ASCII or non-ASCII (``사건 폴더``);
- ``--workers`` 1 or 4;
- the files in a folder, or the same files inside a zip in the folder;

and requires every combination's rows — path, status, verdict, reason, coverage (check, status, reason) and evidence
titles — to be identical to the first combination of its folder/archive group. Scans run in-process through the CLI
(``cli.main``) with ``--pixel off`` to keep the table fast; the audio row runs where librosa is installed and is the
same skipped row everywhere it is not.

R28-1 (round 28) added the upload path as an axis for the checks that read a file's name (the platform-trace file-name
format check): a folder scan (workers 1/4), a zip upload, a single upload on ``/api/analyze-upload`` (web and
api-serve share ``_analyze_upload_payload``) and the single-file ``/api/check`` upload must give the same
``file_name_pattern`` coverage, file-name fact item and path limitation — single uploads used to run that check on
the server's temp file name and report 「일치 없음」.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import itertools
import json
import os
import shutil
import tempfile
import unicodedata
import unittest
import wave
import zipfile
from pathlib import Path

from deepfake_lens.tests.png_fixture import write_png

from deepfake_lens.cli import main

DEEP_LEVELS = 22  # 22 folders of 200 characters: > 4096 bytes (Linux PATH_MAX) and > 1024 (macOS)


def _png(path: Path, text: dict[str, str]) -> Path:
    # Pillow-free (the API venv may lack Pillow): PNG with tEXt chunks
    return write_png(path, text)


def _wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x10\x00\xf0" * 4000)


def _fill(folder: Path) -> None:
    folder.mkdir()
    _png(folder / "a1111.png", {"parameters": "a cat\nNegative prompt: x\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1"})
    _png(folder / "한글 사진.png", {})
    _wav(folder / "소리 a.wav")
    (folder / "note.txt").write_text("평범한 메모입니다. " * 20, encoding="utf-8")
    (folder / "broken.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)


def _rows(payload: dict) -> list[dict]:
    rows = []
    for item in payload["items"]:
        result = item.get("result") or {}
        rows.append({
            "path": item.get("path"), "member": item.get("member"), "status": item.get("status"),
            "error": item.get("error"), "verdict": result.get("verdict_code"),
            "coverage": [(c.get("check"), c.get("status"), c.get("reason")) for c in result.get("coverage") or []],
            "evidence": [e.get("title") for e in result.get("evidence") or []],
        })
    return sorted(rows, key=lambda row: (str(row["path"]), str(row["member"])))


class AxisProductTableTest(unittest.TestCase):
    maxDiff = None

    def _cd(self, base: Path, parts: list[str]) -> None:
        """Enter ``base/parts…`` one folder at a time (a folder deeper than PATH_MAX has no usable absolute name)."""
        os.chdir(base)
        for part in parts:
            os.chdir(part)

    def _scan(self, base: Path, cwd_parts: list[str], folder_arg: str | None, workers: int, out: Path) -> list[dict]:
        previous = os.getcwd()
        try:
            self._cd(base, cwd_parts)
            arg = folder_arg if folder_arg is not None else os.getcwd()
            if folder_arg is None:  # the absolute form: named from the parent
                os.chdir("..")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                code = main(["scan", arg, "--pixel", "off", "--workers", str(workers), "--format", "json", "--json-out", str(out)])
        finally:
            os.chdir(previous)
        self.assertEqual(code, 0, err.getvalue()[:300])
        return _rows(json.loads(out.read_text(encoding="utf-8")))

    def _assert_refused(self, base: Path, cwd_parts: list[str]) -> None:
        previous = os.getcwd()
        try:
            self._cd(base, cwd_parts)
            arg = os.getcwd()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                code = main(["scan", arg, "--pixel", "off", "--format", "json"])
        finally:
            os.chdir(previous)
        self.assertEqual(code, 2)
        self.assertTrue(err.getvalue().startswith("오류: "), err.getvalue()[:200])
        self.assertNotIn("Traceback", err.getvalue())

    def _make(self, base: Path, parent_parts: list[str], name: str, layout: str) -> None:
        previous = os.getcwd()
        try:
            os.chdir(base)
            for part in parent_parts:
                os.mkdir(part)
                os.chdir(part)
            if layout == "folder":
                _fill(Path(name))
            else:
                _fill(Path("staging"))
                Path(name).mkdir()
                with zipfile.ZipFile(Path(name) / "evidence.zip", "w") as archive:
                    for file in sorted(Path("staging").iterdir()):
                        archive.write(file, file.name)
                shutil.rmtree("staging")
        finally:
            os.chdir(previous)

    def test_every_axis_combination_gives_the_same_rows(self) -> None:
        depths = ["shallow", "deep"] if os.name == "posix" else ["shallow"]
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            out = base / "out.json"
            groups: dict[str, list[tuple[tuple, list[dict]]]] = {"folder": [], "archive": []}
            for index, (depth, name, layout) in enumerate(itertools.product(depths, ["case", "사건 폴더"], ["folder", "archive"])):
                parent = [f"p{index}"]
                if depth == "deep":
                    parent += [f"{level:02d}" + "d" * 198 for level in range(DEEP_LEVELS)]
                self._make(base, parent, name, layout)
                case = [*parent, name]
                forms = [("absolute", case, None), ("relative", parent, name), ("dot", case, ".")]
                for (form, cwd_parts, arg), workers in itertools.product(forms, [1, 4]):
                    if depth == "deep" and form == "absolute":
                        # An absolute name longer than PATH_MAX names no file the OS can open (ENAMETOOLONG): the
                        # combination is a usage error — exit 2 with the Korean reason, never a traceback or a scan.
                        self._assert_refused(base, cwd_parts)
                        continue
                    rows = self._scan(base, cwd_parts, arg, workers, out)
                    groups[layout].append(((depth, name, form, workers), rows))
            for layout, scans in groups.items():
                first_key, first_rows = scans[0]
                self.assertTrue(first_rows, layout)
                for key, rows in scans[1:]:
                    with self.subTest(layout=layout, combination=key, against=first_key):
                        self.assertEqual(rows, first_rows)


# R28-1: names that match a registered format, each with bytes of its kind (the name check does not decode them).
NAME_CASES = {
    "KakaoTalk_20240101_123456789.png": None,  # PNG written by _png
    "Screenshot_20240101-123456.png": None,
    "PTT-20240101-WA0001.opus": b"OggS\x00\x02" + b"\x00" * 60,
}


def _multipart(*files: tuple[str, bytes]) -> tuple[str, bytes]:
    boundary = "r28axis"
    body = b"".join(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\n\r\n".encode("utf-8") + data + b"\r\n"
        for name, data in files
    ) + f"--{boundary}--\r\n".encode("ascii")
    return f"multipart/form-data; boundary={boundary}", body


def _name_facts(item: dict) -> tuple:
    """The parts of a row that depend on the file's name (R28-1)."""
    from deepfake_lens.platform_traces import FILE_NAME_CHECK, PATH_LIMITATION

    result = item.get("result") or {}
    return (
        [(c.get("check"), c.get("status"), c.get("reason")) for c in result.get("coverage") or [] if c.get("check") == FILE_NAME_CHECK],
        [e.get("title") for e in result.get("evidence") or [] if str(e.get("title") or "").startswith("파일 이름 형식")],
        PATH_LIMITATION in (result.get("limitations") or []),
    )


class UploadPathNameDependentCheckTest(unittest.TestCase):
    """R28-1: upload path × name-dependent check — every path checks the client's name, never a temp name."""

    maxDiff = None

    def _files(self, folder: Path) -> dict[str, bytes]:
        folder.mkdir()
        out: dict[str, bytes] = {}
        for name, data in NAME_CASES.items():
            if data is None:
                _png(folder / name, {})
            else:
                (folder / name).write_bytes(data)
            out[name] = (folder / name).read_bytes()
        return out

    def test_every_upload_path_checks_the_client_name_like_the_folder_scan(self) -> None:
        from deepfake_lens import webapp_api

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            files = self._files(base / "case")
            paths: dict[str, dict[str, tuple]] = {}
            for workers in (1, 4):
                out = base / f"scan{workers}.json"
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                    code = main(["scan", str(base / "case"), "--pixel", "off", "--workers", str(workers), "--format", "json", "--json-out", str(out)])
                self.assertEqual(code, 0, err.getvalue()[:300])
                paths[f"folder scan w{workers}"] = {item["path"]: _name_facts(item) for item in json.loads(out.read_text(encoding="utf-8"))["items"]}
            with zipfile.ZipFile(base / "case.zip", "w") as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            ctype, body = _multipart(("case.zip", (base / "case.zip").read_bytes()))
            zipped = webapp_api._analyze_upload_payload(ctype, body)["items"]
            paths["zip upload"] = {item["member"]: _name_facts(item) for item in zipped if item.get("member")}
            singles: dict[str, tuple] = {}
            checked: dict[str, tuple] = {}
            for name, data in files.items():
                ctype, body = _multipart((name, data))
                upload = webapp_api._analyze_upload_payload(ctype, body)
                singles[name] = _name_facts(upload["items"][0])
                check = webapp_api._check_file_payload(ctype, body)
                checked[name] = _name_facts(check["item"])
                for payload in (upload, check):
                    text = json.dumps(payload, ensure_ascii=False)
                    self.assertNotIn("dflens-up-", text)  # the temp folder never reaches a row
                    self.assertNotIn(str(base), text)
            paths["single upload (/api/analyze-upload)"] = singles
            paths["single upload (/api/check)"] = checked
            if importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None:
                from fastapi.testclient import TestClient

                from deepfake_lens import api_server

                client = TestClient(api_server.create_app(default_folder=base / "case"), raise_server_exceptions=False)
                served: dict[str, tuple] = {}
                for name, data in files.items():
                    ctype, body = _multipart((name, data))
                    response = client.post("/api/analyze-upload", content=body, headers={"host": "localhost", "content-type": ctype, "X-Deepfake-Lens-Client": "gui"})
                    self.assertEqual(response.status_code, 200, response.text[:300])
                    served[name] = _name_facts(response.json()["items"][0])
                paths["single upload (api-serve)"] = served
            reference = paths.pop("folder scan w1")
            self.assertEqual(sorted(reference), sorted(NAME_CASES))
            for name, facts in reference.items():
                coverage, items, limited = facts
                self.assertEqual([status for _check, status, _reason in coverage], ["ran"], name)
                self.assertIn("이름 형식 일치", coverage[0][2], name)
                self.assertEqual(len(items), 1, name)
                self.assertTrue(limited, name)
            for label, rows in paths.items():
                with self.subTest(path=label):
                    self.assertEqual(rows, reference)

    def test_a_name_that_cannot_be_a_file_name_is_not_reported_as_checked(self) -> None:
        # R28-1: a client name this system cannot create (here: longer than NAME_MAX) is analyzed under a stand-in
        # name — the name check is then "skipped" with the reason, never 「일치 없음」 for a name it did not check.
        from deepfake_lens import webapp_api
        from deepfake_lens.platform_traces import FILE_NAME_CHECK

        with tempfile.TemporaryDirectory() as tmp:
            data = _png(Path(tmp) / "x.png", {}).read_bytes()
        name = "KakaoTalk_20240101_123456789" + "가" * 300 + ".png"  # > 255 characters and > 255 bytes
        ctype, body = _multipart((name, data))
        for label, payload in (("analyze-upload", webapp_api._analyze_upload_payload(ctype, body)), ("check", webapp_api._check_file_payload(ctype, body))):
            item = payload["items"][0] if "items" in payload else payload["item"]
            with self.subTest(path=label):
                self.assertEqual(item["status"], "analyzed")
                self.assertEqual(item["name"], name)
                coverage = [c for c in item["result"]["coverage"] if c["check"] == FILE_NAME_CHECK]
                self.assertEqual([(c["status"], c["reason"]) for c in coverage], [("skipped", webapp_api.UPLOAD_NAME_NOT_CHECKED)])
                self.assertNotIn("dflens-up-", json.dumps(payload, ensure_ascii=False))



# --- R29 (round 29): every upload entry point × every input class = the folder scan --------------------------------

def _zip_bytes(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
    return buffer.getvalue()


def _tgz_bytes(members: list[tuple[str, bytes]]) -> bytes:
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _form(parts: list[tuple[str | None, bytes]], field: str = "files") -> tuple[str, bytes]:
    """A multipart body; each name travels as RFC 5987 ``filename*`` (NUL, surrogate escapes, newlines), None = no file name."""
    from urllib.parse import quote

    boundary = "r29matrix"
    body = b""
    for name, data in parts:
        disposition = f'form-data; name="{field}"'
        if name is not None:
            disposition += "; filename=\"x\"; filename*=UTF-8''" + quote(name.encode("utf-8", "surrogateescape"), safe="")
        body += f"--{boundary}\r\nContent-Disposition: {disposition}\r\nContent-Type: application/octet-stream\r\n\r\n".encode("ascii") + data + b"\r\n"
    return f"multipart/form-data; boundary={boundary}", body + f"--{boundary}--\r\n".encode("ascii")


def _browser_form(parts: list[tuple[str | bytes, bytes, str | None]], field: str = "files",
                  boundary: str = "----WebKitFormBoundaryR30axis") -> tuple[str, bytes]:
    """A multipart body as a browser (and curl) writes it — R30-1..R30-3 (round 30).

    Each part is ``(name, bytes, part Content-Type or None)``. The name travels as a quoted ``filename="…"`` in the
    HTML Standard's encoding: its bytes (UTF-8 for text, else as given) with ``"`` → ``%22``, CR → ``%0D``,
    LF → ``%0A`` and nothing else escaped — a backslash is sent literally. The part's ``Content-Type`` is what a
    browser takes from the extension (``message/rfc822`` for ``.eml``, ``multipart/related`` for ``.mht``).
    """
    body = b""
    for name, data, part_type in parts:
        raw = name if isinstance(name, bytes) else name.encode("utf-8", "surrogateescape")
        raw = raw.replace(b'"', b"%22").replace(b"\r", b"%0D").replace(b"\n", b"%0A")
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="'.encode("ascii") + raw + b'"'
        if part_type is not None:
            head += f"\r\nContent-Type: {part_type}".encode("ascii")
        body += head + b"\r\n\r\n" + data + b"\r\n"
    return f"multipart/form-data; boundary={boundary}", body + f"--{boundary}--\r\n".encode("ascii")


# Identity fields an upload row takes from the client's name (R10-6, R11-14, R12-4); the rest of a row is the scan's.
_IDENTITY = ("path", "name", "display_name", "path_b64", "container", "source")


class UploadEntryPointEquivalenceTest(unittest.TestCase):
    """R29: upload entry points × input classes — every row equals the folder scan's row of the same bytes.

    Rounds 28 and 29 each found one more upload path that gave a different result for the same file (R28-1 the
    temp name, R29-1 a 0-byte file refused by ``/api/check``, R29-2 an archive whose name cannot be a file name here
    failing whole or a 500). This table runs every input class through every entry point that takes file bytes —
    the live ``web`` server's ``/api/analyze-upload`` and ``/api/check``, api-serve's ``/api/analyze-upload``
    (TestClient) — and requires each row to be the row ``webapp_api._scan_run`` gives for a folder holding the same
    bytes under the client's base name: identical except the client-name fields (``_IDENTITY``), whose path is the
    folder row's path under the client's name. A client name this system cannot create is compared with the folder
    scan under the stand-in name (``upload.png``, ``upload.zip``, ``upload.tar.gz``) with the name check skipped and
    its reason (R28-1, R29-2) — the folder path cannot hold that name. No request may answer 500.
    """

    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        from deepfake_lens.tests.png_fixture import write_png

        cls._tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls._tmp.name).resolve()
        from unittest.mock import patch

        cls._env = patch.dict(os.environ, {"DEEPFAKE_LENS_SCAN_JOBS": str(cls.base / "jobs")})
        cls._env.start()
        write_png(cls.base / "p.png", {})
        png = (cls.base / "p.png").read_bytes()
        _wav(cls.base / "w.wav")
        text = "평범한 글입니다. 여러 문장이 있습니다. 사람이 쓴 메모입니다.".encode("utf-8")
        member_zip = _zip_bytes([("in/KakaoTalk_20240101_123456789.png", png), ("t.txt", text)])
        cls.bytes = {
            "png": png, "zero": b"", "png_truncated": png[:40], "txt": text, "wav": (cls.base / "w.wav").read_bytes(),
            "garbage": b"\x00\x01not a media file" * 8, "zip": member_zip,
            "zip_nested": _zip_bytes([("inner.zip", member_zip), ("a.png", png)]),
            "zip_corrupt": b"PK\x03\x04" + b"\x00" * 40, "tgz": _tgz_bytes([("a.png", png), ("b.txt", text)]),
        }

    @classmethod
    def tearDownClass(cls) -> None:
        cls._env.stop()
        cls._tmp.cleanup()

    # (label, client name, bytes key) — one file per request.
    K = "KakaoTalk_20240101_123456789"
    SINGLE = [
        ("png", K + ".png", "png"),
        ("0-byte", K + ".png", "zero"),  # R29-1
        ("0-byte txt", "빈 메모.txt", "zero"),
        ("truncated", K + ".png", "png_truncated"),
        ("png named .jpg", K + ".jpg", "png"),
        ("text named .png", "x.png", "txt"),
        ("garbage named .mp4", "screen-20240101-123456.mp4", "garbage"),
        ("txt", K + ".txt", "txt"),
        ("wav", "통화 녹음 홍길동_240101_123456.wav", "wav"),
        ("zip", "case.zip", "zip"),
        ("nested zip", "n.zip", "zip_nested"),
        ("corrupt zip", "bad.zip", "zip_corrupt"),
        ("tar.gz", "case.tar.gz", "tgz"),
        ("upper-case .ZIP", "CASE.ZIP", "zip"),
        ("zip, 300-char name", "A" * 300 + ".zip", "zip"),  # R29-2
        ("zip, 270-byte name", "가" * 90 + ".zip", "zip"),  # R29-2 (a file name on NTFS, not on ext4/APFS-255-byte)
        ("zip, NUL in name", "nul\x00x.zip", "zip"),  # R29-2
        ("tar.gz, 300-char name", "A" * 300 + ".tar.gz", "tgz"),  # R29-2: the stand-in keeps .tar.gz
        ("zip, trailing backslash", "x.zip\\", "zip"),  # R29-3
        ("zip, Windows path", "C:\\Users\\me\\case.zip", "zip"),
        ("traversal", "../../../../etc/" + K + ".png", "png"),
        ("absolute", "/etc/" + K + ".png", "png"),
        ("Windows path", "C:\\Users\\me\\" + K + ".png", "png"),
        ("trailing slash", K + ".png/", "png"),
        ("dot", ".", "png"),
        ("dot-dot", "..", "png"),
        ("slash", "/", "png"),
        ("NUL", K + "\x00.png", "png"),
        ("300 chars", K + "x" * 300 + ".png", "png"),
        ("NFD", unicodedata.normalize("NFD", "스크린샷 2024-01-01 오후 1.02.03.png"), "png"),
        ("surrogate escape", "caf\udce9.png", "png"),
        ("colons", "t:::c.png", "png"),
        ("newline", "a\nb.png", "png"),
        ("trailing space", K + ".png ", "png"),
        ("stand-in name", "upload.png", "png"),
        ("temp prefix", "dflens-up-abc.png", "png"),
        ("Windows device", "CON.png", "png"),
        ("30-char suffix", "x." + "p" * 30, "png"),
    ]

    # -- the reference: a folder scan ----------------------------------------------------------------------------

    def _folder_rows(self, client: str, data: bytes) -> list[dict]:
        """The folder scan's rows for ``data`` under the client's base name (else the stand-in, name check skipped)."""
        from urllib.parse import quote

        from deepfake_lens import webapp_api
        from deepfake_lens.platform_traces import FILE_NAME_CHECK
        from deepfake_lens.result_text import escape_row_path, member_row_path

        folder = Path(tempfile.mkdtemp(dir=self.base))
        # The client's base name: the last "/"- or "\\"-separated component (a client OS path); the stand-in keeps
        # the suffix that decides the kind, a compound archive suffix whole.
        base = client.replace("\\", "/").rstrip("/").rpartition("/")[2]
        compound = next((base[-len(s):] for s in (".tar.gz", ".tar.bz2", ".tar.xz") if base.lower().endswith(s)), None)
        stand_in = "upload" + (compound or Path(base).suffix[:16])
        written, named = None, False
        for name, is_client in ([(base, True)] if base not in ("", ".", "..") else []) + [(stand_in, False)]:
            try:
                (folder / name).write_bytes(data)
            except (OSError, ValueError, UnicodeError):
                continue
            written, named = name, is_client
            break
        self.assertIsNotNone(written, client)
        payload, _context = webapp_api._scan_run(f"folder={quote(str(folder))}", default_folder=folder)
        rows = payload["items"]
        shown, old = escape_row_path(client), escape_row_path(written)
        for row in rows:
            if row.get("member") is not None:
                row["path"] = member_row_path(shown, row["member"]) if written != client else row["path"]
            else:
                self.assertEqual(row["path"], old)
                row["path"] = shown
                if not named:
                    for entry in (row.get("result") or {}).get("coverage") or []:
                        if entry.get("check") == FILE_NAME_CHECK:
                            entry.update(status="skipped", reason=webapp_api.UPLOAD_NAME_NOT_CHECKED)
        return rows

    @staticmethod
    def _body(row: dict) -> dict:
        return {key: value for key, value in row.items() if key not in _IDENTITY}

    def _assert_rows(self, label: str, client_rows: list[tuple[str, list[dict]]], got: list[dict]) -> None:
        """``got`` = the concatenated folder-scan rows of every (client name, rows) in order, path for path."""
        expected = [row for _client, rows in client_rows for row in rows]
        self.assertEqual([row.get("path") for row in got], [row["path"] for row in expected], label)
        self.assertEqual([self._body(row) for row in got], [self._body(row) for row in expected], label)
        tops = {client for client, _rows in client_rows}
        for row in got:
            self.assertEqual(row.get("source"), "upload", label)
            if row.get("member") is None:
                self.assertIn(row.get("name"), tops, label)  # the container/single row is named by the client
        text = json.dumps(got, ensure_ascii=False)
        self.assertNotIn("dflens-up-", text.replace("dflens-up-abc", ""), label)  # no temp folder reaches a row
        self.assertNotIn(str(self.base), text, label)

    # -- the entry points ----------------------------------------------------------------------------------------

    def _entry_points(self):
        """name -> post(path, content type, body) -> (status, JSON) for web (live HTTP) and api-serve (TestClient)."""
        import threading
        import urllib.error
        import urllib.request

        from collections import OrderedDict
        from unittest.mock import patch

        from deepfake_lens import webapp_api
        from deepfake_lens.webapp import CLIENT_HEADER, build_server

        # The servers register their folder as a read root (module state) — restored after each test.
        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        server = build_server("127.0.0.1", 0, default_folder=self.base)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        origin = f"http://127.0.0.1:{server.server_address[1]}"

        def web(path: str, ctype: str, body: bytes) -> tuple[int, object]:
            request = urllib.request.Request(origin + path, data=body, headers={CLIENT_HEADER: "gui", "Content-Type": ctype}, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    return exc.code, json.loads(raw)
                except ValueError:
                    return exc.code, raw.decode("utf-8", "replace")

        points = {"web": web}
        if importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None:
            from fastapi.testclient import TestClient

            from deepfake_lens import api_server

            client = TestClient(api_server.create_app(default_folder=self.base), raise_server_exceptions=False)

            def api(path: str, ctype: str, body: bytes) -> tuple[int, object]:
                response = client.post(path, content=body, headers={"host": "localhost", "content-type": ctype, CLIENT_HEADER: "gui"})
                try:
                    return response.status_code, response.json()
                except ValueError:
                    return response.status_code, response.text

            points["api-serve"] = api
        return points

    def _upload_legs(self):
        """(leg label, post, path) of every leg that answers with upload rows (``items``)."""
        points = self._entry_points()
        return [(f"{name} /api/analyze-upload", post, "/api/analyze-upload") for name, post in points.items()], points["web"]

    # -- the table -----------------------------------------------------------------------------------------------

    def test_one_file_per_request_every_entry_point_equals_the_folder_scan(self) -> None:
        legs, web = self._upload_legs()
        for label, client, key in self.SINGLE:
            data = self.bytes[key]
            expected = [(client, self._folder_rows(client, data))]
            ctype, body = _form([(client, data)])
            for leg, post, path in legs:
                with self.subTest(case=label, leg=leg):
                    status, payload = post(path, ctype, body)
                    self.assertEqual(status, 200, payload)
                    self._assert_rows(f"{label} / {leg}", expected, payload["items"])
            with self.subTest(case=label, leg="web /api/check"):
                ctype, body = _form([(client, data)], field="file")
                status, payload = web("/api/check", ctype, body)
                self.assertEqual(status, 200, payload)  # R29-1 (0-byte 400), R29-2 (bad archive name 500)
                rows = payload["items"] if payload.get("mode") == "files" else [payload["item"]]
                archive = any(row.get("kind") == "archive" for row in expected[0][1])
                self.assertEqual(payload["mode"], "files" if archive else "file", label)
                self._assert_rows(f"{label} / web /api/check", expected, rows)

    def test_several_files_in_one_request(self) -> None:
        legs, _web = self._upload_legs()
        b = self.bytes
        # several parts (one an archive), a duplicate name with other bytes, a 0-byte file, a part without a file name
        parts = [(self.K + ".png", b["png"]), ("case.zip", b["zip"]), (None, b"a form field, not a file"),
                 (self.K + ".png", b["png_truncated"]), ("빈.txt", b""),
                 ("가" * 90 + ".zip", b["zip"])]  # R29-2
        expected = [(name, self._folder_rows(name, data)) for name, data in parts if name is not None]
        ctype, body = _form(parts)
        for leg, post, path in legs:
            with self.subTest(leg=leg):
                status, payload = post(path, ctype, body)
                self.assertEqual(status, 200, payload)
                self._assert_rows(leg, expected, payload["items"])
                self.assertEqual(payload["summary"]["total"], sum(len(rows) for _name, rows in expected))

    def test_no_part_with_a_file_name_is_the_same_refusal_everywhere(self) -> None:
        legs, web = self._upload_legs()
        for leg, post, path in legs + [("web /api/check", web, "/api/check")]:
            for parts in ([(None, b"field")], [(None, b"")], [("", b"data")]):
                with self.subTest(leg=leg, parts=parts):
                    ctype, body = _form(parts, field="files" if "upload" in path else "file")
                    self.assertEqual(post(path, ctype, body), (400, {"error": "업로드된 파일이 없습니다"}))

    def test_the_upload_cap_counts_files_not_rows(self) -> None:
        # R29-4: the cap counted rows — an archive's 3 rows made the next file of a 3-file upload "상한 초과" under a
        # cap of 2 (20 in production). Every file over the cap is now its own skipped row with the true count.
        from unittest.mock import patch

        from deepfake_lens import webapp_api

        legs, _web = self._upload_legs()
        b = self.bytes
        capped = [("case.zip", b["zip"]), (self.K + ".png", b["png"]), ("t.txt", b["txt"]), ("u.txt", b["txt"])]
        ctype, body = _form(capped)
        with patch.object(webapp_api, "MAX_UPLOAD_FILES", 2):
            for leg, post, path in legs:
                with self.subTest(leg=leg):
                    status, payload = post(path, ctype, body)
                    self.assertEqual(status, 200, payload)
                    rows = payload["items"]
                    self._assert_rows(leg, [(name, self._folder_rows(name, data)) for name, data in capped[:2]], rows[:-2])
                    self.assertEqual([{k: row.get(k) for k in ("path", "name", "status", "error", "source")} for row in rows[-2:]],
                                     [{"path": name, "name": name, "status": "skipped", "source": "upload",
                                       "error": webapp_api.UPLOAD_FILES_OVER_CAP.format(cap=2, count=4)} for name in ("t.txt", "u.txt")])
                    self.assertEqual(payload["summary"]["skipped"], 2)

    def test_the_quick_check_refuses_several_files_instead_of_dropping_them(self) -> None:
        # R29-5: /api/check analyzed the first file part and silently dropped the others.
        from deepfake_lens import webapp_api

        _legs, web = self._upload_legs()
        for parts in ([(self.K + ".png", self.bytes["png"]), ("b.txt", self.bytes["txt"])],
                      [("a.png", b""), (None, b"field"), ("b.png", b""), ("case.zip", self.bytes["zip"])]):
            with self.subTest(parts=[name for name, _data in parts]):
                ctype, body = _form(parts, field="file")
                count = sum(1 for name, _data in parts if name)
                self.assertEqual(web("/api/check", ctype, body), (400, {"error": webapp_api.CHECK_ONE_FILE.format(count=count)}))

    def test_an_analysis_that_raises_is_the_same_failed_row_on_every_entry_point(self) -> None:
        # R29-2: /api/check had no handler for an exception (a 500); every leg now gives /api/analyze-upload's failed row.
        from unittest.mock import patch

        from deepfake_lens import webapp_api

        def boom(*_args, **_kwargs):
            raise RuntimeError("주입된 실패")

        legs, web = self._upload_legs()
        for client, key, target in ((self.K + ".png", "png", "analyze_path"), ("case.zip", "zip", "analyze_rows")):
            with patch.object(webapp_api, target, boom):
                rows = []
                ctype, body = _form([(client, self.bytes[key])])
                for leg, post, path in legs:
                    status, payload = post(path, ctype, body)
                    self.assertEqual(status, 200, payload)
                    rows.append((leg, payload["items"]))
                ctype, body = _form([(client, self.bytes[key])], field="file")
                status, payload = web("/api/check", ctype, body)
                self.assertEqual(status, 200, payload)
                rows.append(("web /api/check", payload["items"] if payload.get("mode") == "files" else [payload["item"]]))
            for leg, got in rows:
                with self.subTest(file=client, leg=leg):
                    self.assertEqual([(r["path"], r["status"], r["error"]) for r in got], [(client, "failed", "RuntimeError: 주입된 실패")])

    def test_compare_takes_exactly_two_files_empty_or_not_and_never_answers_500(self) -> None:
        # R29-6: /api/compare dropped a 0-byte file (two files sent: "비교할 파일 2개가 필요합니다"), ignored a third,
        # and a client name this system cannot create made its temp file fail (a 500).
        from deepfake_lens import webapp_api

        _legs, web = self._upload_legs()
        text = self.bytes["txt"]
        for label, parts in (("two 0-byte texts", [("a.txt", b""), ("b.txt", b"")]),
                             ("300-char and NUL names", [("A" * 300 + ".txt", text), ("n\x00.txt", text)]),
                             ("traversal names", [("../../a.txt", text), ("C:\\x\\b.txt", text)])):
            with self.subTest(case=label):
                ctype, body = _form(parts)
                status, payload = web("/api/compare", ctype, body)
                self.assertEqual(status, 200, payload)
                self.assertNotIn("dflens-up-", json.dumps(payload, ensure_ascii=False))
        # R30-4 (round 30): a side with nothing to measure — a 0-byte WAV, a PNG named .wav, an empty text — gives no
        # number (it showed 「0」, 「거리 1」 and 「0/100」 as if measured).
        png = self.bytes["png"]
        for label, parts in (("0-byte wav", [("a.wav", self.bytes["wav"]), ("b.wav", b"")]),
                             ("png named .wav", [("a.wav", self.bytes["wav"]), ("b.wav", png)]),
                             ("empty text", [("a.txt", b""), ("b.txt", text * 4)]),
                             ("blank text", [("a.txt", text * 4), ("b.txt", b" \r\n\t")])):
            with self.subTest(case=label):
                ctype, body = _form(parts)
                status, payload = web("/api/compare", ctype, body)
                self.assertEqual(status, 200, payload)
                self.assertEqual((payload["raw_score"], payload["diagnostic"]["distance"], payload["reference_band"]), (None, None, "unavailable"), payload)
        for count in (1, 3):
            with self.subTest(files=count):
                ctype, body = _form([(f"{i}.txt", text) for i in range(count)])
                self.assertEqual(web("/api/compare", ctype, body), (400, {"error": webapp_api.COMPARE_TWO_FILES.format(count=count)}))

    # -- R30 (round 30): what a browser sends ----------------------------------------------------------------------

    # The part Content-Types a browser attaches (Chromium: by extension) — the bytes are the file whatever it says.
    PART_TYPES = ("application/octet-stream", "message/rfc822", "multipart/related", "multipart/mixed",
                  "text/plain; charset=utf-8", None)
    EML = b"From: a@example.com\r\nTo: b@example.com\r\nSubject: hi\r\nContent-Type: text/plain\r\n\r\nbody\r\n"
    MHT = (b"MIME-Version: 1.0\r\nContent-Type: multipart/related; boundary=\"mhtb\"\r\n\r\n--mhtb\r\n"
           b"Content-Type: text/html\r\n\r\n<html>x</html>\r\n--mhtb--\r\n")

    def _part_type_files(self) -> list[tuple[str, bytes]]:
        b = self.bytes
        return [("mail.eml", self.EML), ("page.mht", self.MHT), (self.K + ".png", b["png"]), ("빈.txt", b""),
                ("case.zip", b["zip"]), ("x.bin", bytes(range(256)) * 4)]

    def _assert_sha256(self, label: str, client: str, data: bytes, rows: list[dict]) -> None:
        import hashlib

        top = [row for row in rows if row.get("member") is None]
        self.assertEqual([row.get("name") for row in top], [client], label)
        if top[0].get("sha256") is not None:
            self.assertEqual(top[0]["sha256"], hashlib.sha256(data).hexdigest(), label)  # R30-2: the bytes sent

    def test_part_content_type_never_changes_or_drops_the_file(self) -> None:
        # R30-1: a message/* part (a browser's .eml) vanished — alone 「업로드된 파일이 없습니다」, among others no row.
        # R30-2: a multipart/* part (a browser's .mht) was re-serialized — the analyzed bytes and sha256 were not the file's.
        from deepfake_lens import webapp_api

        legs, web = self._upload_legs()
        files = self._part_type_files()
        expected = {client: self._folder_rows(client, data) for client, data in files}
        for part_type in self.PART_TYPES:
            for client, data in files:
                for leg, post, path in legs + [("web /api/check", web, "/api/check")]:
                    with self.subTest(part_type=part_type, file=client, leg=leg):
                        ctype, body = _browser_form([(client, data, part_type)], field="file" if "check" in path else "files")
                        status, payload = post(path, ctype, body)
                        self.assertEqual(status, 200, payload)
                        rows = payload["items"] if "items" in payload else [payload["item"]]
                        self._assert_rows(f"{part_type} {client} {leg}", [(client, expected[client])], rows)
                        self._assert_sha256(f"{part_type} {client} {leg}", client, data, rows)
            # Several files in one request: every file is a row, and the cap counts the files sent.
            with self.subTest(part_type=part_type, files="all"):
                ctype, body = _browser_form([(client, data, part_type) for client, data in files])
                for leg, post, path in legs:
                    status, payload = post(path, ctype, body)
                    self.assertEqual(status, 200, payload)
                    self._assert_rows(f"{part_type} all {leg}", [(client, expected[client]) for client, _data in files], payload["items"])
                many = [(f"{i}.eml", self.EML, part_type) for i in range(3)] + [(f"{i}.png", self.bytes["png"], None) for i in range(3)]
                ctype, body = _browser_form(many)
                from unittest.mock import patch

                with patch.object(webapp_api, "MAX_UPLOAD_FILES", 4):
                    for leg, post, path in legs:
                        status, payload = post(path, ctype, body)
                        self.assertEqual(status, 200, payload)
                        skipped = [row["error"] for row in payload["items"] if row.get("status") == "skipped"]
                        self.assertEqual(skipped, [webapp_api.UPLOAD_FILES_OVER_CAP.format(cap=4, count=6)] * 2, leg)
                # /api/check: two such files are two files; /api/compare: two are two (R29-5, R29-6 counts).
                ctype, body = _browser_form([("a.eml", self.EML, part_type), ("b.mht", self.MHT, part_type)], field="file")
                self.assertEqual(web("/api/check", ctype, body), (400, {"error": webapp_api.CHECK_ONE_FILE.format(count=2)}))
                for pair in ([("a.eml", self.EML, part_type)], [("a.eml", self.EML, part_type)] * 3):
                    ctype, body = _browser_form(pair)
                    self.assertEqual(web("/api/compare", ctype, body), (400, {"error": webapp_api.COMPARE_TWO_FILES.format(count=len(pair))}))
                ctype, body = _browser_form([("a.wav", self.bytes["wav"], part_type), ("mail.eml", self.EML, part_type)])
                status, payload = web("/api/compare", ctype, body)
                self.assertEqual(status, 400, payload)
                self.assertTrue(payload["error"].startswith("지원되는 쌍이 아닙니다(.wav, .eml)"), payload)  # was 「받은 파일 1개」

    # Names as a browser encodes them: (name the user's file has, the bytes sent for it when not its UTF-8).
    BROWSER_NAMES = [
        ("a\\b.png", None),
        ("KakaoTalk_2024010\\1_123456789.png", None),  # not the KakaoTalk format: it read 「이름 형식 일치」 (R30-3)
        ("case.zip\\", None),  # R29-3 from a browser: an archive, its member rows and container row
        ('say "hi".png', None),  # sent as say %22hi%22.png
        ("line\nbreak.png", None),  # sent as line%0Abreak.png
        ("100%25 %41.png", None),  # a percent the browser does not escape stays literal
        ("caf\udce9.png", b"caf\xe9.png"),  # neither UTF-8 nor CP949: kept byte for byte
        ("증거.png", "증거.png".encode("cp949")),  # CP949 bytes (R12-10)
    ]

    def test_browser_encoded_names_are_the_names_of_the_files(self) -> None:
        from deepfake_lens.platform_traces import FILE_NAME_CHECK

        legs, web = self._upload_legs()
        data = self.bytes["png"]
        for client, sent in self.BROWSER_NAMES:
            payload_bytes = self.bytes["zip"] if client.startswith("case.zip") else data
            expected = [(client, self._folder_rows(client, payload_bytes))]
            for leg, post, path in legs + [("web /api/check", web, "/api/check")]:
                with self.subTest(name=client, leg=leg):
                    ctype, body = _browser_form([(sent if sent is not None else client, payload_bytes, "image/png")],
                                                field="file" if "check" in path else "files")
                    status, payload = post(path, ctype, body)
                    self.assertEqual(status, 200, payload)
                    rows = payload["items"] if "items" in payload else [payload["item"]]
                    self._assert_rows(f"{client} {leg}", expected, rows)
                    self._assert_sha256(f"{client} {leg}", client, payload_bytes, rows)
                    if client.startswith("case.zip"):
                        self.assertTrue(any(row.get("member") is not None for row in rows), rows)  # expanded (R29-3)
                    if client.startswith("KakaoTalk_2024010"):
                        name_check = [c for c in rows[0]["result"]["coverage"] if c.get("check") == FILE_NAME_CHECK]
                        self.assertNotIn("일치:", str(name_check), rows[0]["name"])

    def test_boundary_refusals_say_what_is_wrong(self) -> None:
        # R30-5: no boundary, an empty one and one the body never uses all said 「업로드 본문이 잘렸습니다(닫는 경계 없음)」.
        from deepfake_lens import form_data

        legs, web = self._upload_legs()
        good_type, body = _browser_form([(self.K + ".png", self.bytes["png"], "image/png")])
        cases = [("multipart/form-data", body, form_data.MULTIPART_NO_BOUNDARY),
                 ("multipart/form-data; boundary=", body, form_data.MULTIPART_NO_BOUNDARY),
                 ('multipart/form-data; boundary=""', body, form_data.MULTIPART_NO_BOUNDARY),
                 ("multipart/form-data; boundary=wrong", body, form_data.MULTIPART_BOUNDARY_MISMATCH),
                 ("multipart/form-data; boundary=----WebKitFormBoundaryR30", body, form_data.MULTIPART_BOUNDARY_MISMATCH),
                 (good_type, body[:-8], form_data.MULTIPART_INCOMPLETE),  # the closing boundary cut off (P8)
                 (good_type, body[:len(body) // 2], form_data.MULTIPART_INCOMPLETE),
                 (good_type, body[:12], form_data.MULTIPART_INCOMPLETE)]
        for leg, post, path in legs + [("web /api/check", web, "/api/check"), ("web /api/compare", web, "/api/compare")]:
            for ctype, sent, reason in cases:
                with self.subTest(leg=leg, ctype=ctype, size=len(sent)):
                    self.assertEqual(post(path, ctype, sent), (400, {"error": reason}))


if __name__ == "__main__":
    unittest.main()

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


if __name__ == "__main__":
    unittest.main()

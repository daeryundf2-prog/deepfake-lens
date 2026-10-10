"""R12-1 / R12-2 (round 12): native decoders never see a non-ASCII file name.

R12-1: a POSIX file name that is not UTF-8 (``b"\\xc1\\xf5\\xb0\\xc5.mp4"``,
CP949 "증거" copied from a Korean Windows disk) reaches Python with lone
surrogates; ``cv2.VideoCapture`` crashed the whole process on it (SIGSEGV,
exit 139, no output, every other row lost; the web server died). R12-2: the
same name made librosa/soundfile raise ``UnicodeEncodeError`` and pymupdf
``FileDataError``, so an audio or PDF row failed where an ASCII-named copy
of the same bytes was analysed.

Every native call site now goes through
:func:`deepfake_lens.native_path.native_safe_path`; the AST meta-test below
keeps it that way. The end-to-end tests use real non-UTF-8 names made with
``os.fsencode`` and run the scan / servers in a child process, so a
regression shows as a failed assertion (exit -11) instead of killing the
test runner.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
import wave
from pathlib import Path
from unittest import mock

from deepfake_lens import native_path
from deepfake_lens.native_path import NativePathError, is_native_safe, native_safe_path
from deepfake_lens.result_types import Verdict

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parent
A1111 = REPO / "fixtures" / "benchmark" / "a1111-metadata-marker.png"
HAVE_CV2 = importlib.util.find_spec("cv2") is not None
HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None
HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None
# "증거" in CP949 — not valid UTF-8.
CP949_STEM = b"\xc1\xf5\xb0\xc5"
NON_UTF8_SKIP = "파일 시스템이 UTF-8이 아닌 파일 이름을 허용하지 않음(Windows·macOS)"
CHILD_TIMEOUT_SECONDS = 600


def _write_bytes_name(folder: Path, name: bytes, data: bytes) -> str:
    """Write ``data`` under the raw byte name; return the ``str`` name Python sees."""
    try:
        with open(os.fsencode(folder) + b"/" + name, "wb") as handle:
            handle.write(data)
    except (OSError, UnicodeError):
        raise unittest.SkipTest(NON_UTF8_SKIP)
    shown = os.fsdecode(name)
    if shown not in os.listdir(folder):
        raise unittest.SkipTest(NON_UTF8_SKIP)
    return shown


def _mp4_bytes(work: Path) -> bytes:
    """A 12-frame 64x64 mp4 written by OpenCV (ASCII temp name)."""
    import cv2
    import numpy as np

    out = work / "make.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")  # type: ignore[attr-defined]
    writer = cv2.VideoWriter(str(out), fourcc, 6.0, (64, 64))
    if not writer.isOpened():
        raise unittest.SkipTest("OpenCV mp4v 인코더 없음")
    rng = np.random.default_rng(12)
    for _ in range(12):
        writer.write(rng.integers(0, 255, (64, 64, 3), dtype=np.uint8))
    writer.release()
    data = out.read_bytes()
    out.unlink()
    return data


def _wav_bytes() -> bytes:
    """One second of a 440 Hz tone, 16 kHz mono PCM (stdlib only)."""
    import io
    import math

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 16000))) for i in range(16000)))
    return buffer.getvalue()


def _pdf_bytes() -> bytes:
    """A one-page PDF with a line of text (hand-written, offsets computed)."""
    stream = b"BT /F1 12 Tf 20 100 Td (native safe path) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


def _child_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "DEEPFAKE_LENS_LOG_DIR": str(home / "logs"),
            "PYTHONPATH": str(REPO) + os.pathsep + env.get("PYTHONPATH", ""),
        }
    )
    env.pop("DEEPFAKE_LENS_REPORT_KEY", None)
    return env


def _scan_rows(stdout: bytes) -> dict[str, dict]:
    payload = json.loads(stdout.decode("utf-8"))
    return {item["path"]: item for item in payload["items"]}


def _signature(item: dict) -> tuple:
    """What must not depend on the file name: verdict, evidence, coverage."""
    result = item.get("result") or {}
    return (
        item.get("status"),
        result.get("verdict_code"),
        sorted((e.get("title"), e.get("kind"), e.get("direction")) for e in result.get("evidence", [])),
        sorted((c.get("check"), c.get("status"), c.get("reason") or "") for c in result.get("coverage", [])),
    )


class NativeSafePathUnitTest(unittest.TestCase):
    """The helper: pass-through, staging routes, cleanup, evidence untouched."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.name = _write_bytes_name(self.root, CP949_STEM + b".mp4", b"\x00\x00\x00\x18ftypmp42" + bytes(range(200)))
        self.source = self.root / self.name

    def test_ascii_path_is_passed_through(self) -> None:
        plain = self.root / "plain.mp4"
        plain.write_bytes(b"x")
        with native_safe_path(plain) as native:
            self.assertEqual(native, str(plain))
        self.assertTrue(is_native_safe(plain))
        self.assertFalse(is_native_safe(self.source))

    def test_non_ascii_path_is_staged_under_an_ascii_name_and_removed(self) -> None:
        before = os.stat(self.source)
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with native_safe_path(self.source) as native:
            self.assertTrue(native.isascii(), native)
            self.assertTrue(native.endswith(".mp4"), native)
            self.assertNotEqual(os.path.dirname(native), str(self.root))  # never the evidence folder
            self.assertEqual(hashlib.sha256(Path(native).read_bytes()).hexdigest(), digest)
            staged = native
        self.assertFalse(os.path.lexists(staged))
        after = os.stat(self.source)
        # The evidence inode is not touched: same bytes, mtime, ctime, link count.
        self.assertEqual((before.st_mtime_ns, before.st_size, before.st_nlink), (after.st_mtime_ns, after.st_size, after.st_nlink))
        if os.name != "nt":
            self.assertEqual(before.st_ctime_ns, after.st_ctime_ns)
        self.assertEqual(sorted(os.listdir(self.root)), [self.name])

    def test_hard_link_then_read_only_copy_when_symlink_is_refused(self) -> None:
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        with mock.patch.object(native_path.os, "symlink", side_effect=OSError("refused")):
            with native_safe_path(self.source) as native:
                self.assertFalse(os.path.islink(native))
                self.assertEqual(hashlib.sha256(Path(native).read_bytes()).hexdigest(), digest)
            with mock.patch.object(native_path.os, "link", side_effect=OSError("cross-device")):
                with native_safe_path(self.source) as native:
                    self.assertFalse(os.path.islink(native))
                    self.assertEqual(os.stat(native).st_nlink, 1)  # a copy, not a link
                    self.assertEqual(os.stat(native).st_mode & 0o777, 0o400)
                    self.assertEqual(hashlib.sha256(Path(native).read_bytes()).hexdigest(), digest)
                    copied = native
                self.assertFalse(os.path.lexists(copied))

    def test_copy_over_the_cap_fails_closed_in_korean(self) -> None:
        with mock.patch.object(native_path.os, "symlink", side_effect=OSError("refused")), mock.patch.object(
            native_path.os, "link", side_effect=OSError("refused")
        ), mock.patch.object(native_path, "NATIVE_COPY_MAX_BYTES", 10):
            with self.assertRaises(NativePathError) as caught:
                with native_safe_path(self.source):
                    self.fail("must not yield")
        self.assertIn("사본 상한", str(caught.exception))
        self.assertRegex(str(caught.exception), "[가-힣]")

    def test_a_decoder_message_names_the_original_path_not_the_staged_one(self) -> None:
        """R13-1: the staged ASCII name a decoder quotes is put back to the original path."""
        from deepfake_lens.error_text import failure_reason, path_scrub_root

        with path_scrub_root(self.root):
            try:
                with native_safe_path(self.source) as native:
                    staged = native
                    # What soundfile / pymupdf raise: the path they were given, repr()-quoted.
                    raise RuntimeError(f"Error opening {native!r}: Format not recognised.")
            except RuntimeError as exc:
                reason = failure_reason(exc)
            self.assertFalse(os.path.lexists(staged))
            self.assertNotIn(os.path.basename(staged), reason)
            self.assertNotIn(native_path.SESSION_PREFIX, reason)
            # Quoted the way the decoder quotes: repr() of the original (as Pillow writes it).
            self.assertIn(repr(f"<root>/{self.name}"), reason)
            # Only the base name (what some decoders print) -> the original's base name.
            bare = native_path.restore_original_names(f"cannot read {os.path.basename(staged)}")
            self.assertEqual(bare, f"cannot read {self.name}")
            # Windows repr() doubles the separators; the original comes back escaped the same way.
            folder = "C:\\Users\\kim\\AppData\\Local\\Temp\\" + native_path.SESSION_PREFIX + "x1y2"
            name = "000042-00112233aabb.m4a"
            aliases = native_path.OrderedDict({name: "D:\\증거\\녹음 1.m4a"})
            with mock.patch.object(native_path, "_SESSION_DIRS", [folder]), mock.patch.object(native_path, "_ALIASES", aliases):
                quoted = native_path.restore_original_names(repr(folder + "\\" + name))
                plain = native_path.restore_original_names(f"open {folder}\\{name} failed")
            self.assertEqual(quoted, repr("D:\\증거\\녹음 1.m4a"))
            self.assertEqual(plain, "open D:\\증거\\녹음 1.m4a failed")

    def test_unregistered_staging_names_become_fixed_placeholders(self) -> None:
        """R13-1: another process's (or an evicted) staging name is never shown as it is."""
        text = f"Error opening '/tmp/{native_path.SESSION_PREFIX}ab12_cd/000123-0123456789ab.m4a'"
        shown = native_path.restore_original_names(text)
        self.assertEqual(shown, f"Error opening '/tmp/{native_path.STAGED_NAME_PLACEHOLDER}'")
        self.assertEqual(native_path.restore_original_names(f"in {native_path.SESSION_PREFIX}zz9"), f"in {native_path.STAGED_FOLDER_PLACEHOLDER}")
        # An evidence file that merely looks like a staged name is left alone.
        self.assertEqual(native_path.restore_original_names("'<root>/000123-0123456789ab.m4a'"), "'<root>/000123-0123456789ab.m4a'")
        with native_safe_path(self.source) as native:
            folder, name = os.path.split(native)
        with mock.patch.object(native_path, "_ALIASES", native_path.OrderedDict()):
            self.assertEqual(native_path.restore_original_names(os.path.join(folder, name)), native_path.STAGED_NAME_PLACEHOLDER)

    def test_no_ascii_temp_folder_fails_closed(self) -> None:
        with mock.patch.object(native_path, "_SESSION_DIR", None), mock.patch.object(
            native_path, "_base_candidates", return_value=["/없는/폴더"]
        ):
            with self.assertRaises(NativePathError) as caught:
                native_path.session_dir()
        self.assertIn("ASCII 임시 폴더", str(caught.exception))


# Native call sites: (module alias, attribute). Every such call in the
# package must sit inside ``with native_safe_path(...) as <name>:`` and pass
# <name> (or a value built from it in the same block).
NATIVE_CALLS = {
    ("cv2", "VideoCapture"),
    ("cv2", "imread"),
    ("librosa", "load"),
    ("sf", "read"),
    ("soundfile", "read"),
    ("sf", "info"),
    ("soundfile", "info"),
    ("fitz", "open"),
    ("pymupdf", "open"),
    ("c2pa", "Reader"),
    ("_SYNCNET_PIPELINE", "inference"),
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
}


def _names(node: ast.AST) -> set[str]:
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}


class _NativeCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        # Stack of (with-target name, names derived from it inside the block).
        self.scopes: list[tuple[str, set[str]]] = []
        self.sites: list[tuple[int, str, bool]] = []

    def visit_With(self, node: ast.With) -> None:
        pushed = 0
        for item in node.items:
            call = item.context_expr
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "native_safe_path"
                and isinstance(item.optional_vars, ast.Name)
            ):
                target = item.optional_vars.id
                derived = {target}
                for statement in node.body:
                    for sub in ast.walk(statement):
                        if isinstance(sub, ast.Assign) and _names(sub.value) & derived:
                            for tgt in sub.targets:
                                derived |= _names(tgt)
                self.scopes.append((target, derived))
                pushed += 1
        self.generic_visit(node)
        for _ in range(pushed):
            self.scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            key = (func.value.id, func.attr)
            # pymupdf.open() with no argument creates a new document — no file name.
            if key in NATIVE_CALLS and (node.args or node.keywords):
                used: set[str] = set()
                for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                    used |= _names(arg)
                ok = any(used & derived for _, derived in self.scopes)
                self.sites.append((node.lineno, f"{func.value.id}.{func.attr}", ok))
        self.generic_visit(node)


class NativeCallMetaTest(unittest.TestCase):
    """R12-1: every cv2/ffmpeg/librosa/soundfile/pymupdf/C2PA/SyncNet call goes through the helper."""

    def test_every_native_call_site_uses_native_safe_path(self) -> None:
        offenders: list[str] = []
        seen: list[str] = []
        for source in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                    offenders.append(f"{source.name}:{node.lineno} from subprocess import … (bypasses the check)")
            visitor = _NativeCallVisitor()
            visitor.visit(tree)
            for line, call, ok in visitor.sites:
                seen.append(f"{source.name}:{call}")
                if not ok:
                    offenders.append(f"{source.name}:{line} {call} outside `with native_safe_path(...) as <name>`")
        self.assertEqual(offenders, [])
        # Non-vacuous: the known sites were found.
        for expected in (
            "video_analysis.py:cv2.VideoCapture",
            "video_analysis.py:subprocess.run",
            "face_track.py:cv2.VideoCapture",
            "lipsync.py:cv2.VideoCapture",
            "lipsync.py:subprocess.run",
            "lipsync.py:_SYNCNET_PIPELINE.inference",
            "model_adapter.py:cv2.VideoCapture",
            "multimodal.py:cv2.VideoCapture",
            "multimodal.py:librosa.load",
            "rppg.py:cv2.VideoCapture",
            "audio.py:librosa.load",
            "audio.py:sf.read",
            "audio.py:subprocess.run",
            "documents.py:fitz.open",
            "c2pa.py:c2pa.Reader",
            "video.py:subprocess.run",
        ):
            self.assertIn(expected, seen)

    def test_the_visitor_flags_a_raw_call(self) -> None:
        bad = textwrap.dedent(
            """
            def f(path):
                import cv2
                with native_safe_path(path) as native:
                    other = 1
                return cv2.VideoCapture(str(path)), other
            """
        )
        visitor = _NativeCallVisitor()
        visitor.visit(ast.parse(bad))
        self.assertEqual([ok for _, _, ok in visitor.sites], [False])


class NonUtf8MediaEndToEndTest(unittest.TestCase):
    """R12-1 / R12-2: real non-UTF-8 names through CLI, web server and api-serve."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()

    def _case(self, files: dict[bytes, bytes]) -> tuple[Path, dict[bytes, str]]:
        folder = self.root / "case"
        folder.mkdir(exist_ok=True)
        shown = {name: _write_bytes_name(folder, name, data) for name, data in files.items()}
        return folder, shown

    def _cli_scan(self, folder: Path, *extra: str) -> tuple[int, bytes, bytes]:
        proc = subprocess.run(
            [sys.executable, "-m", "deepfake_lens", "scan", str(folder), "--include-low", "--format", "json", *extra],
            capture_output=True,
            env=_child_env(self.home),
            timeout=CHILD_TIMEOUT_SECONDS,
            cwd=str(self.root),
        )
        return proc.returncode, proc.stdout, proc.stderr

    @unittest.skipUnless(HAVE_CV2, "opencv not installed")
    def test_cli_scan_survives_a_non_utf8_mp4_and_keeps_the_other_rows(self) -> None:
        mp4 = _mp4_bytes(self.root)
        folder, shown = self._case({CP949_STEM + b".mp4": mp4, CP949_STEM + b".mov": mp4})
        (folder / "plain.png").write_bytes(A1111.read_bytes())
        (folder / "plain.mp4").write_bytes(mp4)
        for extra in ((), ("--workers", "2")):
            code, out, err = self._cli_scan(folder, *extra)
            self.assertEqual(code, 0, (extra, code, err[-600:]))
            rows = _scan_rows(out)
            self.assertEqual(sorted(rows), sorted(["plain.png", "plain.mp4", *shown.values()]), extra)
            self.assertEqual(rows["plain.png"]["result"]["verdict_code"], Verdict.MANIPULATION_EVIDENCE.value)
            for name in shown.values():
                self.assertEqual(rows[name]["status"], "analyzed", (extra, rows[name]))
                # Same bytes, same outcome as the ASCII-named copy.
                self.assertEqual(_signature(rows[name])[1:], _signature(rows["plain.mp4"])[1:], name)
                ran = {c["check"] for c in rows[name]["result"]["coverage"] if c["status"] == "ran"}
                self.assertIn("video_analysis", ran, rows[name]["result"]["coverage"])

    def test_cli_audio_and_pdf_match_their_ascii_copies(self) -> None:
        """R12-2: audio (librosa/soundfile) and PDF (pymupdf) rows equal the ASCII copy's."""
        wav, pdf = _wav_bytes(), _pdf_bytes()
        folder, shown = self._case({CP949_STEM + b".wav": wav, CP949_STEM + b".pdf": pdf})
        (folder / "plain.wav").write_bytes(wav)
        (folder / "plain.pdf").write_bytes(pdf)
        code, out, err = self._cli_scan(folder)
        self.assertEqual(code, 0, err[-600:])
        rows = _scan_rows(out)
        for raw, ascii_name in ((CP949_STEM + b".wav", "plain.wav"), (CP949_STEM + b".pdf", "plain.pdf")):
            self.assertEqual(_signature(rows[shown[raw]]), _signature(rows[ascii_name]), ascii_name)
            failed = [c for c in rows[shown[raw]]["result"]["coverage"] if c["status"] == "failed"]
            self.assertEqual(failed, [], ascii_name)
        if importlib.util.find_spec("librosa") is not None:
            ran = {c["check"] for c in rows[shown[CP949_STEM + b".wav"]]["result"]["coverage"] if c["status"] == "ran"}
            self.assertIn("audio_features", ran)
        if HAVE_PYMUPDF:
            ran = {c["check"] for c in rows[shown[CP949_STEM + b".pdf"]]["result"]["coverage"] if c["status"] == "ran"}
            self.assertIn("document_text", ran)

    def _server_child(self, leg: str, folder: Path) -> dict:
        """Run one server leg in a child process; it scans twice (still alive after the first)."""
        script = textwrap.dedent(
            """
            import json, sys, threading, urllib.request, urllib.error
            from collections import OrderedDict
            from pathlib import Path
            from urllib.parse import quote
            folder = Path(sys.argv[2]); leg = sys.argv[1]
            headers = {"X-Deepfake-Lens-Client": "gui"}
            if leg == "web":
                from deepfake_lens.webapp import build_server
                server = build_server("127.0.0.1", 0, default_folder=folder)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                port = server.server_address[1]
                def get(path):
                    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
                    try:
                        with urllib.request.urlopen(req, timeout=600) as r:
                            return r.status, r.read()
                    except urllib.error.HTTPError as exc:
                        return exc.code, exc.read()
            else:
                from fastapi.testclient import TestClient
                from deepfake_lens import api_server
                client = TestClient(api_server.create_app(default_folder=folder), raise_server_exceptions=False)
                def get(path):
                    r = client.get(path, headers={"host": "localhost", **headers})
                    return r.status_code, r.content
            out = []
            for _ in range(2):
                status, body = get("/api/scan?folder=" + quote(str(folder)))
                out.append({"status": status, "body": json.loads(body.decode("utf-8")) if status == 200 else body.decode("utf-8", "replace")})
            sys.stdout.write(json.dumps(out))
            """
        )
        proc = subprocess.run(
            [sys.executable, "-c", script, leg, str(folder)],
            capture_output=True,
            env=_child_env(self.home),
            timeout=CHILD_TIMEOUT_SECONDS,
            cwd=str(self.root),
        )
        self.assertEqual(proc.returncode, 0, (leg, proc.returncode, proc.stderr[-800:]))
        return json.loads(proc.stdout.decode("utf-8"))

    def _check_server(self, leg: str) -> None:
        mp4 = _mp4_bytes(self.root)
        folder, shown = self._case({CP949_STEM + b".mp4": mp4})
        (folder / "plain.png").write_bytes(A1111.read_bytes())
        (folder / "plain.mp4").write_bytes(mp4)
        for attempt in self._server_child(leg, folder):
            self.assertEqual(attempt["status"], 200, (leg, attempt))
            rows = {item["path"]: item for item in attempt["body"]["items"]}
            name = shown[CP949_STEM + b".mp4"]
            self.assertEqual(sorted(rows), sorted(["plain.png", "plain.mp4", name]), leg)
            self.assertEqual(rows["plain.png"]["result"]["verdict_code"], Verdict.MANIPULATION_EVIDENCE.value, leg)
            self.assertEqual(_signature(rows[name])[1:], _signature(rows["plain.mp4"])[1:], leg)

    @unittest.skipUnless(HAVE_CV2, "opencv not installed")
    def test_web_server_survives_a_non_utf8_mp4(self) -> None:
        self._check_server("web")

    @unittest.skipUnless(HAVE_CV2, "opencv not installed")
    @unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
    def test_api_server_survives_a_non_utf8_mp4(self) -> None:
        self._check_server("api")


# R13-1: what a staging name looks like in any output (folder prefix or name).
STAGED_TEXT = re.compile(re.escape(native_path.SESSION_PREFIX) + r"|\d{6}-[0-9a-f]{12}|" + re.escape("<네이티브 디코더용 임시"))
# Wall-clock fields of a scan JSON (as in QA-IN-2) plus the cache-replay flag.
VOLATILE_KEYS = {"measured_at", "generated_at", "created_at", "timestamp", "scanned_at", "fitted_at", "cached"}
# Name-bearing row keys (they differ between a file and its copy by design).
NAME_KEYS = {"path", "display_name", "path_b64", "name"}


def _damaged_inputs() -> dict[str, bytes]:
    """Undecodable / unsupported / damaged audio, video, PDF and image bytes (stem.ext -> bytes)."""
    import io

    from PIL import Image

    jpeg = io.BytesIO()
    Image.new("RGB", (160, 120), (90, 120, 150)).save(jpeg, "JPEG")
    junk = bytes((index * 53 + 7) % 256 for index in range(4000))
    return {
        "garbage.wav": b"RIFF\x00\x10\x00\x00WAVEfmt " + junk,  # no 'data' chunk
        "tone.m4a": b"\x00\x00\x00\x18ftypM4A " + junk,  # libsndfile: format not recognised
        "broken.flac": b"fLaC" + junk,
        "cut.wav": _wav_bytes()[:30],  # header only
        "broken.mp4": b"\x00\x00\x00\x18ftypmp42" + junk,
        "broken.pdf": b"%PDF-1.4\n" + junk,
        "cut.jpg": jpeg.getvalue()[:200],
        "notes.xyz": b"unsupported type\n",
    }


def _normalized(node: object, renames: list[tuple[str, str]]) -> object:
    if isinstance(node, dict):
        return {key: _normalized(value, renames) for key, value in node.items() if key not in VOLATILE_KEYS | NAME_KEYS}
    if isinstance(node, list):
        return [_normalized(value, renames) for value in node]
    if isinstance(node, str):
        for old, new in renames:
            node = node.replace(old, new)
        return node
    return node


class StagedNamesNeverLeakEndToEndTest(unittest.TestCase):
    """R13-1 (round 13): no staging name in any output; a Korean / non-UTF-8 name's row equals its ASCII copy's.

    Damaged audio (undecodable m4a, broken flac, wav without data, header-only
    wav), video, PDF, image and an unsupported type, each under an ASCII name,
    a Korean name, a CP949 (non-UTF-8) name and inside a non-UTF-8 folder. A
    decoder that fails on the staged ASCII name quoted it ("'000035-
    df0fda9674ca.m4a'") in the reason, which made every run differ and the
    copy's row differ from the ASCII one; a content-keyed cache then gave the
    ASCII file another file's temp name.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.folder = self.root / "case"
        self.folder.mkdir()
        sub = os.fsencode(self.folder) + b"/sub" + CP949_STEM[:2]
        try:
            os.mkdir(sub)
        except (OSError, UnicodeError):
            raise unittest.SkipTest(NON_UTF8_SKIP)
        self.pairs: list[tuple[str, str]] = []  # (ASCII row path, other row path)
        for name, data in _damaged_inputs().items():
            stem, ext = os.path.splitext(name)
            (self.folder / name).write_bytes(data)
            korean = f"녹음 1 {stem}{ext}"
            (self.folder / korean).write_bytes(data)
            cp949 = _write_bytes_name(self.folder, CP949_STEM + stem.encode() + ext.encode(), data)
            with open(sub + b"/" + name.encode(), "wb") as handle:
                handle.write(data)
            nested = os.fsdecode(b"sub" + CP949_STEM[:2] + b"/" + name.encode())
            self.pairs += [(name, korean), (name, cp949), (name, nested)]

    def _scan(self, tag: str, *extra: str) -> dict[str, Path]:
        outputs = {kind: self.root / f"{tag}.{kind}" for kind in ("json", "csv", "html", "md")}
        args = [
            "--json-out", str(outputs["json"]), "--csv-out", str(outputs["csv"]),
            "--html-out", str(outputs["html"]), "--evidence-statement-out", str(outputs["md"]),
        ]
        if HAVE_PYMUPDF:
            outputs["pdf"] = self.root / f"{tag}.pdf"
            args += ["--pdf-out", str(outputs["pdf"])]
        tmp = self.root / f"tmp_{tag}"
        tmp.mkdir()
        env = {**_child_env(self.home), "TMPDIR": str(tmp)}
        proc = subprocess.run(
            [sys.executable, "-m", "deepfake_lens", "scan", str(self.folder), "--recursive", "--include-low", "--format", "json", *args, *extra],
            capture_output=True, env=env, timeout=CHILD_TIMEOUT_SECONDS, cwd=str(self.root),
        )
        self.assertEqual(proc.returncode, 0, (tag, proc.stderr[-800:]))
        outputs["stdout"] = self.root / f"{tag}.stdout.json"
        outputs["stdout"].write_bytes(proc.stdout)
        self.assertEqual(os.listdir(tmp), [], f"{tag}: staging folder left behind")
        return outputs

    def _texts(self, outputs: dict[str, Path]) -> dict[str, str]:
        texts = {kind: path.read_bytes().decode("utf-8", "surrogateescape") for kind, path in outputs.items() if kind != "pdf"}
        if "pdf" in outputs:
            import pymupdf

            with pymupdf.open(str(outputs["pdf"])) as document:
                texts["pdf"] = "\n".join(page.get_text() for page in document)
        return texts

    def _rows(self, outputs: dict[str, Path]) -> dict[str, dict]:
        return {item["path"]: item for item in json.loads(outputs["json"].read_text(encoding="utf-8"))["items"]}

    def _stable(self, outputs: dict[str, Path]) -> str:
        def strip(node: object) -> object:
            if isinstance(node, dict):
                return {key: strip(value) for key, value in node.items() if key not in VOLATILE_KEYS}
            if isinstance(node, list):
                return [strip(value) for value in node]
            return node

        return json.dumps(strip(json.loads(outputs["json"].read_text(encoding="utf-8"))), ensure_ascii=True, sort_keys=True)

    def test_rows_equal_their_ascii_copies_across_runs_and_warm_cache(self) -> None:
        cache = self.root / "cache.json"
        runs = {
            "cold": self._scan("cold", "--cache", str(cache)),
            "again": self._scan("again"),
            "warm": self._scan("warm", "--cache", str(cache)),
            "workers": self._scan("workers", "--workers", "3"),
        }
        for tag, outputs in runs.items():
            for kind, text in self._texts(outputs).items():
                self.assertIsNone(STAGED_TEXT.search(text), (tag, kind, STAGED_TEXT.search(text)))
        # Non-vacuous: the warm run replayed rows from the content-keyed cache.
        self.assertGreater(json.loads(runs["warm"]["json"].read_text(encoding="utf-8"))["summary"]["cached"], 0)
        reference = self._stable(runs["cold"])
        for tag in ("again", "warm", "workers"):
            self.assertEqual(self._stable(runs[tag]), reference, f"{tag} differs from the cold run")
        rows = self._rows(runs["cold"])
        self.assertEqual(len(rows), len({path for pair in self.pairs for path in pair}))
        for ascii_path, other in self.pairs:
            # Quoted paths are repr()-escaped (a lone surrogate as "\\udcc1"), as Pillow writes them.
            renames = [
                (repr(other)[1:-1], ascii_path), (other, ascii_path),
                (repr(os.path.basename(other))[1:-1], os.path.basename(ascii_path)), (os.path.basename(other), os.path.basename(ascii_path)),
            ]
            self.assertEqual(
                _normalized(rows[other], renames), _normalized(rows[ascii_path], []), f"{other!a} differs from {ascii_path}"
            )
        # Non-vacuous: with librosa the undecodable audio failed with a reason naming the file.
        if importlib.util.find_spec("librosa") is not None:
            korean = rows["녹음 1 tone.m4a"]["result"]["coverage"]
            reason = next(entry["reason"] for entry in korean if entry["check"] == "audio_features")
            self.assertIn("'<root>/녹음 1 tone.m4a'", reason)
            nested = next(other for ascii_path, other in self.pairs if ascii_path == "tone.m4a" and other.startswith("sub"))
            reason = next(entry["reason"] for entry in rows[nested]["result"]["coverage"] if entry["check"] == "audio_features")
            self.assertIn(repr(f"<root>/{nested}"), reason)


if __name__ == "__main__":
    unittest.main()

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
import contextlib
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
import time
import unittest
import wave
from pathlib import Path
from typing import Any, Iterator
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
# R14-2: the real os.chmod (some tests record every call through a mock).
_REAL_CHMOD = os.chmod
# R14-1: Korean-named mp4 + wav pairs in the signalled scan — enough decoding
# time (a few seconds) for the signals to land while names are staged.
R14_1_FILES = 24


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

    def test_read_only_copy_when_symlink_is_refused_never_a_hard_link(self) -> None:
        # R14-2 (round 14): this test used to expect a hard link as the second
        # route — a hard link changes the evidence inode's ctime and link
        # count, which the evidence invariant forbids; the route is now
        # symlink, then copy, and os.link is never called.
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        before = os.stat(self.source)
        with mock.patch.object(native_path.os, "symlink", side_effect=OSError("refused")), mock.patch.object(
            native_path.os, "link", side_effect=AssertionError("hard link tried")
        ):
            with native_safe_path(self.source) as native:
                self.assertFalse(os.path.islink(native))
                info = os.stat(native)
                self.assertNotEqual((info.st_dev, info.st_ino), (before.st_dev, before.st_ino))  # a copy, not a link
                self.assertEqual(info.st_nlink, 1)
                self.assertEqual(info.st_mode & 0o777, 0o400)
                self.assertEqual(hashlib.sha256(Path(native).read_bytes()).hexdigest(), digest)
                # R14-2: the marker names exactly this copy's inode.
                self.assertEqual(native_path._marked_identity(native), (info.st_dev, info.st_ino))
                copied = native
            self.assertFalse(os.path.lexists(copied))
            self.assertFalse(os.path.lexists(copied + native_path.COPY_MARKER_SUFFIX))
        after = os.stat(self.source)
        self.assertEqual((before.st_mode, before.st_nlink, before.st_ctime_ns), (after.st_mode, after.st_nlink, after.st_ctime_ns))

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
            # Quoted as the decoder quotes (repr()), the name as in its row: a lone
            # surrogate is the character itself, not repr()'s "\\udcc1" text.
            self.assertIn(f"'<root>/{self.name}'", reason)
            self.assertNotIn("\\udc", reason)
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

    def test_repr_surrogate_escapes_read_as_the_rows_name(self) -> None:
        """R13-1: "\\udcc1" text from repr() becomes the character; an escaped backslash does not."""
        from deepfake_lens.error_text import scrub_paths, unescape_surrogates

        self.assertEqual(unescape_surrogates(repr("<root>/r\udcc1.jpg")), "'<root>/r\udcc1.jpg'")
        self.assertEqual(unescape_surrogates(repr("<root>/back\\udcc1.jpg")), repr("<root>/back\\udcc1.jpg"))  # literal text
        self.assertEqual(unescape_surrogates(repr("a\\b\udcff")), "'a\\\\b\udcff'")
        self.assertEqual(unescape_surrogates("\\ud800 \\u00e9"), "\\ud800 \\u00e9")  # only U+DC80–U+DCFF
        # A non-UTF-8 scan root is matched in a repr()-quoted message too.
        root = "/case/\udcc1\udcf5"
        from deepfake_lens.error_text import path_scrub_root

        with path_scrub_root(root):
            self.assertEqual(scrub_paths(f"cannot identify image file {root + '/x.png'!r}"), "cannot identify image file '<root>/x.png'")

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


class WindowsStagingOrderTest(unittest.TestCase):
    """R13-8 / R14-2: on Windows the ASCII 8.3 short name, then a copy — never a hard link (Windows API mocked).

    Without symbolic links and with the evidence on another drive than the
    temp folder, every Korean-named file used to be copied in full (up to
    2 GB) for the native decoder. R14-2 (round 14): the hard link that came
    first changed the evidence file's link count and NTFS change time, and
    its read-only attribute is the evidence file's — clearing it to remove
    the link changed the evidence; no route creates one now.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.name = _write_bytes_name(self.root, CP949_STEM + b".mp4", b"\x00\x00\x00\x18ftypmp42" + bytes(range(200)))
        self.source = self.root / self.name
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        windows = mock.patch.object(native_path, "_is_windows", return_value=True)
        windows.start()
        self.addCleanup(windows.stop)
        # Windows has no unprivileged symbolic links: the route must not try one.
        self._real_link = os.link
        no_symlink = mock.patch.object(native_path.os, "symlink", side_effect=AssertionError("symlink tried on Windows"))
        no_symlink.start()
        self.addCleanup(no_symlink.stop)
        # R14-2: nor a hard link.
        no_link = mock.patch.object(native_path.os, "link", side_effect=AssertionError("hard link tried"))
        no_link.start()
        self.addCleanup(no_link.stop)

    def _short_alias(self) -> str:
        """An ASCII name for the same file, standing in for its 8.3 short name."""
        alias_dir = self.root / "SHORT~1"
        alias_dir.mkdir(exist_ok=True)
        alias = alias_dir / "EVIDEN~1.MP4"
        if not alias.exists():
            self._real_link(self.source, alias)  # the test's stand-in for the 8.3 name, not the code under test
        return str(alias)

    def test_short_name_first_nothing_is_created_or_copied(self) -> None:
        # R14-2 (round 14): replaces "test_hard_link_first" — the hard link it
        # expected first modified the evidence file's metadata; the 8.3 name
        # (nothing created, nothing touched) now comes first.
        from deepfake_lens.error_text import failure_reason, path_scrub_root

        alias = self._short_alias()
        before = native_path.staged_names()
        with mock.patch.object(native_path, "_short_path_name", return_value=alias), mock.patch.object(
            native_path.shutil, "copyfile", side_effect=AssertionError("copied")
        ):
            with path_scrub_root(self.root):
                try:
                    with native_safe_path(self.source) as native:
                        self.assertEqual(native, alias)
                        self.assertEqual(native_path.staged_names(), before)  # nothing staged
                        self.assertEqual(hashlib.sha256(Path(native).read_bytes()).hexdigest(), self.digest)
                        raise RuntimeError(f"Error opening {native!r}: Format not recognised.")
                except RuntimeError as exc:
                    reason = failure_reason(exc)
        # The short name is the evidence file itself: never removed.
        self.assertTrue(os.path.exists(alias))
        self.assertTrue(self.source.exists())
        self.assertNotIn("EVIDEN~1", reason)
        self.assertIn(f"'<root>/{self.name}'", reason)

    def test_copy_when_there_is_no_short_name(self) -> None:
        """R14-2: a read-only copy only where a no-follow chmod can clear the flag again; only the copy is ever released."""
        real_chmod, real_unlink = os.chmod, os.unlink
        for no_follow in (True, False):
            with self.subTest(no_follow_chmod=no_follow):
                calls: list[tuple[str, dict]] = []
                denied: set[str] = set()

                def chmod(path: str, mode: int, **kwargs: object) -> None:
                    calls.append((os.fspath(path), dict(kwargs)))
                    real_chmod(path, mode)  # (POSIX ignores the flag for a regular file)

                def unlink(path: str, *args: Any, **kwargs: Any) -> None:
                    # What Windows does to a read-only file: DeleteFile is refused.
                    if not args and not kwargs and os.path.isfile(path) and not os.lstat(path).st_mode & 0o200:
                        denied.add(os.fspath(path))
                        raise PermissionError(13, "Access is denied", path)
                    real_unlink(path, *args, **kwargs)

                with mock.patch.object(native_path, "_short_path_name", return_value=None), mock.patch.object(
                    native_path, "_can_chmod_without_following", return_value=no_follow
                ), mock.patch.object(native_path.os, "chmod", side_effect=chmod), mock.patch.object(native_path.os, "unlink", side_effect=unlink):
                    with native_safe_path(self.source) as native:
                        info = os.lstat(native)
                        self.assertEqual(info.st_nlink, 1)
                        self.assertNotEqual(info.st_ino, os.stat(self.source).st_ino)
                        self.assertEqual(info.st_mode & 0o777 == 0o400, no_follow)  # read-only only when releasable
                        self.assertEqual(hashlib.sha256(Path(native).read_bytes()).hexdigest(), self.digest)
                        copied = native
                self.assertFalse(os.path.lexists(copied))
                self.assertFalse(os.path.lexists(copied + native_path.COPY_MARKER_SUFFIX))
                self.assertEqual(denied, {copied} if no_follow else set())
                # Every chmod: on the copy, never following a link.
                self.assertEqual(calls, [(copied, {"follow_symlinks": False})] * (2 if no_follow else 0))

    def test_copy_over_the_cap_is_a_failed_check_with_the_reason(self) -> None:
        from deepfake_lens.checks import run_check
        from deepfake_lens.error_text import english_prose
        from deepfake_lens.result_types import CoverageStatus

        def decode() -> None:
            with native_safe_path(self.source):
                self.fail("must not yield")

        with mock.patch.object(native_path.os, "link", side_effect=OSError("cross-device")), mock.patch.object(
            native_path, "_short_path_name", return_value=None
        ), mock.patch.object(native_path, "NATIVE_COPY_MAX_BYTES", 10):
            _, entry = run_check("audio_features", decode)
        self.assertEqual(entry.status, CoverageStatus.FAILED)
        self.assertIn("NativePathError", entry.reason)
        self.assertIn("판단 불가: 네이티브 디코더용 임시 사본 상한 초과", entry.reason)
        self.assertIn("짧은 이름(8.3)", entry.reason)
        self.assertIsNone(english_prose(entry.reason), entry.reason)

    def test_no_short_name_lookup_off_windows(self) -> None:
        with mock.patch.object(native_path, "_is_windows", return_value=False):
            self.assertIsNone(native_path._short_path_name(str(self.source)))


@unittest.skipUnless(HAVE_CV2, "opencv not installed")
class NonAsciiTempFolderTest(unittest.TestCase):
    """R13-7 (round 13): face crops and video frames are written into a non-ASCII temp folder.

    ``cv2.imwrite`` wrote them under ``tempfile`` — a Korean Windows user name
    puts Korean in that path, which OpenCV's narrow name cannot open, and a
    non-UTF-8 TMPDIR crashed the process (SIGSEGV). Run in a child process so
    a crash is an assertion, not the end of the test run.
    """

    SCRIPT = textwrap.dedent(
        """
        import json, sys, tempfile
        from pathlib import Path
        from unittest import mock
        import cv2, numpy as np
        from deepfake_lens import model_adapter
        from deepfake_lens.result_types import ExternalModelAnalysis
        assert not tempfile.gettempdir().isascii(), tempfile.gettempdir()
        seen = []
        def fake_score(profile, path, **kwargs):
            from PIL import Image
            with Image.open(path) as image:
                image.load()
                seen.append(list(image.size))
            return ExternalModelAnalysis(available=True, score=40, confidence="reference", model="fake", detail="ok")
        crops = [np.full((40 + 8 * i, 48, 3), 60 * i, np.uint8) for i in range(3)]
        with mock.patch.object(model_adapter, "_score_from_runtime_profile", fake_score):
            result = model_adapter._score_face_crops(crops, {"runtime": "onnx"}, base_dir=Path("."), model_name="fake", profile_limitations=[])
        with tempfile.TemporaryDirectory(prefix="dfl-frames-") as tmp:
            assert not tmp.isascii()
            frames = model_adapter._extract_sampled_frames(cv2, Path(sys.argv[1]), Path(tmp), 4)
            decoded = [cv2.imdecode(np.fromfile(str(f), np.uint8), cv2.IMREAD_COLOR).shape[:2] for f in frames]
        print(json.dumps({"seen": seen, "available": result.available, "decoded": [list(d) for d in decoded]}))
        """
    )

    def test_crops_and_frames_under_a_korean_or_non_utf8_temp_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            video = root / "clip.mp4"
            video.write_bytes(_mp4_bytes(root))
            for label, name in (("korean", "임시 폴더".encode()), ("non-utf8", b"tmp" + CP949_STEM)):
                with self.subTest(label):
                    folder = os.fsencode(root) + b"/" + name
                    try:
                        os.mkdir(folder)
                    except (OSError, UnicodeError):
                        self.skipTest(NON_UTF8_SKIP)
                    env = {**_child_env(root / "home"), "TMPDIR": os.fsdecode(folder)}
                    proc = subprocess.run(
                        [sys.executable, "-c", self.SCRIPT, str(video)], capture_output=True, env=env, timeout=CHILD_TIMEOUT_SECONDS, cwd=str(root)
                    )
                    self.assertEqual(proc.returncode, 0, (label, proc.returncode, proc.stderr[-800:]))
                    out = json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
                    self.assertEqual(out["seen"], [[48, 40], [48, 48], [48, 56]], label)  # every crop written and read back
                    self.assertTrue(out["available"], label)
                    self.assertEqual(len(out["decoded"]), 4, label)
                    self.assertTrue(all(shape == [64, 64] for shape in out["decoded"]), out)
                    self.assertEqual(os.listdir(folder), [], f"{label}: temp files left behind")

    def test_frame_rows_name_the_frame_not_its_temp_path(self) -> None:
        """R13-7: a per-frame row named its random temp file ("/tmp/dfl-frames-x1y2/frame-00003.png")."""
        import re

        from deepfake_lens import model_adapter
        from deepfake_lens.result_types import ExternalModelAnalysis

        def fake_score(profile: object, path: Path, **kwargs: object) -> ExternalModelAnalysis:
            self.assertTrue(Path(path).is_file())
            return ExternalModelAnalysis(available=True, score=40, confidence="reference", model="fake", detail="ok")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            video.write_bytes(_mp4_bytes(root))
            profile = {"runtime": "video-frames", "frames": 3, "inner": {"runtime": "onnx", "checkpoint": "absent.onnx"}}
            with mock.patch.object(model_adapter, "_score_from_runtime_profile", fake_score):
                result = model_adapter._run_video_frames(video, profile, model_name="fake", base_dir=root)
        paths = [entry["path"] for entry in result.models]
        self.assertEqual(len(paths), 3, result.models)
        for path in paths:
            self.assertRegex(str(path), r"^frame-\d{5}\.png$")
        self.assertIsNone(re.search(r"dfl-frames-|[\\/]", " ".join(map(str, paths))))


@unittest.skipIf(os.name == "nt", "POSIX signals")
class SessionFolderCleanupTest(unittest.TestCase):
    """R13-4 (round 13): the staging folder does not outlive the process.

    SIGTERM (no atexit) and SIGINT left the per-process folder in TMPDIR;
    now the handlers remove it before the signal's previous action, and a
    folder of a process that died anyway (SIGKILL) is swept by the next one.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()

    def _child(self, mode: str, signum: int) -> tuple[int, list[str]]:
        script = textwrap.dedent(
            """
            import os, sys, threading, time
            from pathlib import Path
            from deepfake_lens import native_path
            folder, mode = Path(sys.argv[1]), sys.argv[2]
            source = folder / "증거.mp4"
            source.write_bytes(b"x" * 64)
            if mode == "thread":
                native_path.install_cleanup_handlers()  # what the CLI does at start
            def hold():
                with native_safe_path(source) as native:
                    print(os.path.dirname(native), flush=True)
                    time.sleep(60)
            from deepfake_lens.native_path import native_safe_path
            try:
                if mode == "thread":
                    worker = threading.Thread(target=hold, daemon=True)
                    worker.start()
                    while worker.is_alive():
                        time.sleep(0.05)
                else:
                    hold()
            except KeyboardInterrupt:
                sys.exit(3)
            """
        )
        env = {**_child_env(self.base), native_path.NATIVE_TMP_ENV: str(self.base)}
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(self.base), mode], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=str(self.base)
        )
        assert proc.stdout is not None and proc.stderr is not None
        folder = proc.stdout.readline().decode("utf-8").strip()
        self.assertTrue(folder and os.path.isdir(folder), (folder, proc.stderr.read()[-500:] if proc.poll() is not None else ""))
        self.assertEqual(os.path.basename(folder).split("-")[3], str(proc.pid))  # <prefix><pid>-<random>
        proc.send_signal(signum)
        code = proc.wait(timeout=30)
        proc.stdout.close()
        proc.stderr.close()
        return code, [name for name in os.listdir(self.base) if name.startswith(native_path.SESSION_PREFIX)]

    def test_sigterm_removes_the_folder_and_still_terminates(self) -> None:
        import signal

        for mode in ("main", "thread"):
            with self.subTest(mode=mode):
                code, left = self._child(mode, signal.SIGTERM)
                self.assertEqual(code, -signal.SIGTERM)  # the default action still ran
                self.assertEqual(left, [])

    def test_sigint_removes_the_folder_and_still_raises_keyboard_interrupt(self) -> None:
        import signal

        code, left = self._child("main", signal.SIGINT)
        self.assertEqual(code, 3)  # the previous handler (KeyboardInterrupt) still ran
        self.assertEqual(left, [])

    def test_only_a_default_action_gets_the_cleanup_handler(self) -> None:
        """R14-1: an ignored signal stays ignored, a Python handler stays in place, SIG_DFL is wrapped."""
        script = textwrap.dedent(
            """
            import json, signal
            from deepfake_lens import native_path
            def own(signum, frame):
                pass
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            signal.signal(signal.SIGINT, own)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            native_path.install_cleanup_handlers()
            print(json.dumps({
                "hup_ignored": signal.getsignal(signal.SIGHUP) == signal.SIG_IGN,
                "int_own": signal.getsignal(signal.SIGINT) is own,
                "term_wrapped": signal.getsignal(signal.SIGTERM) is native_path._cleanup_then_default,
            }))
            """
        )
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, env=_child_env(self.base), timeout=CHILD_TIMEOUT_SECONDS, cwd=str(self.base)
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertEqual(json.loads(proc.stdout), {"hup_ignored": True, "int_own": True, "term_wrapped": True})

    @unittest.skipUnless(HAVE_CV2, "opencv not installed")
    def test_ignored_sighup_and_sigint_leave_the_scan_unchanged(self) -> None:
        """R14-1 (round 14): a ``nohup`` scan (SIGHUP ignored, SIGINT ignored as in a background job) survives both untouched.

        The cleanup handler used to replace the inherited SIG_IGN: a hang-up
        removed the live staging folder and the scan went on, so the file being
        decoded became a false ``failed`` row ("System error").
        """
        import signal
        import time

        mp4, wav = _mp4_bytes(self.base), _wav_bytes()
        case = self.base / "case"
        case.mkdir()
        for index in range(R14_1_FILES):
            (case / f"증거{index:02d}_clip.mp4").write_bytes(mp4)
            (case / f"증거{index:02d}_tone.wav").write_bytes(wav)

        def ignore_both() -> None:
            signal.signal(signal.SIGHUP, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)

        def scan(tag: str, send_signals: bool) -> tuple[str, int]:
            tmp = self.base / f"tmp_{tag}"
            tmp.mkdir()
            out = self.base / f"{tag}.json"
            env = {**_child_env(self.base / f"home_{tag}"), "TMPDIR": str(tmp)}
            command = [sys.executable, "-m", "deepfake_lens", "scan", str(case), "--include-low", "--format", "json", "--workers", "2"]
            with open(out, "wb") as handle:
                proc = subprocess.Popen(command, stdout=handle, stderr=subprocess.DEVNULL, env=env, cwd=str(self.base), preexec_fn=ignore_both)
                sent = 0
                deadline = time.monotonic() + CHILD_TIMEOUT_SECONDS
                while proc.poll() is None and time.monotonic() < deadline:
                    staged = [name for folder in tmp.glob(f"{native_path.SESSION_PREFIX}*") for name in os.listdir(folder)]
                    if send_signals and staged:
                        proc.send_signal(signal.SIGHUP)
                        proc.send_signal(signal.SIGINT)
                        sent += 1
                    time.sleep(0.02)
                self.assertEqual(proc.wait(timeout=CHILD_TIMEOUT_SECONDS), 0, tag)
            payload = json.loads(out.read_text(encoding="utf-8"))
            failed = [(item["path"], c["check"]) for item in payload["items"] for c in item["result"]["coverage"] if c["status"] == "failed"]
            self.assertEqual(failed, [], tag)
            return json.dumps(_normalized(payload, []), ensure_ascii=True, sort_keys=True), sent

        reference, _ = scan("reference", send_signals=False)
        signalled, sent = scan("signalled", send_signals=True)
        self.assertGreater(sent, 0, "no signal reached the scan while a name was staged")
        self.assertEqual(signalled, reference)

    def test_stale_folders_of_dead_processes_are_swept(self) -> None:
        import time

        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True)
        dead_pid = int(dead.stdout)
        live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(live.wait)
        self.addCleanup(live.kill)
        evidence = self.base / "evidence.bin"
        evidence.write_bytes(b"evidence")
        prefix = native_path.SESSION_PREFIX
        old = time.time() - 2 * native_path.STALE_LEGACY_SECONDS
        made = {
            f"{prefix}{dead_pid}-aaaa": old,  # owner gone, old: swept
            f"{prefix}{dead_pid}-bbbb": None,  # owner gone, just touched: kept (another PID namespace?)
            f"{prefix}{live.pid}-cccc": old,  # owner alive: kept
            f"{prefix}{os.getpid()}-dddd": old,  # this process: kept
            f"{prefix}legacyold": old,  # before R13-4, a day old: swept
            f"{prefix}legacynew": None,  # before R13-4, recent: kept
        }
        for name, mtime in made.items():
            folder = self.base / name
            folder.mkdir()
            os.symlink(evidence, folder / "000001-0123456789ab.bin")
            copy = folder / "000002-0123456789ab.bin"
            copy.write_bytes(b"copy")
            copy.chmod(0o400)
            if mtime is not None:
                os.utime(folder, (mtime, mtime))
        os.symlink(self.base / f"{prefix}{dead_pid}-aaaa", self.base / f"{prefix}{dead_pid}-link")  # a link: never followed
        (self.base / "other-folder").mkdir()
        removed = native_path.sweep_stale_sessions(str(self.base))
        self.assertEqual(removed, [f"{prefix}{dead_pid}-aaaa", f"{prefix}legacyold"])
        left = sorted(os.listdir(self.base))
        self.assertEqual(
            left,
            sorted([*(name for name in made if name not in removed), f"{prefix}{dead_pid}-link", "other-folder", "evidence.bin"]),
        )
        self.assertEqual(evidence.read_bytes(), b"evidence")  # links removed, never their targets

    def test_a_new_session_folder_sweeps_first_and_carries_the_pid(self) -> None:
        import time

        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True)
        stale = self.base / f"{native_path.SESSION_PREFIX}{int(dead.stdout)}-zzzz"
        stale.mkdir()
        old = time.time() - 2 * native_path.STALE_MIN_AGE_SECONDS
        os.utime(stale, (old, old))
        with mock.patch.object(native_path, "_SESSION_DIR", None), mock.patch.object(native_path, "_SESSION_DIRS", []), mock.patch.object(
            native_path, "_base_candidates", return_value=[str(self.base)]
        ), mock.patch.object(native_path, "install_cleanup_handlers") as install:
            created = native_path.session_dir()
            self.assertTrue(os.path.basename(created).startswith(f"{native_path.SESSION_PREFIX}{os.getpid()}-"))
            self.assertFalse(stale.exists())
            install.assert_called_once_with()
            native_path.cleanup_session()
            self.assertFalse(os.path.exists(created))

    def test_the_cli_installs_the_handlers_at_start(self) -> None:
        import contextlib
        import io

        from deepfake_lens import cli

        with mock.patch.object(native_path, "install_cleanup_handlers") as install, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main([]), 0)
        install.assert_called_once_with()


def _evidence_state(path: Path) -> tuple:
    """Everything about an evidence file a scan must leave as it was (R14-2).

    Bytes, size, mode, mtime and ctime (ns), extended attributes and file
    flags / Windows attributes where the OS has them. Excluded: atime (a
    read may update it) and the link count (the instruction's "nlink-
    excluded" snapshot).
    """
    info = os.lstat(path)
    xattrs: tuple = ()
    if hasattr(os, "listxattr"):
        try:
            xattrs = tuple(sorted((name, os.getxattr(path, name, follow_symlinks=False)) for name in os.listxattr(path, follow_symlinks=False)))
        except OSError:
            xattrs = ("<unreadable>",)
    return (
        hashlib.sha256(path.read_bytes()).hexdigest(),
        info.st_size,
        info.st_mode,
        info.st_mtime_ns,
        info.st_ctime_ns,
        getattr(info, "st_flags", None),
        getattr(info, "st_file_attributes", None),
        xattrs,
    )


@contextlib.contextmanager
def _isolated_session(base: Path) -> Iterator[None]:
    """A private session folder under ``base`` (this test process's real one is left alone)."""
    with mock.patch.object(native_path, "_SESSION_DIR", None), mock.patch.object(native_path, "_SESSION_DIRS", []), mock.patch.object(
        native_path, "_base_candidates", return_value=[str(base)]
    ), mock.patch.object(native_path, "install_cleanup_handlers"):
        yield


def _chattr(flag: str, path: str) -> bool:
    """``chattr <flag> path``; True when it took effect (root on a file system that supports it)."""
    import shutil

    if os.name == "nt" or shutil.which("chattr") is None:
        return False
    return subprocess.run(["chattr", flag, path], capture_output=True).returncode == 0


@contextlib.contextmanager
def _unremovable(folder: str) -> Iterator[str]:
    """R14-2: make ``folder``'s entries impossible to remove; yields how ("chmod", "chattr" or "mock").

    The real thing where the OS allows it — a write-protected folder for a
    normal user, an immutable one (``chattr +i``) for root, who ignores the
    folder's mode — else every unlink/rmdir inside it is refused.
    """
    real_unlink, real_rmdir = os.unlink, os.rmdir
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        _REAL_CHMOD(folder, 0o500)
        try:
            yield "chmod"
        finally:
            _REAL_CHMOD(folder, 0o700)
        return
    if _chattr("+i", folder):
        try:
            yield "chattr"
        finally:
            _chattr("-i", folder)
        return

    def refuse(function: Any) -> Any:
        def call(path: Any, *args: Any, **kwargs: Any) -> None:
            if os.fspath(path).startswith(folder) or kwargs.get("dir_fd") is not None:
                raise PermissionError(1, "Operation not permitted", path)
            function(path, *args, **kwargs)

        return call

    with mock.patch.object(os, "unlink", side_effect=refuse(real_unlink)), mock.patch.object(os, "rmdir", side_effect=refuse(real_rmdir)):
        yield "mock"


class EvidenceNeverModifiedTest(unittest.TestCase):
    """R14-2 (round 14): removing a staged name never changes the evidence file.

    When the staged symbolic link could not be unlinked (immutable or
    write-protected staging folder), the fallback ``chmod`` followed the
    link and made the 0444 evidence file 0600 (its ctime changed); the
    stale-folder sweep did the same. On Windows the hard link that was the
    first route shares the evidence file's read-only attribute.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.case = self.base / "case"
        self.case.mkdir()
        self.evidence = self.case / "증거.jpg"
        self.evidence.write_bytes(b"EVIDENCE" * 64)
        self.evidence.chmod(0o444)
        self.addCleanup(self.evidence.chmod, 0o644)
        self.chmods: list[str] = []
        real_chmod = os.chmod

        def recording_chmod(path: Any, mode: int, **kwargs: Any) -> None:
            self.chmods.append(os.fspath(path))
            real_chmod(path, mode, **kwargs)

        patcher = mock.patch.object(native_path.os, "chmod", side_effect=recording_chmod)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _routes(self) -> list[tuple[str, Any]]:
        refuse_symlink = mock.patch.object(native_path.os, "symlink", side_effect=OSError("refused"))
        return [("symlink", contextlib.nullcontext()), ("copy", refuse_symlink)]

    def test_unremovable_staging_folder_leaves_the_evidence_untouched(self) -> None:
        before = _evidence_state(self.evidence)
        for route, patch in self._routes():
            with self.subTest(route=route), _isolated_session(self.base), patch:
                self.chmods.clear()
                staging = native_safe_path(self.evidence)
                native = staging.__enter__()
                folder = os.path.dirname(native)
                self.assertEqual(os.path.islink(native), route == "symlink")
                with _unremovable(folder) as how:
                    staging.__exit__(None, None, None)  # the with block ends: the unlink is refused …
                    self.assertTrue(os.path.lexists(native), how)
                    native_path.cleanup_session()  # … and so is the folder's removal (atexit / signal path)
                    self.assertTrue(os.path.lexists(native), how)
                self.assertEqual(_evidence_state(self.evidence), before, (route, how))
                # Not a single chmod on the evidence or through a link (only the fresh copy is made 0400).
                self.assertEqual([path for path in self.chmods if path != native], [], (route, how))
                native_path.cleanup_session()
                self.assertFalse(os.path.lexists(folder))

    def test_unremovable_stale_folder_is_left_and_the_evidence_untouched(self) -> None:
        before = _evidence_state(self.evidence)
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True)
        stale = self.base / f"{native_path.SESSION_PREFIX}{int(dead.stdout)}-zz"
        stale.mkdir()
        os.symlink(self.evidence, stale / "000001-0123456789ab.jpg")
        # A forged marker naming the evidence inode does not make the link "ours".
        info = os.stat(self.evidence)
        (stale / f"000001-0123456789ab.jpg{native_path.COPY_MARKER_SUFFIX}").write_text(f"{info.st_dev}:{info.st_ino}", encoding="ascii")
        old = time.time() - 2 * native_path.STALE_MIN_AGE_SECONDS
        os.utime(stale, (old, old))
        with mock.patch.object(native_path, "_can_chmod_without_following", return_value=True):
            for windows in (False, True):
                with self.subTest(windows=windows), mock.patch.object(native_path, "_is_windows", return_value=windows):
                    with _unremovable(str(stale)) as how, mock.patch.object(native_path, "_win_pid_alive", return_value=False):
                        removed = native_path.sweep_stale_sessions(str(self.base))
                    self.assertEqual(removed, [], how)
                    self.assertTrue(os.path.islink(stale / "000001-0123456789ab.jpg"), how)
                    self.assertEqual(_evidence_state(self.evidence), before, how)
                    self.assertEqual(self.chmods, [], how)
        os.utime(stale, (old, old))
        self.assertEqual(native_path.sweep_stale_sessions(str(self.base)), [stale.name])
        self.assertEqual(_evidence_state(self.evidence), before)

    def test_windows_hard_link_of_an_older_version_is_never_released(self) -> None:
        """R14-8: a pre-R14 Windows staging folder holds a hard link to read-only evidence — its attribute is never cleared."""
        dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True)
        stale = self.base / f"{native_path.SESSION_PREFIX}{int(dead.stdout)}-hl"
        stale.mkdir()
        link = stale / "000001-0123456789ab.jpg"
        os.link(self.evidence, link)
        info = os.stat(self.evidence)
        before = _evidence_state(self.evidence)  # (the test's own hard link changed the ctime once)
        # Even a marker naming the inode: a link count > 1 is never released.
        (stale / f"{link.name}{native_path.COPY_MARKER_SUFFIX}").write_text(f"{info.st_dev}:{info.st_ino}", encoding="ascii")
        old = time.time() - 2 * native_path.STALE_MIN_AGE_SECONDS
        os.utime(stale, (old, old))
        real_unlink = os.unlink

        def windows_unlink(path: Any, *args: Any, **kwargs: Any) -> None:
            # DeleteFile refuses a read-only file — a hard link shares the evidence's flag.
            if os.path.basename(os.fspath(path)) == link.name:
                raise PermissionError(13, "Access is denied", path)
            real_unlink(path, *args, **kwargs)

        with mock.patch.object(native_path, "_is_windows", return_value=True), mock.patch.object(
            native_path, "_can_chmod_without_following", return_value=True
        ), mock.patch.object(native_path, "_win_pid_alive", return_value=False), mock.patch.object(os, "unlink", side_effect=windows_unlink):
            self.assertFalse(native_path._release_copy(str(link), str(self.evidence)))
            native_path.sweep_stale_sessions(str(self.base))
            native_path._unlink_staged(str(link), str(self.evidence))
        self.assertTrue(link.exists())
        self.assertEqual(self.chmods, [])
        self.assertEqual(_evidence_state(self.evidence), before)
        self.assertEqual(os.stat(self.evidence).st_mode & 0o777, 0o444)

    def test_a_marked_copy_is_released_and_removed_on_windows(self) -> None:
        """R14-2: the one file the guard releases — a regular copy, link count 1, whose marker names its inode."""
        with _isolated_session(self.base), mock.patch.object(native_path, "_is_windows", return_value=True), mock.patch.object(
            native_path, "_can_chmod_without_following", return_value=True
        ), mock.patch.object(native_path, "_short_path_name", return_value=None):
            with native_safe_path(self.evidence) as native:
                self.assertEqual(os.lstat(native).st_mode & 0o777, 0o400)
            self.assertFalse(os.path.lexists(native))
            folder = native_path.session_dir()
            copy = os.path.join(folder, "000009-0123456789ab.jpg")
            Path(copy).write_bytes(b"copy")
            os.chmod(copy, 0o400)
            self.assertFalse(native_path._release_copy(copy))  # no marker: not ours
            Path(copy + native_path.COPY_MARKER_SUFFIX).write_text("1:2", encoding="ascii")
            self.assertFalse(native_path._release_copy(copy))  # a marker for another inode
            info = os.lstat(copy)
            Path(copy + native_path.COPY_MARKER_SUFFIX).write_text(f"{info.st_dev}:{info.st_ino}", encoding="ascii")
            self.assertTrue(native_path._release_copy(copy))
            native_path.cleanup_session()
            self.assertFalse(os.path.lexists(folder))
        self.assertEqual([path for path in self.chmods if path not in (native, copy)], [])
        self.assertEqual(os.stat(self.evidence).st_mode & 0o777, 0o444)


class _FakeFunction:
    """A kernel32 export: checks its signature was declared, converts every argument with it, then answers."""

    def __init__(self, name: str, answer: Any) -> None:
        self.name, self.answer = name, answer
        self.argtypes: list[Any] | None = None
        self.restype: Any = "unset"
        self.calls: list[tuple] = []

    def __call__(self, *args: Any) -> Any:
        assert self.argtypes is not None and self.restype != "unset", f"{self.name}: signature not declared"
        assert len(args) == len(self.argtypes), f"{self.name}: {len(args)} arguments for {len(self.argtypes)}"
        for argtype, arg in zip(self.argtypes, args):
            argtype.from_param(arg)  # raises TypeError / ArgumentError like the real FFI call
        self.calls.append(args)
        return self.answer(*args)


class _FakeKernel32:
    def __init__(self, **answers: Any) -> None:
        self.functions = {name: _FakeFunction(name, answer) for name, answer in answers.items()}

    def __getattr__(self, name: str) -> Any:
        try:
            return self.__dict__["functions"][name]
        except KeyError:
            raise AttributeError(name) from None


class WindowsCtypesWrapperTest(unittest.TestCase):
    """R14-8 (round 14): the Windows-only ctypes calls run against a fake ``ctypes.WinDLL``.

    ``_short_path_name`` and the Windows ``_pid_alive`` branch were only ever
    replaced wholesale by mocks; now the wrapper itself runs: declared
    signatures (``HANDLE`` restype — a 64-bit handle is not truncated),
    buffer size, NUL accounting, error codes and handle closing.
    """

    def _with(self, kernel32: _FakeKernel32, last_error: int = 0) -> contextlib.ExitStack:
        import ctypes

        stack = contextlib.ExitStack()
        loaded: list[tuple] = []

        def win_dll(name: str, **kwargs: Any) -> _FakeKernel32:
            loaded.append((name, kwargs))
            return kernel32

        stack.enter_context(mock.patch.object(ctypes, "WinDLL", side_effect=win_dll, create=True))
        stack.enter_context(mock.patch.object(ctypes, "get_last_error", return_value=last_error, create=True))
        stack.enter_context(mock.patch.object(native_path, "_is_windows", return_value=True))
        stack.callback(lambda: self.assertTrue(all(entry == ("kernel32", {"use_last_error": True}) for entry in loaded), loaded))
        return stack

    def _short_kernel(self, short: str, *, grow: bool = False, fail: bool = False) -> _FakeKernel32:
        def get_short_path_name(path: str, buffer: Any, size: int) -> int:
            if fail:
                return 0
            needed = len(short) + 1  # with the terminating NUL
            if buffer is None or size < needed:
                return needed + (5 if grow and buffer is not None else 0)
            if grow:
                return needed + 5  # the name grew between the two calls
            buffer.value = short
            return len(short)  # without the NUL

        return _FakeKernel32(GetShortPathNameW=get_short_path_name, OpenProcess=None, GetExitCodeProcess=None, CloseHandle=None)

    def test_short_path_name_signature_buffer_and_results(self) -> None:
        from ctypes import wintypes

        long_path = "D:\\증거\\녹음 파일.m4a"
        kernel32 = self._short_kernel("D:\\8B1F~1\\4D5C~1.M4A")
        with self._with(kernel32):
            self.assertEqual(native_path._short_path_name(long_path), "D:\\8B1F~1\\4D5C~1.M4A")
        function = kernel32.functions["GetShortPathNameW"]
        self.assertEqual(function.argtypes, [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD])
        self.assertIs(function.restype, wintypes.DWORD)
        (first, second) = function.calls
        self.assertEqual(first, (long_path, None, 0))
        self.assertEqual(second[0], long_path)
        self.assertEqual(second[2], len("D:\\8B1F~1\\4D5C~1.M4A") + 1)  # exactly the size asked for
        import ctypes

        self.assertEqual(ctypes.sizeof(second[1]), ctypes.sizeof(ctypes.c_wchar) * second[2])
        for label, fake, expected in (
            ("error (0)", self._short_kernel("X", fail=True), None),
            ("grew between calls", self._short_kernel("D:\\A~1.M4A", grow=True), None),
            ("8.3 disabled: the long name back", self._short_kernel(long_path), None),
            ("short name not ASCII", self._short_kernel("D:\\증거~1\\A.M4A"), None),
        ):
            with self.subTest(label), self._with(fake):
                self.assertEqual(native_path._short_path_name(long_path), expected)

    def _process_kernel(self, *, handle: int | None, exit_ok: bool = True, code: int = 259) -> _FakeKernel32:
        def get_exit_code(handle_value: Any, pointer: Any) -> int:
            if not exit_ok:
                return 0
            pointer._obj.value = code
            return 1

        return _FakeKernel32(
            GetShortPathNameW=None,
            OpenProcess=lambda access, inherit, pid: handle,
            GetExitCodeProcess=get_exit_code,
            CloseHandle=lambda handle_value: 1,
        )

    def test_pid_alive_signatures_error_codes_and_handle_closing(self) -> None:
        from ctypes import wintypes

        big_handle = 0x7FFF_0000_1234  # beyond 32 bits: a c_int restype would truncate it
        kernel32 = self._process_kernel(handle=big_handle, code=259)
        with self._with(kernel32):
            self.assertTrue(native_path._pid_alive(4242))
        open_process = kernel32.functions["OpenProcess"]
        self.assertEqual(open_process.argtypes, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])
        self.assertIs(open_process.restype, wintypes.HANDLE)
        self.assertEqual(open_process.calls, [(native_path._PROCESS_QUERY_LIMITED_INFORMATION, False, 4242)])
        exit_code = kernel32.functions["GetExitCodeProcess"]
        self.assertEqual(exit_code.argtypes, [wintypes.HANDLE, wintypes.LPDWORD])
        self.assertIs(exit_code.restype, wintypes.BOOL)
        self.assertEqual(exit_code.calls[0][0], big_handle)
        close = kernel32.functions["CloseHandle"]
        self.assertEqual((close.argtypes, close.restype), ([wintypes.HANDLE], wintypes.BOOL))
        self.assertEqual(close.calls, [(big_handle,)])
        for label, fake, last_error, expected in (
            ("exited (code 0)", self._process_kernel(handle=8, code=0), 0, False),
            ("exit code unreadable", self._process_kernel(handle=8, exit_ok=False), 0, True),
            ("no such process (ERROR_INVALID_PARAMETER)", self._process_kernel(handle=None), native_path._ERROR_INVALID_PARAMETER, False),
            ("access denied (another user's process)", self._process_kernel(handle=None), 5, True),
        ):
            with self.subTest(label), self._with(fake, last_error=last_error):
                self.assertEqual(native_path._pid_alive(77), expected)
                if fake.functions["OpenProcess"].answer(0, False, 0):
                    self.assertEqual(len(fake.functions["CloseHandle"].calls), 1)  # always closed
                else:
                    self.assertEqual(fake.functions["CloseHandle"].calls, [])
        with self._with(self._process_kernel(handle=8)):
            self.assertFalse(native_path._pid_alive(0))  # never asked


EVIDENCE_INVARIANT_SCRIPT = textwrap.dedent(
    """
    import os, sys
    from unittest import mock
    from deepfake_lens import cli
    if sys.argv[1] == "copy":
        # Force the copy route: no symbolic link can be made.
        mock.patch.object(os, "symlink", side_effect=OSError("refused")).start()
    sys.exit(cli.main(["scan", sys.argv[2], "--recursive", "--include-low", "--format", "json", "--workers", "2",
                       "--json-out", sys.argv[3], "--deep-signals"]))
    """
)


class EvidenceInvariantFullScanTest(unittest.TestCase):
    """R14-2 (round 14), hard invariant: a full scan with staging forced leaves every evidence file exactly as it was.

    Every evidence name is Korean (or not UTF-8), so every native decoder
    call stages it; the scan runs once with the symbolic-link route and once
    with links refused (copy route). Each file's bytes, size, mode, mtime,
    ctime, extended attributes and flags are compared (atime and link count
    excluded), and the folder holds exactly the same entries afterwards.
    Some files are read-only (0444) — a chmod reaching them shows as a mode
    and ctime change.
    """

    def test_full_scan_never_modifies_an_evidence_file(self) -> None:
        import io
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            case = root / "case"
            (case / "하위 폴더").mkdir(parents=True)
            files: dict[str, bytes] = {
                "증거 사진.png": A1111.read_bytes(),
                "녹음 1.wav": _wav_bytes(),
                "문서.pdf": _pdf_bytes(),
                "메모.txt": "AI 언어 모델에 대한 사람의 글입니다.\n".encode(),
                "하위 폴더/깨진 녹음.m4a": b"\x00\x00\x00\x18ftypM4A " + bytes(range(256)) * 8,
                "하위 폴더/깨진 영상.mp4": b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 8,
            }
            if HAVE_CV2:
                files["영상.mp4"] = _mp4_bytes(root)
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("안의 녹음.wav", _wav_bytes())
                handle.writestr("안의 사진.png", A1111.read_bytes())
            files["압축.zip"] = archive.getvalue()
            for index, (name, data) in enumerate(sorted(files.items())):
                (case / name).write_bytes(data)
                if index % 2 == 0:
                    (case / name).chmod(0o444)
            try:
                _write_bytes_name(case, CP949_STEM + b".wav", _wav_bytes())  # a non-UTF-8 name too, where the OS allows it
            except unittest.SkipTest:
                pass
            paths = sorted(path for path in case.rglob("*") if path.is_file())
            before = {path: _evidence_state(path) for path in paths}
            listing = sorted(os.fsencode(path) for path in case.rglob("*"))
            for route in ("symlink", "copy"):
                with self.subTest(route=route):
                    tmpdir = root / f"tmp_{route}"
                    tmpdir.mkdir()
                    out = root / f"{route}.json"
                    env = {**_child_env(root / f"home_{route}"), "TMPDIR": str(tmpdir)}
                    proc = subprocess.run(
                        [sys.executable, "-c", EVIDENCE_INVARIANT_SCRIPT, route, str(case), str(out)],
                        capture_output=True, env=env, timeout=CHILD_TIMEOUT_SECONDS, cwd=str(root),
                    )
                    self.assertIn(proc.returncode, (0, 1), proc.stderr[-800:])
                    rows = json.loads(out.read_text(encoding="utf-8"))["items"]
                    self.assertGreaterEqual(len(rows), len(paths))
                    for path in paths:
                        self.assertEqual(_evidence_state(path), before[path], f"{route}: {path.relative_to(case)!a} modified")
                    self.assertEqual(sorted(os.fsencode(path) for path in case.rglob("*")), listing, route)
                    self.assertEqual(os.listdir(tmpdir), [], f"{route}: temp files left behind")
                    # Non-vacuous: a staged decoder ran on a Korean name.
                    ran = {c["check"] for row in rows if row["path"] == "녹음 1.wav" for c in row["result"]["coverage"] if c["status"] == "ran"}
                    if importlib.util.find_spec("librosa") is not None:
                        self.assertIn("audio_features", ran)
            for path in paths:
                path.chmod(0o644)


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


# R13-7 (round 13): OpenCV writers take a narrow file name too — a non-ASCII
# temp folder (a Korean Windows user name; a non-UTF-8 TMPDIR crashed the
# process). They are not allowed anywhere in the package: native_path.
# imwrite_any encodes in memory and writes with Python.
BANNED_NATIVE_WRITES = {("cv2", "imwrite"), ("cv2", "VideoWriter")}


def _names(node: ast.AST) -> set[str]:
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}


def _import_aliases(tree: ast.AST) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """R13-7: ``{local name: module}`` and ``{local name: (module, attribute)}`` of a module.

    ``import cv2 as cv``, ``X = importlib.import_module("cv2")`` and
    ``from cv2 import imread as read`` all name the same native calls.
    """
    modules: dict[str, str] = {}
    members: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name.split(".")[0]] = alias.name.split(".")[0] if alias.asname is None else alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                members[alias.asname or alias.name] = (node.module.split(".")[0], alias.name)
        elif (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "import_module"
            and node.value.args
            and isinstance(node.value.args[0], ast.Constant)
            and isinstance(node.value.args[0].value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    modules[target.id] = node.value.args[0].value.split(".")[0]
    return modules, members


class _NativeCallVisitor(ast.NodeVisitor):
    def __init__(self, tree: ast.AST | None = None) -> None:
        # Stack of (with-target name, names derived from it inside the block).
        self.scopes: list[tuple[str, set[str]]] = []
        self.sites: list[tuple[int, str, bool]] = []
        # R13-7: banned writer calls (line, call).
        self.banned: list[tuple[int, str]] = []
        self.modules, self.members = _import_aliases(tree) if tree is not None else ({}, {})

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
        keys: set[tuple[str, str]] = set()
        shown = ""
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            # The spelled name (``sf.read``) and the module it stands for (R13-7: ``cv.imread``).
            keys = {(func.value.id, func.attr), (self.modules.get(func.value.id, func.value.id), func.attr)}
            shown = f"{func.value.id}.{func.attr}"
        elif isinstance(func, ast.Name) and func.id in self.members:
            keys = {self.members[func.id]}  # R13-7: ``from cv2 import imread``
            shown = ".".join(self.members[func.id])
        if keys & BANNED_NATIVE_WRITES:
            self.banned.append((node.lineno, shown))
        # pymupdf.open() with no argument creates a new document — no file name.
        if keys & NATIVE_CALLS and (node.args or node.keywords):
            used: set[str] = set()
            for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                used |= _names(arg)
            ok = any(used & derived for _, derived in self.scopes)
            self.sites.append((node.lineno, shown, ok))
        self.generic_visit(node)


class NativeCallMetaTest(unittest.TestCase):
    """R12-1: every cv2/ffmpeg/librosa/soundfile/pymupdf/C2PA/SyncNet call goes through the helper."""

    def test_every_native_call_site_uses_native_safe_path(self) -> None:
        offenders: list[str] = []
        seen: list[str] = []
        # R13-7: every package module, sub-packages included (the tests write
        # their fixtures into ASCII temp folders and are not package code).
        sources = [path for path in sorted(PACKAGE.rglob("*.py")) if "tests" not in path.relative_to(PACKAGE).parts]
        self.assertIn(PACKAGE / "native_path.py", sources)
        for source in sources:
            label = source.relative_to(PACKAGE).as_posix()
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                    offenders.append(f"{label}:{node.lineno} from subprocess import … (bypasses the check)")
            visitor = _NativeCallVisitor(tree)
            visitor.visit(tree)
            for line, call, ok in visitor.sites:
                seen.append(f"{label}:{call}")
                if not ok:
                    offenders.append(f"{label}:{line} {call} outside `with native_safe_path(...) as <name>`")
            for line, call in visitor.banned:
                offenders.append(f"{label}:{line} {call} (R13-7: use native_path.imwrite_any)")
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

    def test_the_visitor_resolves_aliases_and_bans_writers(self) -> None:
        """R13-7: ``import cv2 as cv``, ``from cv2 import …`` and import_module are seen; writers are banned."""
        bad = textwrap.dedent(
            """
            import importlib
            import cv2 as cv
            from cv2 import imread as read, imwrite
            def f(path, image):
                cv.VideoCapture(path)
                read(path)
                cv.imwrite(path, image)
                imwrite(path, image)
                ocv = importlib.import_module("cv2")
                ocv.VideoWriter(path, 0, 1.0, (2, 2))
                with native_safe_path(path) as native:
                    cv.imread(native)
            """
        )
        tree = ast.parse(bad)
        visitor = _NativeCallVisitor(tree)
        visitor.visit(tree)
        self.assertEqual([(call, ok) for _, call, ok in visitor.sites], [("cv.VideoCapture", False), ("cv2.imread", False), ("cv.imread", True)])
        self.assertEqual([call for _, call in visitor.banned], ["cv.imwrite", "cv2.imwrite", "ocv.VideoWriter"])


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
        "garbage.png": b"not an image at all " * 8,  # Pillow quotes the real path with repr()
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
            # A message names the file as its row does (raw name, no repr() escapes).
            renames = [(other, ascii_path), (os.path.basename(other), os.path.basename(ascii_path))]
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
            self.assertIn(f"'<root>/{nested}'", reason)  # the name as in its row


if __name__ == "__main__":
    unittest.main()

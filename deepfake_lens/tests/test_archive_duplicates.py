"""R12-3 (round 12): archive entries that name the same path never overwrite each other.

Before R12-3 a zip/tar holding the same member name twice — or ``x/y.png``
and ``x\\y.png``, ``./p.png`` and ``p.png``, ``A.png`` and ``a.png`` on a
case-insensitive file system — was extracted to one file: the later entry
overwrote the earlier, both rows showed the last member's sha256 and
verdict, and the A1111 member's evidence was lost (the container was
"판단 불가"). Now each entry is extracted to its own index-numbered folder;
the first keeps its path, a later one is ``<path>#2`` with the reason
"중복 멤버 이름" in its limitations and the container's, and
(container, member, member_index) is unique.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tarfile
import tempfile
import threading
import types
import unicodedata
import unittest
import urllib.error
import urllib.request
import warnings
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Any
from unittest.mock import patch

from deepfake_lens import webapp_api
from deepfake_lens.archives import SEVEN_ZIP_DUPLICATE_REASON, extract_archive
from deepfake_lens.core import scan_directory
from deepfake_lens.result_types import ScanItem, Verdict
from deepfake_lens.serialization import _scan_item_from_json
from deepfake_lens.tests.test_archives import _Fake7zEntry, _FakeRarFile, _FakeRarInfo, _FakeSevenZipFile

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "benchmark"
A1111 = (FIXTURES / "a1111-metadata-marker.png").read_bytes()
TEXTURE = (FIXTURES / "real-like-texture.png").read_bytes()
DUPLICATE = "중복 멤버 이름"


def _zip(path: Path, members: list[tuple[str, bytes]]) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # zipfile warns "Duplicate name" — that is the point
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in members:
                zf.writestr(name, data)


def _tar(path: Path, members: list[tuple[str, bytes]]) -> None:
    with tarfile.open(path, "w") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# (case label, [(entry name, bytes)], expected row names)
NAME_CASES = [
    ("same name twice", [("same.png", A1111), ("same.png", TEXTURE)], ["same.png", "same.png#2"]),
    ("slash vs backslash", [("x/y.png", A1111), ("x\\y.png", TEXTURE)], ["x/y.png", "x/y.png#2"]),
    ("./ prefix", [("./p.png", A1111), ("p.png", TEXTURE)], ["p.png", "p.png#2"]),
    ("letter case", [("Evidence.png", A1111), ("evidence.png", TEXTURE)], ["Evidence.png", "evidence.png#2"]),
    (
        "Unicode normalization",
        [(unicodedata.normalize("NFC", "증거.png"), A1111), (unicodedata.normalize("NFD", "증거.png"), TEXTURE)],
        [unicodedata.normalize("NFC", "증거.png"), unicodedata.normalize("NFD", "증거.png") + "#2"],
    ),
    (
        "a later literal '#2' name",
        [("a.png", A1111), ("a.png", TEXTURE), ("a.png#2", b"third")],
        ["a.png", "a.png#2", "a.png#2#2"],
    ),
]


class ExtractionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _check(self, out, root: Path, members: list[tuple[str, bytes]], expected: list[str]) -> None:
        names = [out.member_name(m, root) for m in out.members]
        self.assertEqual(names, expected)
        self.assertEqual(len(set(out.members)), len(members))  # distinct files on disk
        for member, (raw, data) in zip(out.members, members):
            self.assertEqual(member.read_bytes(), data, raw)  # nothing overwritten
        renamed = [m for m in out.members if out.member_name(m, root).endswith("#2") or "#2#" in out.member_name(m, root)]
        self.assertTrue(renamed)
        for member in renamed:
            self.assertIn(DUPLICATE, out.notes[member])
        self.assertTrue(any(DUPLICATE in warning for warning in out.warnings))
        self.assertEqual(out.skipped, 0)

    def test_zip_entries_naming_the_same_path(self) -> None:
        for label, members, expected in NAME_CASES:
            with self.subTest(label):
                path = self.tmp / f"{len(list(self.tmp.iterdir()))}.zip"
                _zip(path, members)
                root = self.tmp / (path.stem + "-out")
                self._check(extract_archive(path, root), root, members, expected)

    def test_tar_entries_naming_the_same_path(self) -> None:
        for label, members, expected in NAME_CASES:
            with self.subTest(label):
                path = self.tmp / f"{len(list(self.tmp.iterdir()))}.tar"
                _tar(path, members)
                root = self.tmp / (path.stem + "-out")
                self._check(extract_archive(path, root), root, members, expected)

    def test_rar_entries_naming_the_same_path(self) -> None:
        module = types.ModuleType("rarfile")
        module.RarFile = _FakeRarFile  # type: ignore[attr-defined]
        _FakeRarFile.infos = [_FakeRarInfo("same.png", A1111), _FakeRarInfo("SAME.png", TEXTURE)]
        archive = self.tmp / "a.rar"
        archive.write_bytes(b"Rar!")
        with patch.dict(sys.modules, {"rarfile": module}):
            out = extract_archive(archive, self.tmp / "out")
        self._check(out, self.tmp / "out", [("same.png", A1111), ("SAME.png", TEXTURE)], ["same.png", "SAME.png#2"])

    def test_7z_exact_duplicates_are_refused_and_case_collisions_are_kept_apart(self) -> None:
        module = types.ModuleType("py7zr")
        module.SevenZipFile = _FakeSevenZipFile  # type: ignore[attr-defined]
        _FakeSevenZipFile.entries = [
            _Fake7zEntry("Evidence.png", A1111),
            _Fake7zEntry("evidence.png", TEXTURE),
            _Fake7zEntry("twice.png", b"first"),
            _Fake7zEntry("twice.png", b"second"),
        ]
        _FakeSevenZipFile.extracted = []
        _FakeSevenZipFile.resets = 0
        archive = self.tmp / "a.7z"
        archive.write_bytes(b"7z")
        with patch.dict(sys.modules, {"py7zr": module}):
            out = extract_archive(archive, self.tmp / "out")
        root = self.tmp / "out"
        self.assertEqual([out.member_name(m, root) for m in out.members], ["Evidence.png", "evidence.png#2"])
        self.assertEqual([m.read_bytes() for m in out.members], [A1111, TEXTURE])
        # py7zr extracts by name: both "twice.png" entries would land on one file.
        self.assertEqual(out.rejected, [("twice.png", SEVEN_ZIP_DUPLICATE_REASON)] * 2)
        self.assertEqual(_FakeSevenZipFile.resets, 1)  # two extraction passes, one per case variant
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["00000", "00001"])  # batch folders removed

    def test_nested_duplicates_keep_their_reason(self) -> None:
        inner = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(inner, "w") as zf:
                zf.writestr("in.png", A1111)
                zf.writestr("in.png", TEXTURE)
        outer = self.tmp / "outer.zip"
        _zip(outer, [("inner.zip", inner.getvalue()), ("inner.zip", inner.getvalue())])
        root = self.tmp / "out"
        out = extract_archive(outer, root)
        names = [out.member_name(m, root) for m in out.members]
        self.assertEqual(
            sorted(names), ["inner.zip#2::in.png", "inner.zip#2::in.png#2", "inner.zip::in.png", "inner.zip::in.png#2"]
        )
        for member in out.members:
            if out.member_name(member, root).endswith("in.png#2"):
                self.assertIn(DUPLICATE, out.notes[member])


class ScanRowsTest(unittest.TestCase):
    """Rows: unique identity, own sha256 and verdict, container verdict keeps the A1111 member."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _scan(self, folder: Path) -> list[ScanItem]:
        _, items = scan_directory(folder)
        return items

    def test_every_duplicate_case_gets_its_own_row(self) -> None:
        folder = self.tmp / "case"
        folder.mkdir()
        for index, (_label, entries, _expected) in enumerate(NAME_CASES[:5]):
            for order in (entries, list(reversed(entries))):
                stem = f"c{index}-{'a' if order is entries else 'b'}"
                _zip(folder / f"{stem}.zip", order)
                _tar(folder / f"{stem}.tar", order)
        items = self._scan(folder)
        members = [item for item in items if item.member is not None]
        identities = [(item.container, item.member, item.member_index) for item in members]
        self.assertEqual(len(identities), len(set(identities)))
        self.assertEqual(len({(item.container, item.member) for item in members}), len(members))
        by_container: dict[str, list[ScanItem]] = {}
        for item in members:
            assert item.container is not None
            by_container.setdefault(item.container, []).append(item)
        self.assertEqual(len(by_container), 20)
        for container, rows in by_container.items():
            self.assertEqual(sorted(row.member_index or 0 for row in rows), [1, 2], container)
            self.assertEqual({row.sha256 for row in rows}, {_sha(A1111), _sha(TEXTURE)}, container)
            a1111 = next(row for row in rows if row.sha256 == _sha(A1111))
            assert a1111.result is not None
            self.assertEqual(a1111.result.verdict_code, Verdict.MANIPULATION_EVIDENCE, container)
            renamed = next(row for row in rows if (row.member or "").endswith("#2"))
            assert renamed.result is not None
            self.assertIn(DUPLICATE, renamed.result.limitations[0], container)
            box = next(item for item in items if item.path == container)
            assert box.result is not None
            self.assertEqual(box.result.verdict_code, Verdict.MANIPULATION_EVIDENCE, container)
            self.assertTrue(any(DUPLICATE in line for line in box.result.limitations), container)

    def test_member_index_round_trips_through_json(self) -> None:
        folder = self.tmp / "rt"
        folder.mkdir()
        _zip(folder / "d.zip", [("same.png", A1111), ("same.png", TEXTURE)])
        members = [item for item in self._scan(folder) if item.member is not None]
        for item in members:
            data = json.loads(json.dumps(item.to_json()))
            self.assertEqual(data["member_index"], item.member_index)
            self.assertEqual(_scan_item_from_json(data).member_index, item.member_index)
        plain = ScanItem("a.png", "a.png", "image", "analyzed", 1)
        self.assertNotIn("member_index", plain.to_json())


class ReportRederivationTest(unittest.TestCase):
    """POST /api/report re-derives each duplicate member's own digest (no shared sha256)."""

    def setUp(self) -> None:
        roots: Any = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.folder = self.tmp / "case"
        self.folder.mkdir()
        _zip(self.folder / "d.zip", [("same.png", A1111), ("same.png", TEXTURE)])

    def test_web_report_keeps_both_rows_and_digests(self) -> None:
        from urllib.parse import quote

        from deepfake_lens.webapp import build_server

        server = build_server("127.0.0.1", 0, default_folder=self.folder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def call(path: str, body: bytes | None = None) -> tuple[int, bytes]:
            headers = {"X-Deepfake-Lens-Client": "gui", **({"Content-Type": "application/json"} if body else {})}
            request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, headers=headers, method="POST" if body else "GET")
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    return response.status, response.read()
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read()

        status, body = call("/api/scan?folder=" + quote(str(self.folder)))
        self.assertEqual(status, 200, body[:300])
        scan = json.loads(body)
        members = {item["member"]: item for item in scan["items"] if "member" in item}
        self.assertEqual(sorted(members), ["same.png", "same.png#2"])
        self.assertEqual({item["sha256"] for item in members.values()}, {_sha(A1111), _sha(TEXTURE)})
        request = json.dumps({"items": scan["items"], "scan_root": scan["scan_root"], "format": "json"}).encode("utf-8")
        status, body = call("/api/report?format=json", request)
        self.assertEqual(status, 200, body[:300])
        report = {item["path"]: item for item in json.loads(body)["items"]}
        for member, item in members.items():
            self.assertEqual(report[item["path"]]["sha256"], item["sha256"], member)
            self.assertEqual(report[item["path"]]["member_index"], item["member_index"], member)


if __name__ == "__main__":
    unittest.main()

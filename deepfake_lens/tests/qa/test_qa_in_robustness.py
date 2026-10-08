"""QA-IN-5 (WP-H, G34): damaged and hostile inputs never take the scan down.

Twenty damaged inputs sit in one folder next to two valid files. The scan
must survive, every damaged input must come back as "판단 불가 + 이유" or
"미지원"/"실패" with a reason, archive extraction must stay inside its
aggregate budget on disk (no symlinks, nothing outside the temp dir), and
the valid files must get exactly the result they get when scanned alone.

Runs without weights; stdlib-only fixtures (no numpy/Pillow needed).
"""

from __future__ import annotations

import io
import os
import random
import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens import archives
from deepfake_lens.analysis_api import AnalysisOptions, scan_folder
from deepfake_lens.result_types import ScanItem, Verdict

REPO_ROOT = Path(__file__).resolve().parents[3]
VALID_PNG = REPO_ROOT / "fixtures" / "benchmark" / "ai-like-gradient.png"
# Small aggregate budget so the byte cap is exercised by a few MB of
# fixtures instead of 2 GiB (the production default).
TEST_BUDGET_BYTES = 4 * 1024 * 1024
ESCAPE_NAME = "qa-in-5-escape.txt"


def _zip_bytes(members: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def _random_bytes(size: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(size)


def _declared_bomb() -> bytes:
    """1 MiB stored member whose headers declare 1 GiB (forged sizes)."""
    raw = bytearray(_zip_bytes({"bomb.bin": _random_bytes(1024 * 1024, 1)}, compression=zipfile.ZIP_STORED))
    declared = struct.pack("<I", 1 << 30)
    local = raw.index(b"PK\x03\x04")
    raw[local + 22:local + 26] = declared
    central = raw.index(b"PK\x01\x02")
    raw[central + 24:central + 28] = declared
    return bytes(raw)


def _deflate_bomb() -> bytes:
    """Real deflate bomb: 64 MiB of zeros compressed to ~64 KB."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        with zf.open("zeros.bin", "w") as handle:
            chunk = bytes(1024 * 1024)
            for _ in range(64):
                handle.write(chunk)
    return buffer.getvalue()


def _nested_hundred() -> bytes:
    """An outer zip holding 100 inner zips (one short text each)."""
    inner = {
        f"inner-{index:03d}.zip": _zip_bytes({f"note-{index:03d}.txt": f"내부 문서 {index}번입니다. 평범한 사람의 글입니다.".encode()})
        for index in range(100)
    }
    return _zip_bytes(inner, compression=zipfile.ZIP_STORED)


def _deeply_nested(levels: int = 8) -> bytes:
    payload = _zip_bytes({"deep.txt": "가장 깊은 곳의 문서입니다.".encode()})
    for level in range(levels):
        payload = _zip_bytes({f"level-{level}.zip": payload})
    return payload


def _symlink_zip() -> bytes:
    """A directory symlink member (-> /etc) plus a file 'inside' it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        link = zipfile.ZipInfo("linkdir")
        link.create_system = 3  # unix
        link.external_attr = (0o120777 << 16)
        zf.writestr(link, "/etc")
        file_link = zipfile.ZipInfo("passwd-link")
        file_link.create_system = 3
        file_link.external_attr = (0o120777 << 16)
        zf.writestr(file_link, "/etc/passwd")
        zf.writestr("linkdir/inside.txt", "심볼릭 링크 디렉터리 안쪽 파일입니다.")
    return buffer.getvalue()


def _absolute_path_zip() -> bytes:
    return _zip_bytes({
        f"/tmp/{ESCAPE_NAME}": b"absolute",
        f"../../{ESCAPE_NAME}": b"traversal",
        f"C:\\{ESCAPE_NAME}": b"drive",
        "ok-member.txt": "정상 멤버입니다.".encode(),
    })


def _corrupt_crc_zip() -> bytes:
    raw = bytearray(_zip_bytes({"data.txt": ("손상된 압축 데이터 " * 200).encode()}))
    local = raw.index(b"PK\x03\x04")
    name_len, extra_len = struct.unpack("<HH", raw[local + 26:local + 30])
    data_start = local + 30 + name_len + extra_len
    for offset in range(data_start + 4, data_start + 24):
        raw[offset] ^= 0xFF
    return bytes(raw)


def _fat_zip() -> bytes:
    """Ten 1 MiB incompressible members — 10 MiB > the test byte budget."""
    return _zip_bytes({f"blob-{index}.bin": _random_bytes(1024 * 1024, 100 + index) for index in range(10)}, compression=zipfile.ZIP_STORED)


def _png_header_only(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", b"\x78\x9c\x00") + chunk(b"IEND", b"")


def damaged_inputs() -> dict[str, bytes]:
    """The 20 damaged/hostile inputs of QA-IN-5 (name -> bytes)."""
    jpeg_head = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00\x43\x00" + bytes(20)
    return {
        "truncated.jpg": jpeg_head,
        "broken-moov.mp4": b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x7f\xff\xff\xffmoov" + _random_bytes(512, 2),
        "empty.png": b"",
        "empty.wav": b"",
        "text-as.jpg": "이것은 이미지가 아니라 텍스트입니다.".encode(),
        "zip-as.png": _zip_bytes({"x.txt": b"zip disguised as png"}),
        "png-as.wav": VALID_PNG.read_bytes()[:4096],
        "truncated.png": VALID_PNG.read_bytes()[:60],
        "huge-dims.png": _png_header_only(60000, 60000),
        "garbage.wav": b"RIFF\xff\xff\xff\x7fWAVEfmt " + _random_bytes(256, 3),
        "bad.pdf": b"%PDF-1.7\n" + _random_bytes(2048, 4),
        "bad.docx": _random_bytes(2048, 5),
        "bomb-declared.zip": _declared_bomb(),
        "bomb-deflate.zip": _deflate_bomb(),
        "nested-100.zip": _nested_hundred(),
        "deep-nested.zip": _deeply_nested(),
        "dir-symlink.zip": _symlink_zip(),
        "absolute-path.zip": _absolute_path_zip(),
        "corrupt-crc.zip": _corrupt_crc_zip(),
        "truncated.zip": _fat_zip()[:300_000],
        "bad.tar.gz": b"\x1f\x8b\x08\x00" + _random_bytes(1024, 6),
        "fat.zip": _fat_zip(),
    }


def write_valid_files(folder: Path) -> list[str]:
    (folder / "valid.png").write_bytes(VALID_PNG.read_bytes())
    (folder / "valid.txt").write_text("오늘은 공원에서 산책을 하고 친구와 점심을 먹었다. 날씨가 좋아서 오래 걸었다.", encoding="utf-8")
    return ["valid.png", "valid.txt"]


def _disk_usage(root: Path) -> tuple[int, list[Path]]:
    """Bytes of regular files under ``root`` and any symlinks found (lstat, no follow)."""
    total = 0
    links: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                links.append(path)
            elif name in filenames:
                total += path.lstat().st_size
    return total, links


def _comparable(item: ScanItem) -> dict[str, Any]:
    result = item.result
    return {
        "status": item.status,
        "kind": item.kind,
        "verdict": result.verdict_code if result else None,
        "evidence": [(e.title, e.kind, e.direction) for e in result.evidence] if result else None,
        "coverage": [(c.check, c.status, c.reason) for c in result.coverage] if result else None,
    }


class QaIn5DamagedInputsTest(unittest.TestCase):
    """QA-IN-5: 손상 파일 20종(잘린 JPEG, 깨진 mp4 moov, 빈 파일, 확장자 위장, zip 폭탄, 중첩 zip 100개) → 프로세스 생존, 각 파일이 "판단 불가 + 이유" 또는 "미지원". 디스크 사용 상한 초과 없음. 다른 파일 결과에 영향 없음."""

    _tmp: tempfile.TemporaryDirectory[str]
    folder: Path
    clean: Path
    damaged: dict[str, bytes]
    valid: list[str]
    extractions: dict[str, tuple[int, list[Path], archives.ArchiveExtraction]]
    items: list[ScanItem]
    by_path: dict[str, ScanItem]
    summary: Any

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.folder = root / "evidence"
        cls.folder.mkdir()
        cls.damaged = damaged_inputs()
        for name, data in cls.damaged.items():
            (cls.folder / name).write_bytes(data)
        cls.valid = write_valid_files(cls.folder)
        cls.clean = root / "clean"
        cls.clean.mkdir()
        write_valid_files(cls.clean)

        cls.extractions = {}
        real_extract = archives.extract_archive

        def measured_extract(path: Any, dest: Any, **kwargs: Any) -> archives.ArchiveExtraction:
            out = real_extract(path, dest, **kwargs)
            usage, links = _disk_usage(Path(dest))
            cls.extractions[Path(path).name] = (usage, links, out)
            return out

        with mock.patch.object(archives, "TOTAL_EXTRACTION_BYTES", TEST_BUDGET_BYTES), \
                mock.patch("deepfake_lens.core.extract_archive", measured_extract):
            cls.summary, cls.items, _ = scan_folder(cls.folder, AnalysisOptions(max_files=100))
        cls.by_path = {item.path: item for item in cls.items}

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_twenty_damaged_inputs_present(self) -> None:
        self.assertGreaterEqual(len(self.damaged), 20)
        for name in self.damaged:
            self.assertIn(name, self.by_path, name)

    def test_each_damaged_input_is_undetermined_unsupported_or_failed_with_reason(self) -> None:
        """QA-IN-5: every damaged input (and every member pulled out of one) is
        판단 불가 with a reason, 미지원, or 실패 — never a conclusion."""
        for item in self.items:
            top = item.path.split("::", 1)[0]
            if top in self.valid:
                continue
            with self.subTest(path=item.path):
                if item.result is None:
                    self.assertIn(item.status, {"failed", "unsupported", "skipped", "unknown"})
                    self.assertTrue(item.error, "a row without a result must carry a reason")
                else:
                    self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED, item.result.verdict)
                    reasons = [item.result.verdict, *item.result.limitations, *(e.reason for e in item.result.coverage if e.reason)]
                    self.assertTrue(any(reasons), "판단 불가 must say why")

    def test_disk_usage_stays_within_budget_and_inside_temp_dir(self) -> None:
        """QA-IN-5: extraction never writes past the aggregate budget, no symlink lands on disk, nothing escapes."""
        self.assertTrue(self.extractions)
        for name, (usage, links, _) in self.extractions.items():
            with self.subTest(archive=name):
                self.assertLessEqual(usage, TEST_BUDGET_BYTES)
                self.assertEqual(links, [])
        for candidate in (Path(tempfile.gettempdir()) / ESCAPE_NAME, Path(tempfile.gettempdir()).parent / ESCAPE_NAME, Path("/") / ESCAPE_NAME):
            self.assertFalse(candidate.exists(), candidate)

    def test_budget_exhaustion_is_reported(self) -> None:
        """The fat archive hits the byte budget; the 100-inner-zip archive hits the nested-archive budget."""
        _, _, fat = self.extractions["fat.zip"]
        self.assertTrue(any("총량 예산" in w for w in fat.warnings), fat.warnings)
        _, _, nested = self.extractions["nested-100.zip"]
        self.assertTrue(any("중첩 압축 예산" in w for w in nested.warnings), nested.warnings)
        opened = [m for m in nested.members if m.name.startswith("note-")]
        self.assertLessEqual(len(opened), archives.TOTAL_NESTED_ARCHIVES)
        container = self.by_path["nested-100.zip"]
        assert container.result is not None
        self.assertEqual(container.result.verdict_code, Verdict.UNDETERMINED)

    def test_hostile_members_are_skipped(self) -> None:
        for name in ("dir-symlink.zip", "absolute-path.zip", "bomb-declared.zip", "bomb-deflate.zip", "corrupt-crc.zip"):
            with self.subTest(archive=name):
                _, _, out = self.extractions[name]
                self.assertGreater(out.skipped, 0, out)
        _, _, deep = self.extractions["deep-nested.zip"]
        self.assertTrue(any("최대 깊이" in w for w in deep.warnings), deep.warnings)

    def test_valid_files_unaffected(self) -> None:
        """QA-IN-5: the valid files get the same result as in a clean folder."""
        _, clean_items, _ = scan_folder(self.clean, AnalysisOptions())
        clean = {item.path: _comparable(item) for item in clean_items}
        for name in self.valid:
            with self.subTest(path=name):
                item = self.by_path[name]
                self.assertEqual(item.status, "analyzed")
                self.assertIsNotNone(item.result)
                self.assertEqual(_comparable(item), clean[name])


if __name__ == "__main__":
    unittest.main()

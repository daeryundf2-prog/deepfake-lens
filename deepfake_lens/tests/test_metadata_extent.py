"""R18-4 (round 18): the metadata check says how much of the file it read.

The verifier put an A1111 ``parameters`` tEXt chunk after the IDAT of a
6.7 MB PNG: only the first ``--metadata-bytes`` (4 MB) were read, the
generator evidence was missed, the conclusion was 판단 불가 — and coverage
said ``metadata: ran`` (also with ``--metadata-bytes 10``). A PNG's chunks are
now walked through the whole file (IDAT skipped by seeking), a WebP's XMP
chunk likewise; where only a prefix is searched (JPEG segments before SOS
not reached, BMP/GIF/TIFF text) the ``metadata`` entry is ``ran`` with the
reason "메타데이터 앞 N … 만 확인" and the same text is a limitation.
"""

from __future__ import annotations

import importlib.util
import random
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from deepfake_lens.core import analyze_file
from deepfake_lens.image_metadata import read_image_metadata_full
from deepfake_lens.result_types import CoverageStatus, Verdict

HAVE_PIL = importlib.util.find_spec("PIL") is not None
A1111 = b"parameters\x00a cat, Steps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, Size: 1024x1024, Model: sd15"
MIB = 1024 * 1024


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _png_with_text_after_idat(width: int = 1024, height: int = 1024, text: bytes = A1111) -> bytes:
    """An RGB PNG whose (incompressible, so ~3 MB) single IDAT chunk precedes a tEXt chunk."""
    rng = random.Random(18)
    raw = b"".join(b"\x00" + rng.randbytes(width * 3) for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, 1)) + _chunk(b"tEXt", text) + _chunk(b"IEND", b"")


def _metadata_entry(result: object) -> tuple[CoverageStatus, str]:
    entry = next(entry for entry in getattr(result, "coverage") if entry.check == "metadata")
    return entry.status, entry.reason


class PngChunksAreWalkedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_text_after_a_large_idat_is_read(self) -> None:
        path = self.root / "a1111.png"
        path.write_bytes(_png_with_text_after_idat())
        self.assertGreater(path.stat().st_size, 3 * MIB)
        for metadata_bytes in (MIB, 4 * MIB):
            with self.subTest(metadata_bytes=metadata_bytes):
                read = read_image_metadata_full(path, metadata_bytes=metadata_bytes)
                self.assertIn("Steps: 20", read.metadata.get("png.parameters", ""))
                self.assertEqual(read.dimensions, (1024, 1024))
                self.assertIsNone(read.error)
                self.assertIsNone(read.partial)
        # --metadata-bytes 10: the tEXt chunk is over the bound — said, not "ran" silently.
        read = read_image_metadata_full(path, metadata_bytes=10)
        self.assertEqual(read.dimensions, (1024, 1024))
        self.assertEqual(read.partial, f"메타데이터 청크를 10바이트까지만 읽음(--metadata-bytes) — 이후 tEXt 청크({len(A1111)}바이트)를 읽지 않음")

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")  # the EXIF read
    def test_the_generator_evidence_decides(self) -> None:
        path = self.root / "a1111.png"
        path.write_bytes(_png_with_text_after_idat())
        item = analyze_file(path, root=self.root, metadata_bytes=MIB)
        assert item.result is not None
        self.assertEqual(item.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        self.assertEqual(_metadata_entry(item.result), (CoverageStatus.RAN, ""))

    def test_a_truncated_png_is_still_a_failed_read(self) -> None:
        path = self.root / "cut.png"
        path.write_bytes(_png_with_text_after_idat()[: 2 * MIB])
        read = read_image_metadata_full(path, metadata_bytes=MIB)  # the prefix no longer hides the missing IEND
        self.assertIn("IEND", read.error or "")

    def test_metadata_chunks_past_the_budget_are_reported(self) -> None:
        path = self.root / "big-text.png"
        path.write_bytes(_png_with_text_after_idat(64, 64, text=b"Comment\x00" + b"x" * 5000))
        read = read_image_metadata_full(path, metadata_bytes=1000)
        self.assertIn("메타데이터 청크를 1000바이트까지만 읽음", read.partial or "")
        self.assertIn(read.partial, read.notes)


class PrefixOnlyFormatsSayItTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_a_bmp_beyond_the_prefix_says_so_in_coverage(self) -> None:
        path = self.root / "big.bmp"
        width = height = 64
        row = b"\x10\x20\x30" * width + b"\x00" * ((4 - width * 3 % 4) % 4)
        pixels = row * height
        header = b"BM" + struct.pack("<IHHI", 54 + len(pixels), 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
        path.write_bytes(header + pixels)
        read = read_image_metadata_full(path, metadata_bytes=40)
        self.assertEqual(read.partial, "메타데이터 앞 40바이트만 확인(--metadata-bytes) — 이후 부분의 문자열 메타데이터는 검사하지 않음")
        whole = read_image_metadata_full(path)
        self.assertIsNone(whole.partial)
        if HAVE_PIL:
            item = analyze_file(path, root=self.root, metadata_bytes=40)
            assert item.result is not None
            self.assertEqual(_metadata_entry(item.result), (CoverageStatus.RAN, read.partial))
            self.assertIn(read.partial, item.result.limitations)

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")  # a decodable JPEG
    def test_a_jpeg_whose_segments_outrun_the_prefix_says_so(self) -> None:
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), (90, 120, 150)).save(buffer, "JPEG")
        jpeg = buffer.getvalue()
        app = b"\xff\xe2" + struct.pack(">H", 2 + 60000) + b"\x00" * 60000  # a 60 KB APP2 segment before the rest
        path = self.root / "app.jpg"
        path.write_bytes(jpeg[:2] + app + jpeg[2:])
        read = read_image_metadata_full(path, metadata_bytes=1000)
        self.assertIsNone(read.error)
        self.assertEqual(read.partial, "메타데이터 앞 1000바이트만 확인(--metadata-bytes) — SOS 마커 전 세그먼트를 끝까지 읽지 못함")


class WebpXmpIsWalkedTest(unittest.TestCase):
    def test_an_xmp_chunk_after_the_image_data_is_read(self) -> None:
        xmp = (
            b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            b'<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:CreatorTool="Midjourney"/></rdf:RDF></x:xmpmeta>'
        )
        vp8x = b"VP8X" + struct.pack("<I", 10) + bytes([0x04, 0, 0, 0]) + (63).to_bytes(3, "little") + (63).to_bytes(3, "little")
        image = b"VP8L" + struct.pack("<I", 200_000) + b"\x00" * 200_000
        xmp_chunk = b"XMP " + struct.pack("<I", len(xmp)) + xmp + (b"\x00" if len(xmp) % 2 else b"")
        body = b"WEBP" + vp8x + image + xmp_chunk
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.webp"
            path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
            read = read_image_metadata_full(path, metadata_bytes=4096)
        self.assertEqual(read.metadata.get("xmp.CreatorTool"), "Midjourney")


if __name__ == "__main__":
    unittest.main()

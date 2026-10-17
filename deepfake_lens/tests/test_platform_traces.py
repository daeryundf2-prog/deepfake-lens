"""TB-4: platform / messenger traces are facts — JPEG recompression tables, file-name formats, codec and band traces."""

from __future__ import annotations

import importlib.util
import json
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from deepfake_lens.core import analyze_file
from deepfake_lens.platform_traces import (
    AUDIO_CHANNEL_CHECK,
    FILE_NAME_CHECK,
    LIBRARY_PATH,
    PATH_LIMITATION,
    RECOMPRESSION_CHECK,
    ZIGZAG,
    codec_trace,
    effective_bandwidth,
    file_name_layers,
    load_library,
    parse_jpeg,
    recompression_layers,
)
from deepfake_lens.result_types import CoverageStatus, EvidenceDirection, Verdict

HAVE_NUMPY = importlib.util.find_spec("numpy") is not None
HAVE_PIL = importlib.util.find_spec("PIL") is not None
HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def jpeg_header(luma: list[int], chroma: list[int], *, sampling: int = 0x22, exif: bool = False) -> bytes:
    """A JPEG prefix (SOI, optional APP1 Exif, DQT x2 in zigzag order, SOF0 3 components, SOS) — enough for the parser."""
    def zz(table: list[int]) -> bytes:
        return bytes(table[index] for index in ZIGZAG)

    out = b"\xff\xd8"
    if exif:
        body = b"Exif\x00\x00" + b"MM\x00*\x00\x00\x00\x08\x00\x00"
        out += b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
    dqt = b"\x00" + zz(luma) + b"\x01" + zz(chroma)
    out += b"\xff\xdb" + struct.pack(">H", len(dqt) + 2) + dqt
    sof = b"\x08" + struct.pack(">HH", 480, 640) + b"\x03" + bytes([1, sampling, 0, 2, 0x11, 1, 3, 0x11, 1])
    out += b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof
    out += b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
    return out


class LibraryTest(unittest.TestCase):
    def test_every_entry_has_a_source(self) -> None:
        data = json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
        for table in data["jpeg_tables"]:
            self.assertEqual(len(table["luma"]), 64)
            self.assertTrue(table["source"] and table["confidence"] and table["checked"])
        for pattern in data["filename_patterns"]:
            self.assertTrue(pattern["source"] and pattern["confidence"])


class JpegTablesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.whatsapp = load_library().data["jpeg_tables"][0]["luma"]

    def test_whatsapp_android_table_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.jpg"
            path.write_bytes(jpeg_header(self.whatsapp, self.whatsapp))
            layers = recompression_layers(path)
        [entry] = layers.coverage
        self.assertEqual(entry.status, CoverageStatus.RAN)
        self.assertIn("WhatsApp", entry.reason)
        [item] = layers.evidence
        self.assertEqual(item.direction, EvidenceDirection.NEUTRAL)
        self.assertIn("EXIF가 없어", item.detail)

    def test_other_chroma_does_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.jpg"
            path.write_bytes(jpeg_header(self.whatsapp, [min(255, v + 1) for v in self.whatsapp], exif=True))
            layers = recompression_layers(path)
        self.assertEqual(layers.evidence, [])
        self.assertIn("무정보", layers.coverage[0].reason)
        self.assertIn("EXIF 있음", layers.coverage[0].reason)

    def test_parser_reads_subsampling(self) -> None:
        structure = parse_jpeg(jpeg_header(self.whatsapp, self.whatsapp, sampling=0x21))
        self.assertEqual(structure.subsampling, "4:2:2")
        self.assertEqual((structure.width, structure.height), (640, 480))

    @unittest.skipUnless(HAVE_PIL, "Pillow required")
    def test_natural_order_against_pillow_ijg_quality_75(self) -> None:
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.new("RGB", (16, 16), (120, 80, 40)).save(buffer, "JPEG", quality=75)
        structure = parse_jpeg(buffer.getvalue())
        # IJG Annex K luma table scaled to quality 75 (scale 50%), natural order, first row.
        self.assertEqual(structure.tables[0][:8], [8, 6, 5, 8, 12, 20, 26, 31])

    def test_not_a_jpeg_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.png"
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
            self.assertEqual(recompression_layers(path).coverage[0].status, CoverageStatus.SKIPPED)


class FileNameTest(unittest.TestCase):
    def check(self, name: str) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_bytes(b"x")
            return [item.title for item in file_name_layers(path).evidence]

    def test_known_formats(self) -> None:
        self.assertEqual(self.check("KakaoTalk_20220313_104118161.jpg"), ["파일 이름 형식: 카카오톡에서 저장한 파일 이름 형식"])
        self.assertEqual(len(self.check("Screenshot_20201126-201222_Samsung Internet.jpg")), 1)
        self.assertEqual(len(self.check("스크린샷 2022-08-04 오전 4.19.15.png")), 1)
        self.assertEqual(len(self.check("IMG-20160314-WA0038.jpg")), 1)
        self.assertEqual(len(self.check("photo_2024-01-02_10-11-12.jpg")), 1)
        self.assertEqual(len(self.check("screen-20240102-101112.mp4")), 1)

    def test_ordinary_names_do_not_match(self) -> None:
        for name in ("IMG_1234.JPG", "holiday.jpg", "KakaoTalk.jpg", "Screenshot.png"):
            self.assertEqual(self.check(name), [], name)

    def test_name_is_part_of_the_cache_key(self) -> None:
        from deepfake_lens.scan_cache import _cache_key

        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.jpg", Path(tmp) / "KakaoTalk_20220313_104118161.jpg"
            a.write_bytes(b"same")
            b.write_bytes(b"same")
            keys = [_cache_key(p, text_bytes=1, metadata_bytes=1, pixel_mode="off", pixel_max_side=1, heatmaps=False, model_path=None, scan_context="") for p in (a, b)]
        self.assertNotEqual(keys[0], keys[1])


class AudioChannelTest(unittest.TestCase):
    def test_codec_magic(self) -> None:
        self.assertEqual(codec_trace(b"#!AMR-WB\n\x00\x00")[0], "AMR-WB")
        self.assertEqual(codec_trace(b"#!AMR\n\x00")[0], "AMR-NB")
        head = b"OggS" + bytes(24) + b"OpusHead\x01\x01\x38\x01" + struct.pack("<I", 16000) + b"\x00\x00\x00"
        head += b"OggS" + bytes(24) + b"OpusTags" + struct.pack("<I", 21) + b"Encoded with WhatsApp" + bytes(4)
        codec, detail, vendor = codec_trace(head)
        self.assertEqual((codec, vendor), ("Opus", "Encoded with WhatsApp"))
        self.assertIn("16000 Hz", detail)
        self.assertEqual(codec_trace(b"RIFF....WAVE")[0], "")

    @unittest.skipUnless(HAVE_NUMPY, "numpy required")
    def test_bandwidth_of_band_limited_noise(self) -> None:
        import numpy as np

        rng = np.random.default_rng(1)
        noise = rng.standard_normal(48000 * 3)
        spectrum = np.fft.rfft(noise)
        spectrum[int(3400 * len(noise) / 48000):] = 0
        limited = np.fft.irfft(spectrum, n=len(noise))
        bandwidth = effective_bandwidth(limited, 48000)
        assert bandwidth is not None
        self.assertAlmostEqual(bandwidth, 3400, delta=150)
        full = effective_bandwidth(noise, 48000)
        assert full is not None
        self.assertGreater(full, 23000)

    @unittest.skipUnless(HAVE_NUMPY and HAVE_FFMPEG, "numpy and ffmpeg required")
    def test_audio_row_names_the_narrow_band_and_never_concludes(self) -> None:
        import numpy as np

        rng = np.random.default_rng(2)
        noise = rng.standard_normal(16000 * 3)
        spectrum = np.fft.rfft(noise)
        spectrum[int(3300 * len(noise) / 16000):] = 0
        limited = np.fft.irfft(spectrum, n=len(noise))
        pcm = (limited / np.abs(limited).max() * 20000).astype("<i2").tobytes()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "call.wav"
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(pcm)
            result = analyze_file(path).result
        assert result is not None
        [entry] = [c for c in result.coverage if c.check == AUDIO_CHANNEL_CHECK]
        self.assertEqual(entry.status, CoverageStatus.RAN)
        titles = [e.title for e in result.evidence]
        self.assertIn("음성 유효 대역폭 제한", titles)
        self.assertIn(PATH_LIMITATION, result.limitations)
        self.assertNotEqual(result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        self.assertNotEqual(result.verdict_code, Verdict.AUTHENTICITY_EVIDENCE)


class ImageRowTest(unittest.TestCase):
    def test_image_row_carries_both_checks(self) -> None:
        whatsapp = load_library().data["jpeg_tables"][0]["luma"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "IMG-20240102-WA0001.jpg"
            path.write_bytes(jpeg_header(whatsapp, whatsapp))
            result = analyze_file(path).result
        assert result is not None
        checks = {c.check: c for c in result.coverage}
        self.assertEqual(checks[RECOMPRESSION_CHECK].status, CoverageStatus.RAN)
        self.assertEqual(checks[FILE_NAME_CHECK].status, CoverageStatus.RAN)
        self.assertIn(PATH_LIMITATION, result.limitations)


if __name__ == "__main__":
    unittest.main()

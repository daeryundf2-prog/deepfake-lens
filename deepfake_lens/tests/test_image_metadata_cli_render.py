from __future__ import annotations

import argparse
import contextlib
import io
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deepfake_lens import cli_render, image_metadata
from deepfake_lens.result_types import SourceGuess


def _tiff(order: str, entries: list[tuple[int, int, int, int]]) -> bytes:
    head = (b"II*\x00" if order == "<" else b"MM\x00*") + struct.pack(order + "I", 8)
    body = struct.pack(order + "H", len(entries))
    for tag, field_type, count, value in entries:
        packed = struct.pack(order + "H", value) + b"\x00\x00" if field_type == 3 else struct.pack(order + "I", value)
        body += struct.pack(order + "HHI", tag, field_type, count) + packed
    return head + body + b"\x00\x00\x00\x00"


def _jpeg(width: int, height: int) -> bytes:
    app0 = b"\xff\xe0\x00\x04ab"
    sof = b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8" + app0 + sof + b"\x00" * 16


def _webp_vp8x(width: int, height: int) -> bytes:
    chunk = b"\x00\x00\x00\x00" + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    body = b"WEBP" + b"VP8X" + struct.pack("<I", len(chunk)) + chunk
    return b"RIFF" + struct.pack("<I", len(body)) + body


class ImageDimensionTests(unittest.TestCase):
    def test_jpeg_dimensions(self) -> None:
        self.assertEqual(image_metadata._read_jpeg_dimensions(_jpeg(640, 480)), (640, 480))

    def test_jpeg_without_sof_or_truncated_returns_none(self) -> None:
        self.assertIsNone(image_metadata._read_jpeg_dimensions(b"\xff\xd8"))
        self.assertIsNone(image_metadata._read_jpeg_dimensions(b"not jpeg at all"))
        self.assertIsNone(image_metadata._read_jpeg_dimensions(b"\xff\xd8\xff\xe0\xff\xff" + b"\x00" * 16))
        self.assertIsNone(image_metadata._read_jpeg_dimensions(b"\xff\xd8" + b"\xff\xe0\x00\x04ab" + b"\x00" * 16))

    def test_webp_vp8x_dimensions(self) -> None:
        self.assertEqual(image_metadata._read_webp_dimensions(_webp_vp8x(1024, 768)), (1024, 768))

    def test_webp_invalid_inputs_return_none(self) -> None:
        self.assertIsNone(image_metadata._read_webp_dimensions(b"RIFF" + b"\x00" * 40))
        oversized = b"RIFF\x00\x00\x00\x00WEBPVP8X" + struct.pack("<I", 9999) + b"\x00" * 20
        self.assertIsNone(image_metadata._read_webp_dimensions(oversized))
        other_chunk = b"RIFF\x00\x00\x00\x00WEBPJUNK" + struct.pack("<I", 3) + b"abc\x00" + b"\x00" * 20
        self.assertIsNone(image_metadata._read_webp_dimensions(other_chunk))

    def test_tiff_dimensions_both_byte_orders_and_field_types(self) -> None:
        for order in ("<", ">"):
            with self.subTest(order=order):
                data = _tiff(order, [(256, 3, 1, 300), (257, 4, 1, 200)])
                self.assertEqual(image_metadata._read_tiff_dimensions(data), (300, 200))

    def test_tiff_missing_tags_or_bad_ifd_returns_none(self) -> None:
        self.assertIsNone(image_metadata._read_tiff_dimensions(_tiff("<", [(256, 3, 1, 300)])))
        self.assertIsNone(image_metadata._read_tiff_dimensions(_tiff("<", [(256, 3, 2, 300), (257, 3, 1, 5)])))
        self.assertIsNone(image_metadata._read_tiff_dimensions(b"II*\x00\xff\xff\xff\x7f"))
        too_many = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 500)
        self.assertIsNone(image_metadata._read_tiff_dimensions(too_many))


class ReadImageMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _read(self, name: str, data: bytes):
        path = self.root / name
        path.write_bytes(data)
        return image_metadata.read_image_metadata(path)

    def test_container_dispatch_by_magic_bytes(self) -> None:
        bmp = b"BM" + b"\x00" * 16 + struct.pack("<ii", 50, -30) + b"\x00" * 8
        cases = {
            "a.jpg": (_jpeg(11, 7), (11, 7)),
            "a.webp": (_webp_vp8x(33, 22), (33, 22)),
            "a.bmp": (bmp, (50, 30)),
            "a.gif": (b"GIF89a" + struct.pack("<HH", 12, 9) + b"\x00" * 4, (12, 9)),
            "a.tif": (_tiff("<", [(256, 3, 1, 8), (257, 3, 1, 6)]), (8, 6)),
            "a.bin": (b"unknown format header", None),
        }
        for name, (data, expected) in cases.items():
            with self.subTest(name=name):
                _, dimensions = self._read(name, data)
                self.assertEqual(dimensions, expected)

    def test_bmp_with_non_positive_width_has_no_dimensions(self) -> None:
        bmp = b"BM" + b"\x00" * 16 + struct.pack("<ii", 0, 10) + b"\x00" * 8
        self.assertIsNone(self._read("zero.bmp", bmp)[1])

    def test_header_text_collects_generator_markers_only(self) -> None:
        data = b"\x00\x01plain text here\x00prompt: a red fox\x00Sampler: Euler a\x00"
        metadata, _ = self._read("t.bin", data)
        self.assertEqual(metadata["header.text"].splitlines(), ["prompt: a red fox", "Sampler: Euler a"])

    def test_no_markers_means_no_header_text_key(self) -> None:
        metadata, _ = self._read("quiet.bin", b"\x00nothing interesting in here\x00")
        self.assertNotIn("header.text", metadata)

    def test_guess_source_empty_metadata_is_unknown(self) -> None:
        self.assertEqual(image_metadata.guess_image_source({}), SourceGuess.unknown())
        self.assertEqual(image_metadata.guess_image_source({"k": "   "}), SourceGuess.unknown())

    def test_guess_source_recognises_generation_parameters(self) -> None:
        params = {"png.parameters": "a cat\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 12345, Size: 512x512"}
        guess = image_metadata.guess_image_source(params)
        self.assertNotEqual(guess, SourceGuess.unknown())
        self.assertIn("A1111", guess.label)

    def test_guess_source_ignores_unrelated_metadata(self) -> None:
        self.assertEqual(
            image_metadata.guess_image_source({"png.software": "paint", "png.title": "holiday photo"}),
            SourceGuess.unknown(),
        )


class CliRenderHelperTests(unittest.TestCase):
    def test_parse_split_ratios_accepts_valid_values(self) -> None:
        self.assertEqual(cli_render._parse_split_ratios("8, 1,1"), (8.0, 1.0, 1.0))
        self.assertEqual(cli_render._parse_split_ratios("0,0,1"), (0.0, 0.0, 1.0))

    def test_parse_split_ratios_rejects_bad_input(self) -> None:
        for value in ("1,2", "1,2,3,4", "a,b,c", "-1,1,1", "0,0,0"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                cli_render._parse_split_ratios(value)

    def test_parse_csv_strips_and_drops_blanks(self) -> None:
        self.assertEqual(cli_render._parse_csv(" a, b ,,c ,"), ["a", "b", "c"])
        self.assertEqual(cli_render._parse_csv(""), [])

    def test_has_subdirectories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "file.txt").write_text("x", encoding="utf-8")
            self.assertFalse(cli_render._has_subdirectories(root))
            (root / "child").mkdir()
            self.assertTrue(cli_render._has_subdirectories(root))
            self.assertFalse(cli_render._has_subdirectories(root / "missing"))

    def test_write_json_out_creates_parents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a" / "b" / "out.json"
            cli_render._write_json_out(target, '{"ok": true}')
            self.assertEqual(target.read_text(encoding="utf-8"), '{"ok": true}')

    def test_maybe_sign_passthrough_and_unsigned_note(self) -> None:
        payload: dict[str, object] = {"a": 1}
        self.assertIs(cli_render._maybe_sign(payload, sign=False, key_file=None), payload)
        env = {k: v for k, v in os.environ.items() if k != "DEEPFAKE_LENS_REPORT_KEY"}
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(stderr):
            result = cli_render._maybe_sign(payload, sign=True, key_file=None)
        self.assertIsNone(result.get("signature"))
        self.assertIn("unsigned", stderr.getvalue())

    def test_write_csv_header_and_provenance_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            default_csv = Path(tmp) / "default.csv"
            cli_render._write_csv(default_csv, [])
            lines = default_csv.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "# thresholds_source=builtin_defaults provisional=true")
            self.assertTrue(lines[1].startswith("path,kind,status,score,risk"))
            self.assertEqual(len(lines), 2)

            profiled = Path(tmp) / "profiled.csv"
            thresholds = {"source": "measured", "version": "v2", "provisional": False, "samples": 80, "dataset_fingerprint": "abc"}
            cli_render._write_csv(profiled, [], coverage={"weights_available": 2, "weights_total": 5}, thresholds=thresholds)
            lines = profiled.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "# weights_available=2 weights_total=5")
            self.assertEqual(
                lines[1],
                "# thresholds_source=profile:v2 provisional=False samples=80 fingerprint=abc",
            )

            builtin = Path(tmp) / "builtin.csv"
            cli_render._write_csv(builtin, [], thresholds={"source": "builtin_defaults"})
            self.assertEqual(
                builtin.read_text(encoding="utf-8").splitlines()[0],
                "# thresholds_source=builtin_defaults provisional=true",
            )

    def test_file_text_reads_plain_text_and_ignores_other_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            text_file = Path(tmp) / "note.log"
            text_file.write_text("hello log", encoding="utf-8")
            self.assertEqual(cli_render._file_text(text_file), "hello log")
            self.assertIsNone(cli_render._file_text(Path(tmp) / "image.png"))
            self.assertIsNone(cli_render._file_text(Path(tmp) / "missing.log"))


if __name__ == "__main__":
    unittest.main()

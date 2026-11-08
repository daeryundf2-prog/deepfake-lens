from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

from deepfake_lens import checkpoint_integrity, jpegq, png, release


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _png(*chunks: bytes, iend: bool = True) -> bytes:
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 7, 5, 8, 2, 0, 0, 0))
    tail = _chunk(b"IEND", b"") if iend else b""
    return png.PNG_SIGNATURE + ihdr + b"".join(chunks) + tail


class PngMetadataTests(unittest.TestCase):
    def test_non_png_returns_empty(self) -> None:
        self.assertEqual(png.read_png_metadata(b"not a png"), {})
        self.assertIsNone(png.read_png_dimensions(b"not a png" * 4))

    def test_dimensions_from_ihdr(self) -> None:
        self.assertEqual(png.read_png_dimensions(_png()), (7, 5))

    def test_dimensions_require_ihdr_first(self) -> None:
        data = png.PNG_SIGNATURE + _chunk(b"tEXt", b"a\x00b") + b"\x00" * 8
        self.assertIsNone(png.read_png_dimensions(data))

    def test_text_chunk_key_is_normalized_and_prefixed(self) -> None:
        meta = png.read_png_metadata(_png(_chunk(b"tEXt", b"Software\x00Tool 1.0")))
        self.assertEqual(len(meta), 1)
        self.assertTrue(next(iter(meta)).startswith("png."))
        self.assertEqual(next(iter(meta.values())), "Tool 1.0")

    def test_text_chunk_without_separator_or_value_is_ignored(self) -> None:
        data = _png(_chunk(b"tEXt", b"noseparator"), _chunk(b"tEXt", b"key\x00   "))
        self.assertEqual(png.read_png_metadata(data), {})

    def test_compressed_text_chunk(self) -> None:
        payload = b"Comment\x00\x00" + zlib.compress("hello zTXt".encode())
        meta = png.read_png_metadata(_png(_chunk(b"zTXt", payload)))
        self.assertEqual(list(meta.values()), ["hello zTXt"])

    def test_compressed_text_rejects_unknown_method_and_garbage(self) -> None:
        bad_method = b"Comment\x00\x01" + zlib.compress(b"x")
        garbage = b"Comment\x00\x00" + b"not zlib data"
        self.assertEqual(png.read_png_metadata(_png(_chunk(b"zTXt", bad_method))), {})
        self.assertEqual(png.read_png_metadata(_png(_chunk(b"zTXt", garbage))), {})

    def test_international_text_plain_and_compressed(self) -> None:
        plain = b"Title\x00\x00\x00en\x00titre\x00caf\xc3\xa9"
        packed = b"Source\x00\x01\x00\x00\x00" + zlib.compress(b"compressed value")
        meta = png.read_png_metadata(_png(_chunk(b"iTXt", plain), _chunk(b"iTXt", packed)))
        self.assertEqual(sorted(meta.values()), ["café", "compressed value"])

    def test_international_text_rejects_malformed(self) -> None:
        cases = {
            "empty_keyword": b"\x00\x00\x00\x00\x00",
            "truncated_header": b"K\x00\x00",
            "missing_language_terminator": b"K\x00\x00\x00en",
            "missing_translated_terminator": b"K\x00\x00\x00en\x00tr",
            "unknown_compression_flag": b"K\x00\x02\x00\x00\x00data",
            "undecodable_payload": b"K\x00\x01\x00\x00\x00not zlib",
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                self.assertEqual(png.read_png_metadata(_png(_chunk(b"iTXt", payload))), {})

    def test_parsing_stops_at_iend(self) -> None:
        data = _png(iend=True) + _chunk(b"tEXt", b"After\x00ignored")
        self.assertEqual(png.read_png_metadata(data), {})

    def test_truncated_chunk_stops_parsing_without_error(self) -> None:
        good = _chunk(b"tEXt", b"Key\x00value")
        data = png.PNG_SIGNATURE + good + good[:-6]
        self.assertEqual(len(png.read_png_metadata(data)), 1)

    def test_oversized_chunk_length_stops_parsing(self) -> None:
        oversized = struct.pack(">I", png.MAX_CHUNK_BYTES + 1) + b"tEXt" + b"\x00" * 16
        self.assertEqual(png.read_png_metadata(png.PNG_SIGNATURE + oversized), {})

    def test_decompression_bomb_is_rejected(self) -> None:
        bomb = zlib.compress(b"\x00" * (png.MAX_CHUNK_BYTES + 10))
        self.assertIsNone(png._decompress_limited(bomb))
        self.assertEqual(png._decompress_limited(zlib.compress(b"ok")), b"ok")
        self.assertIsNone(png._decompress_limited(zlib.compress(b"truncated")[:-4]))


class CheckpointIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.checkpoint = Path(self._tmp.name) / "model.pth"
        self.checkpoint.write_bytes(b"checkpoint-bytes")
        self.digest = hashlib.sha256(b"checkpoint-bytes").hexdigest()
        env = {k: v for k, v in os.environ.items() if k != "DEEPFAKE_LENS_CHECKPOINT_SHA256"}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _sidecar(self, text: str) -> None:
        (self.checkpoint.parent / "model.pth.sha256").write_text(text, encoding="utf-8")

    def test_no_pin_means_unverified_and_passes(self) -> None:
        checkpoint_integrity.verify_checkpoint_sha256(self.checkpoint)

    def test_sidecar_match_accepts_sha256sum_format_and_uppercase(self) -> None:
        self._sidecar(f"{self.digest.upper()}  model.pth\n")
        checkpoint_integrity.verify_checkpoint_sha256(self.checkpoint)

    def test_sidecar_mismatch_raises(self) -> None:
        self._sidecar("0" * 64)
        with self.assertRaisesRegex(RuntimeError, "sha256 mismatch"):
            checkpoint_integrity.verify_checkpoint_sha256(self.checkpoint)

    def test_malformed_sidecar_raises(self) -> None:
        for text in ("", "short", "z" * 64):
            with self.subTest(text=text):
                self._sidecar(text)
                with self.assertRaisesRegex(RuntimeError, "malformed sha256 sidecar"):
                    checkpoint_integrity.verify_checkpoint_sha256(self.checkpoint)

    def test_env_pin_is_enforced_when_no_sidecar(self) -> None:
        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_CHECKPOINT_SHA256": self.digest}):
            checkpoint_integrity.verify_checkpoint_sha256(self.checkpoint)
        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_CHECKPOINT_SHA256": "f" * 64}):
            with self.assertRaises(RuntimeError):
                checkpoint_integrity.verify_checkpoint_sha256(self.checkpoint)

    def test_declared_pin_wins_over_sidecar(self) -> None:
        self._sidecar("0" * 64)
        checkpoint_integrity.verify_checkpoint_sha256(self.checkpoint, f" {self.digest.upper()} ")
        with self.assertRaises(RuntimeError):
            checkpoint_integrity.verify_checkpoint_sha256(self.checkpoint, "1" * 64)

    def test_load_torch_state_verifies_before_loading(self) -> None:
        fake_torch = mock.Mock()
        fake_torch.load.return_value = {"w": 1}
        with mock.patch.dict("sys.modules", {"torch": fake_torch}):
            state = checkpoint_integrity.load_torch_state(self.checkpoint, expected_sha256=self.digest)
            self.assertEqual(state, {"w": 1})
            kwargs = fake_torch.load.call_args.kwargs
            self.assertIs(kwargs["weights_only"], True)
            fake_torch.load.reset_mock()
            with self.assertRaises(RuntimeError):
                checkpoint_integrity.load_torch_state(self.checkpoint, expected_sha256="2" * 64)
            fake_torch.load.assert_not_called()


class ReleaseChecklistTests(unittest.TestCase):
    def test_checklist_reports_present_and_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                '[project.scripts]\ndeepfake-lens = "deepfake_lens.cli:main"\n', encoding="utf-8"
            )
            payload = release.build_release_checklist(root)
        files = {item["path"]: item for item in payload["files"]}
        self.assertTrue(files["pyproject.toml"]["exists"])
        self.assertGreater(files["pyproject.toml"]["size_bytes"], 0)
        self.assertFalse(files["deepfake_lens/cli.py"]["exists"])
        self.assertEqual(files["deepfake_lens/cli.py"]["size_bytes"], 0)
        self.assertTrue(payload["entrypoint_present"])
        self.assertEqual(payload["commands"], release.RELEASE_CHECK_COMMANDS)

    def test_missing_pyproject_means_no_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(release.build_release_checklist(tmp)["entrypoint_present"])

    def test_write_checklist_creates_parent_dirs_and_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "dir" / "release.json"
            payload = release.write_release_checklist(tmp, out)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), payload)


class JpegQualityTests(unittest.TestCase):
    def test_scaled_table_known_values(self) -> None:
        self.assertEqual(jpegq._scaled_table(50), jpegq._LUMA_BASE_ZZ)
        self.assertEqual(set(jpegq._scaled_table(100)), {1})
        self.assertEqual(jpegq._scaled_table(0), jpegq._scaled_table(1))
        self.assertEqual(jpegq._scaled_table(1000), jpegq._scaled_table(100))
        self.assertLessEqual(max(jpegq._scaled_table(1)), 255)

    def test_unreadable_files_return_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "junk.jpg"
            junk.write_bytes(b"not an image")
            self.assertIsNone(jpegq.estimate_jpeg_quality(junk))
            self.assertIsNone(jpegq.image_min_side(junk))
            self.assertIsNone(jpegq.estimate_jpeg_quality(Path(tmp) / "missing.jpg"))

    def test_estimate_recovers_encoder_quality(self) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        with tempfile.TemporaryDirectory() as tmp:
            for quality in (30, 75, 90):
                path = Path(tmp) / f"q{quality}.jpg"
                Image.new("RGB", (24, 16), (120, 40, 200)).save(path, quality=quality)
                with self.subTest(quality=quality):
                    self.assertEqual(jpegq.estimate_jpeg_quality(path), float(quality))
                    self.assertEqual(jpegq.image_min_side(path), 16)
            bmp = Path(tmp) / "plain.png"
            Image.new("RGB", (9, 9)).save(bmp)
            self.assertIsNone(jpegq.estimate_jpeg_quality(bmp))
            self.assertEqual(jpegq.image_min_side(bmp), 9)


if __name__ == "__main__":
    unittest.main()

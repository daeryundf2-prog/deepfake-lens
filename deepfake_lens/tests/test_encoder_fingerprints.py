"""Encoder fingerprints: the IJG standard JPEG tables and messenger video re-encode formats are neutral facts."""

from __future__ import annotations

import importlib.util
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path

from deepfake_lens import container_metadata as cm
from deepfake_lens.encoder_fingerprints import TABLE_PATH, compare_ijg, ijg_scaled, jpeg_ijg_comparison, load_table
from deepfake_lens.evidence_rules import (
    JPEG_IJG_TITLE,
    JPEG_NON_IJG_TITLE,
    JPEG_RESAVE_CUE_TITLE,
    MESSENGER_VIDEO_TITLE,
    jpeg_table_evidence,
)
from deepfake_lens.platform_traces import PATH_LIMITATION, parse_jpeg
from deepfake_lens.result_types import CoverageStatus, EvidenceDirection, EvidenceStrength
from deepfake_lens.tests.test_container_metadata import box, full, hdlr, itunes_meta

HAVE_PIL = importlib.util.find_spec("PIL") is not None


def pillow_jpeg(quality: int, *, mode: str = "RGB", qtables: list[list[int]] | None = None) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    image = Image.new(mode, (64, 48), 120 if mode == "L" else (120, 30, 200))
    if qtables is None:
        image.save(out, "JPEG", quality=quality)
    else:
        image.save(out, "JPEG", qtables=qtables)
    return out.getvalue()


def video_mp4(width: int, height: int, *, profile: int = 66, level: int = 41, encoder: str = "Lavf57.56.101") -> bytes:
    fields = (
        b"\x00" * 6 + struct.pack(">H", 1) + b"\x00" * 16 + struct.pack(">HH", width, height) + struct.pack(">II", 0x480000, 0x480000)
        + b"\x00" * 4 + struct.pack(">H", 1) + b"\x00" * 32 + struct.pack(">Hh", 0x18, -1)
    )
    avc1 = box(b"avc1", fields + box(b"avcC", bytes([1, profile, 0xC0, level, 0xFF, 0xE0])))
    stbl = box(b"stbl", full(b"stsd", struct.pack(">I", 1) + avc1))
    trak = box(b"trak", box(b"mdia", hdlr(b"vide", "VideoHandler") + box(b"minf", stbl)))
    udta = box(b"udta", itunes_meta([(b"\xa9too", encoder)]))
    return box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41") + box(b"moov", trak + udta)


class _Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.folder = Path(self._dir.name)

    def tearDown(self) -> None:
        self._dir.cleanup()

    def write(self, name: str, data: bytes) -> Path:
        path = self.folder / name
        path.write_bytes(data)
        return path


class TableTest(unittest.TestCase):
    def test_every_entry_has_a_source(self) -> None:
        data = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
        standard = data["jpeg_ijg_standard"]
        self.assertEqual((len(standard["luma"]), len(standard["chroma"])), (64, 64))
        for entry in [standard, *data["messenger_video"]]:
            self.assertTrue(entry["source"] and entry["confidence"] and entry["checked"])

    def test_scaling_follows_ijg(self) -> None:
        base = load_table().data["jpeg_ijg_standard"]["luma"]
        self.assertEqual(ijg_scaled(base, 50), base)
        self.assertEqual(ijg_scaled(base, 100), [1] * 64)
        self.assertEqual(max(ijg_scaled(base, 5)), 255)
        self.assertGreater(max(ijg_scaled(base, 5, force_baseline=False)), 255)


@unittest.skipUnless(HAVE_PIL, "Pillow required")
class JpegTableTest(_Tmp):
    def test_pillow_qualities_match_exactly(self) -> None:
        from PIL import Image

        for quality in (10, 50, 75, 85, 95, 100):
            data = pillow_jpeg(quality)
            with Image.open(io.BytesIO(data)) as image:
                pillow_tables = {k: list(v) for k, v in image.quantization.items()}
            self.assertEqual(parse_jpeg(data).tables, pillow_tables)
            comparison = jpeg_ijg_comparison(self.write(f"q{quality}.jpg", data), load_table())
            self.assertIn(quality, comparison.qualities)
            self.assertEqual((comparison.differing, comparison.compared), (0, 128))

    def test_grayscale_compares_luma_only(self) -> None:
        comparison = jpeg_ijg_comparison(self.write("gray.jpg", pillow_jpeg(80, mode="L")), load_table())
        self.assertEqual((comparison.qualities, comparison.compared), ((80,), 64))

    def test_custom_tables_are_not_ijg(self) -> None:
        flat = [[3] * 64, [5] * 64]
        comparison = jpeg_ijg_comparison(self.write("custom.jpg", pillow_jpeg(0, qtables=flat)), load_table())
        self.assertFalse(comparison.matched)
        self.assertGreater(comparison.differing, 0)
        items = jpeg_table_evidence(comparison, {"exif.Make": "Canon"}, "표")
        self.assertEqual([i.title for i in items], [JPEG_NON_IJG_TITLE])

    def test_exif_make_with_ijg_table_is_a_neutral_cue(self) -> None:
        luma = ijg_scaled(load_table().data["jpeg_ijg_standard"]["luma"], 75)
        chroma = ijg_scaled(load_table().data["jpeg_ijg_standard"]["chroma"], 75)
        comparison = compare_ijg(luma, chroma, load_table())
        items = jpeg_table_evidence(comparison, {"exif.Make": "samsung", "exif.Model": "SM-S918N"}, "표")
        self.assertEqual([i.title for i in items], [JPEG_IJG_TITLE, JPEG_RESAVE_CUE_TITLE])
        self.assertIn("「samsung SM-S918N」", items[1].detail)
        for item in items:
            self.assertEqual((item.direction, item.strength), (EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK))
        self.assertEqual([i.title for i in jpeg_table_evidence(comparison, {}, "표")], [JPEG_IJG_TITLE])

    def test_image_check_reports_the_table(self) -> None:
        path = self.write("q75.jpg", pillow_jpeg(75))
        layers = cm.image_aigc_layers({"exif.Make": "Apple"}, "jpeg", None, None, path=path)
        entry = layers.coverage[0]
        self.assertEqual(entry.status, CoverageStatus.RAN)
        self.assertIn("IJG 표준 표(품질 75)와 일치", entry.reason)
        self.assertEqual([i.title for i in layers.evidence], [JPEG_IJG_TITLE, JPEG_RESAVE_CUE_TITLE])
        self.assertEqual([f.outcome for f in layers.findings], ["not_found"])

    def test_not_a_jpeg_is_a_note(self) -> None:
        layers = cm.image_aigc_layers({}, "jpeg", None, None, path=self.write("fake.jpg", b"not a jpeg"))
        self.assertEqual(layers.coverage[0].status, CoverageStatus.RAN)
        self.assertIn("JPEG 양자화표 대조 안 함", layers.coverage[0].reason)
        self.assertEqual(layers.evidence, [])


class MessengerVideoTest(_Tmp):
    def test_track_facts_are_parsed(self) -> None:
        facts = cm.read_container_metadata(self.write("k.mp4", video_mp4(720, 404)))
        video = facts.video
        assert video is not None
        self.assertEqual(video, cm.VideoTrack("H.264", 720, 404, 66, 41))
        self.assertEqual((video.profile, video.level), ("Baseline", "4.1"))

    def test_kakaotalk_normal_quality_matches(self) -> None:
        for size, platform in (((720, 404), "iOS"), ((404, 720), "iOS"), ((852, 480), "안드로이드")):
            layers = cm.media_layers(self.write("k.mp4", video_mp4(*size, level=31)))
            items = [i for i in layers.evidence if i.title == MESSENGER_VIDEO_TITLE]
            self.assertEqual(len(items), 1, size)
            self.assertIn(platform, items[0].detail)
            self.assertIn("「Lavf57.56.101」", items[0].detail)
            self.assertEqual((items[0].direction, items[0].strength), (EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK))
            self.assertIn(PATH_LIMITATION, layers.limitations)
            self.assertEqual([f.outcome for f in layers.findings], ["not_found"])

    def test_partial_matches_are_not_reported(self) -> None:
        for data in (
            video_mp4(720, 404, encoder="Lavf58.76.100"),
            video_mp4(720, 404, profile=100),
            video_mp4(1280, 720),
        ):
            layers = cm.media_layers(self.write("v.mp4", data))
            self.assertNotIn(MESSENGER_VIDEO_TITLE, [i.title for i in layers.evidence])
            self.assertNotIn(PATH_LIMITATION, layers.limitations)


if __name__ == "__main__":
    unittest.main()

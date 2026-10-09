"""D7/D16: JPEG/PNG EXIF and XMP parsing and the evidence rules built on it.

Fixtures are written with Pillow (EXIF via ``Image.Exif``, XMP via the
JPEG ``xmp=`` save argument / a PNG iTXt ``XML:com.adobe.xmp`` chunk), so
the parser is exercised on the bytes a real encoder produces.
"""

from __future__ import annotations

import builtins
import importlib.util
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest import mock

from deepfake_lens.core import analyze_file
from deepfake_lens.evidence_rules import (
    CAMERA_MIN_JPEG_QUALITY,
    GENERATOR_NAMES,
    camera_exif_evidence,
    generator_name_in,
    xmp_evidence,
)
from deepfake_lens.image_metadata import parse_xmp_fields, read_image_metadata_full
from deepfake_lens.result_types import (
    CoverageStatus,
    EvidenceDirection,
    EvidenceKind,
    EvidenceStrength,
    Verdict,
)

HAVE_PIL = importlib.util.find_spec("PIL") is not None and importlib.util.find_spec("numpy") is not None
TRAINED = "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"


def xmp_packet(*, creator_tool: str = "", source_type: str = "", creators: tuple[str, ...] = (), credit: str = "", doctype: bool = False) -> bytes:
    attrs = []
    if creator_tool:
        attrs.append(f'xmp:CreatorTool="{creator_tool}"')
    if source_type:
        attrs.append(f'Iptc4xmpExt:DigitalSourceType="{source_type}"')
    if credit:
        attrs.append(f'photoshop:Credit="{credit}"')
    creator_xml = ""
    if creators:
        items = "".join(f"<rdf:li>{name}</rdf:li>" for name in creators)
        creator_xml = f"<dc:creator><rdf:Seq>{items}</rdf:Seq></dc:creator>"
    dtd = '<!DOCTYPE x [<!ENTITY a "aaaa">]>' if doctype else ""
    return (
        f'<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>{dtd}'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
        'xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" '
        f'{" ".join(attrs)}>{creator_xml}</rdf:Description>'
        '</rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    ).encode("utf-8")


@unittest.skipUnless(HAVE_PIL, "Pillow + numpy required to write EXIF/XMP fixtures")
class ExifXmpFixtureTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _photo(self) -> Any:
        import numpy as np
        from PIL import Image

        rng = np.random.default_rng(7)
        base: Any = np.linspace(40, 200, 256, dtype=np.float32)
        pixels = np.stack([np.add.outer(base, base[::-1]) / 2 + rng.normal(0, 9, (256, 256)) for _ in range(3)], axis=-1)
        return Image.fromarray(np.clip(pixels, 0, 255).astype("uint8"), "RGB")

    def _camera_exif(self, *, software: str | None = None, original: str = "2024:05:01 10:20:30", gps_date: str | None = "2024:05:01", gps: bool = True) -> Any:
        from PIL import Image

        exif = Image.Exif()
        exif[271] = "Canon"
        exif[272] = "Canon EOS R5"
        if software is not None:
            exif[305] = software
        sub = exif.get_ifd(0x8769)
        sub[36867] = original
        sub[42036] = "RF24-105mm F4 L IS USM"
        if gps:
            gps_ifd = exif.get_ifd(0x8825)
            gps_ifd[1] = "N"
            gps_ifd[2] = (37.0, 33.0, 36.0)
            gps_ifd[3] = "E"
            gps_ifd[4] = (126.0, 58.0, 12.0)
            if gps_date:
                gps_ifd[29] = gps_date
        return exif

    def _jpeg(self, name: str, *, quality: int = 95, exif: Any = None, xmp: bytes | None = None) -> Path:
        path = self.root / name
        kwargs: dict[str, Any] = {"quality": quality}
        if exif is not None:
            kwargs["exif"] = exif
        if xmp is not None:
            kwargs["xmp"] = xmp
        self._photo().save(path, "JPEG", **kwargs)
        return path

    def _png_with_xmp(self, name: str, packet: bytes) -> Path:
        from PIL import PngImagePlugin

        info = PngImagePlugin.PngInfo()
        info.add_itxt("XML:com.adobe.xmp", packet.decode("utf-8"))
        path = self.root / name
        self._photo().save(path, "PNG", pnginfo=info)
        return path

    @staticmethod
    def _titles(path: Path) -> list[str]:
        item = analyze_file(path)
        assert item.result is not None
        return [e.title for e in item.result.evidence]

    # --- EXIF -------------------------------------------------------------

    def test_exif_fields_are_parsed(self) -> None:
        path = self._jpeg("camera.jpg", exif=self._camera_exif(software="Ver.1.8.1"))
        read = read_image_metadata_full(path)
        self.assertIsNone(read.error)
        self.assertTrue(read.exif_read)
        self.assertEqual(read.image_format, "jpeg")
        md = read.metadata
        self.assertEqual(md["exif.Make"], "Canon")
        self.assertEqual(md["exif.Model"], "Canon EOS R5")
        self.assertEqual(md["exif.Software"], "Ver.1.8.1")
        self.assertEqual(md["exif.DateTimeOriginal"], "2024:05:01 10:20:30")
        self.assertEqual(md["exif.LensModel"], "RF24-105mm F4 L IS USM")
        self.assertAlmostEqual(float(md["exif.GPSLatitude"]), 37.56, places=2)
        self.assertAlmostEqual(float(md["exif.GPSLongitude"]), 126.97, places=2)
        self.assertEqual(md["exif.GPSDateStamp"], "2024:05:01")

    def test_consistent_camera_exif_is_authentic_moderate_and_does_not_conclude(self) -> None:
        path = self._jpeg("camera.jpg", quality=95, exif=self._camera_exif())
        item = analyze_file(path)
        assert item.result is not None
        matches = [e for e in item.result.evidence if e.title == "카메라 EXIF 일관"]
        self.assertEqual(len(matches), 1, [e.title for e in item.result.evidence])
        evidence = matches[0]
        self.assertEqual(
            (evidence.kind, evidence.direction, evidence.strength),
            (EvidenceKind.DETERMINISTIC, EvidenceDirection.AUTHENTIC, EvidenceStrength.MODERATE),
        )
        self.assertIn("Canon EOS R5", evidence.detail)
        self.assertIn("RF24-105mm", evidence.detail)
        # Moderate authentic evidence never concludes (decision rule 5 needs strong).
        self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED)
        self.assertNotIn("메타데이터 부재", [e.title for e in item.result.evidence])

    def test_recompressed_jpeg_is_not_camera_consistent(self) -> None:
        path = self._jpeg("kakao.jpg", quality=75, exif=self._camera_exif())
        titles = self._titles(path)
        self.assertNotIn("카메라 EXIF 일관", titles)
        self.assertIn("카메라 EXIF 있음(일관성 조건 미충족)", titles)
        item = analyze_file(path)
        assert item.result is not None
        note = next(e for e in item.result.evidence if e.title.startswith("카메라 EXIF 있음"))
        self.assertEqual(note.direction, EvidenceDirection.NEUTRAL)
        self.assertIn(f"< {CAMERA_MIN_JPEG_QUALITY}", note.detail)

    def test_editor_software_breaks_consistency(self) -> None:
        path = self._jpeg("edited.jpg", exif=self._camera_exif(software="Adobe Photoshop 25.0 (Windows)"))
        self.assertNotIn("카메라 EXIF 일관", self._titles(path))

    def test_vendor_software_and_absent_software_are_consistent(self) -> None:
        for name, software in (("vendor.jpg", "Canon Digital Photo Professional"), ("none.jpg", None), ("fw.jpg", "Firmware Version 1.0.2")):
            with self.subTest(software=software):
                path = self._jpeg(name, exif=self._camera_exif(software=software))
                self.assertIn("카메라 EXIF 일관", self._titles(path))

    def test_unparseable_capture_time_and_gps_mismatch_break_consistency(self) -> None:
        cases = {
            "bad-time.jpg": self._camera_exif(original="0000:00:00 00:00:00"),
            "gps-date.jpg": self._camera_exif(gps_date="2023:01:01"),
        }
        for name, exif in cases.items():
            with self.subTest(name=name):
                self.assertNotIn("카메라 EXIF 일관", self._titles(self._jpeg(name, exif=exif)))

    # --- XMP --------------------------------------------------------------

    def test_jpeg_xmp_digital_source_type_is_strong_synthetic(self) -> None:
        path = self._jpeg("gen.jpg", xmp=xmp_packet(source_type=TRAINED))
        read = read_image_metadata_full(path)
        self.assertEqual(read.metadata.get("xmp.DigitalSourceType"), TRAINED)
        item = analyze_file(path)
        assert item.result is not None
        evidence = next(e for e in item.result.evidence if e.title == "XMP 디지털 출처 유형: 생성형 AI")
        self.assertEqual(
            (evidence.kind, evidence.direction, evidence.strength),
            (EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG),
        )
        self.assertEqual(item.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)

    def test_png_itxt_xmp_creator_tool_names_generator(self) -> None:
        path = self._png_with_xmp("mj.png", xmp_packet(creator_tool="Midjourney v6"))
        read = read_image_metadata_full(path)
        self.assertEqual(read.metadata.get("xmp.CreatorTool"), "Midjourney v6")
        item = analyze_file(path)
        assert item.result is not None
        self.assertEqual(item.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        titles = [e.title for e in item.result.evidence]
        self.assertEqual(titles.count("XMP 생성 도구 필드"), 1, titles)

    def test_dc_creator_seq_and_photoshop_credit(self) -> None:
        path = self._jpeg("credit.jpg", xmp=xmp_packet(creators=("Kim", "Generated by AI"), credit="Adobe Firefly"))
        md = read_image_metadata_full(path).metadata
        self.assertEqual(md["xmp.creator"], "Kim; Generated by AI")
        self.assertEqual(md["xmp.Credit"], "Adobe Firefly")
        self.assertEqual(self._titles(path).count("XMP 생성 도구 필드"), 2)

    def test_photographer_xmp_is_not_generator_evidence(self) -> None:
        path = self._jpeg("plain.jpg", xmp=xmp_packet(creator_tool="Adobe Photoshop Lightroom Classic 13.2", creators=("홍길동",)))
        item = analyze_file(path)
        assert item.result is not None
        self.assertFalse([e for e in item.result.evidence if e.direction == EvidenceDirection.SYNTHETIC])
        self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED)

    def test_xmp_with_entity_declaration_is_refused(self) -> None:
        self.assertEqual(parse_xmp_fields(xmp_packet(source_type=TRAINED, doctype=True)), {})
        self.assertEqual(parse_xmp_fields(b"<x:xmpmeta><broken"), {})

    def test_camera_exif_with_generator_xmp_is_not_consistent(self) -> None:
        path = self._jpeg("mixed.jpg", exif=self._camera_exif(), xmp=xmp_packet(creator_tool="Stable Diffusion XL"))
        item = analyze_file(path)
        assert item.result is not None
        titles = [e.title for e in item.result.evidence]
        self.assertNotIn("카메라 EXIF 일관", titles)
        self.assertEqual(item.result.verdict_code, Verdict.MANIPULATION_EVIDENCE)

    # --- D16: failed reads are failures, not absence ------------------------

    def test_truncated_jpeg_is_a_failed_read_not_missing_metadata(self) -> None:
        full = self._jpeg("full.jpg", exif=self._camera_exif())
        cut = self.root / "cut.jpg"
        data = full.read_bytes()
        sos = data.index(b"\xff\xda")
        cut.write_bytes(data[: sos - 10])
        item = analyze_file(cut)
        assert item.result is not None
        metadata_entry = next(c for c in item.result.coverage if c.check == "metadata")
        self.assertEqual(metadata_entry.status, CoverageStatus.FAILED)
        self.assertIn("잘린 파일", metadata_entry.reason)
        self.assertNotIn("메타데이터 부재", [e.title for e in item.result.evidence])
        self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED)

    def test_truncated_body_camera_exif_is_not_evaluated(self) -> None:
        """R9: EXIF read intact but image data cut after SOS — the decode
        (image_class) check fails, so no "카메라 EXIF 일관": a neutral/weak
        "EXIF 존재(파일 손상으로 일관성 미평가)" note instead."""
        from deepfake_lens.evidence_rules import EXIF_UNEVALUATED_DAMAGED_TITLE

        full = self._jpeg("full.jpg", quality=95, exif=self._camera_exif())
        self.assertIn("카메라 EXIF 일관", self._titles(full))  # control: intact file is consistent
        data = full.read_bytes()
        cut = self.root / "cut-body.jpg"
        cut.write_bytes(data[: len(data) // 2])
        item = analyze_file(cut)
        assert item.result is not None
        coverage = {c.check: c for c in item.result.coverage}
        self.assertEqual(coverage["image_class"].status, CoverageStatus.FAILED)
        titles = [e.title for e in item.result.evidence]
        self.assertNotIn("카메라 EXIF 일관", titles)
        self.assertFalse([e for e in item.result.evidence if e.direction == EvidenceDirection.AUTHENTIC], titles)
        note = next(e for e in item.result.evidence if e.title == EXIF_UNEVALUATED_DAMAGED_TITLE)
        self.assertEqual(note.title, "EXIF 존재(파일 손상으로 일관성 미평가)")
        self.assertEqual((note.kind, note.direction, note.strength), (EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK))
        self.assertIn("Canon EOS R5", note.detail)
        self.assertIn(coverage["image_class"].reason, note.detail)
        self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED)

    def test_undecoded_image_camera_exif_is_not_evaluated(self) -> None:
        """R9: when the decode check did not run (skipped), the EXIF rule is not applied either."""
        from deepfake_lens.checks import CheckSkipped
        from deepfake_lens.evidence_rules import EXIF_UNEVALUATED_UNDECODED_TITLE

        path = self._jpeg("camera.jpg", quality=95, exif=self._camera_exif())
        with mock.patch("deepfake_lens.core.classify_image", side_effect=CheckSkipped("의존성 부재: numpy")):
            titles = self._titles(path)
        self.assertNotIn("카메라 EXIF 일관", titles)
        self.assertIn(EXIF_UNEVALUATED_UNDECODED_TITLE, titles)

    def test_without_pillow_exif_is_a_note(self) -> None:
        path = self._jpeg("camera.jpg", exif=self._camera_exif())
        real_import = builtins.__import__

        def no_pil(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("No module named 'PIL'", name="PIL")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", no_pil):
            read = read_image_metadata_full(path)
        self.assertIsNone(read.error)
        self.assertFalse(read.exif_read)
        self.assertNotIn("exif.Make", read.metadata)
        self.assertTrue(any("의존성 부재: PIL" in note for note in read.notes), read.notes)


class GeneratorNameTest(unittest.TestCase):
    def test_every_listed_generator_name_matches(self) -> None:
        for name in GENERATOR_NAMES:
            with self.subTest(name=name):
                self.assertTrue(generator_name_in(f"made with {name} 2"))

    def test_word_boundaries(self) -> None:
        for value in ("Fluxus Studio", "imagenes", "Canon EOS", "geminid"):
            with self.subTest(value=value):
                self.assertIsNone(generator_name_in(value))
        self.assertTrue(generator_name_in("AI-generated image"))

    def test_xmp_rules_on_parsed_keys(self) -> None:
        items = xmp_evidence({"xmp.DigitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture"})
        self.assertEqual(items, [])
        items = xmp_evidence({"xmp.DigitalSourceType": TRAINED, "xmp.Credit": "Nano Banana"})
        self.assertEqual(len(items), 2)
        self.assertTrue(all(i.strength == EvidenceStrength.STRONG and i.direction == EvidenceDirection.SYNTHETIC for i in items))

    def test_camera_rule_unit(self) -> None:
        metadata = {
            "exif.Make": "NIKON CORPORATION", "exif.Model": "NIKON Z 6_2",
            "exif.Software": "Ver.1.40", "exif.DateTimeOriginal": "2025:03:02 08:00:00",
        }
        now = datetime(2026, 10, 9)
        ok = camera_exif_evidence(metadata, image_format="jpeg", jpeg_quality=96.0, now=now)
        self.assertEqual([i.title for i in ok], ["카메라 EXIF 일관"])
        png = camera_exif_evidence(metadata, image_format="png", jpeg_quality=None, now=now)
        self.assertEqual(png[0].direction, EvidenceDirection.NEUTRAL)
        future = camera_exif_evidence({**metadata, "exif.DateTimeOriginal": "2030:01:01 00:00:00"}, image_format="jpeg", jpeg_quality=96.0, now=now)
        self.assertIn("미래", future[0].detail)
        self.assertEqual(camera_exif_evidence({}, image_format="jpeg", jpeg_quality=96.0), [])


if __name__ == "__main__":
    unittest.main()

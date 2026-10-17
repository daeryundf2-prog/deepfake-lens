"""TB-5: screenshot facts — the OS "Screenshot" user comment and native screen sizes; never a verdict."""

from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from deepfake_lens.core import analyze_file
from deepfake_lens.result_types import CoverageStatus, EvidenceDirection, Verdict
from deepfake_lens.screen_capture import CAMERA_C2PA_LIMITATION, SCREEN_CAPTURE_CHECK, screen_capture_layers, xmp_user_comments

IOS_XMP = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="XMP Core 6.0.0"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="" xmlns:exif="http://ns.adobe.com/exif/1.0/"><exif:UserComment>Screenshot</exif:UserComment>'
    "</rdf:Description></rdf:RDF></x:xmpmeta>"
)


def png(width: int, height: int, xmp: str = "") -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x80\x80\x80" * width for _ in range(height))
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    if xmp:
        out += chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + xmp.encode())
    return out + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


class ScreenCaptureTest(unittest.TestCase):
    def test_xmp_user_comment(self) -> None:
        self.assertEqual(xmp_user_comments(IOS_XMP.encode()), ["Screenshot"])
        self.assertEqual(xmp_user_comments(b"<!DOCTYPE x>" + IOS_XMP.encode()), [])

    def test_ios_screenshot_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "IMG_0001.PNG"
            path.write_bytes(png(828, 1792, IOS_XMP))  # iPhone 11 / XR native size
            result = analyze_file(path).result
        assert result is not None
        [entry] = [c for c in result.coverage if c.check == SCREEN_CAPTURE_CHECK]
        self.assertEqual(entry.status, CoverageStatus.RAN)
        titles = [e.title for e in result.evidence]
        self.assertIn("화면 캡처 표시(운영체제가 쓴 주석)", titles)
        self.assertIn("기기 화면 해상도와 같은 크기", titles)
        self.assertTrue(all(e.direction == EvidenceDirection.NEUTRAL for e in result.evidence if e.layer == "screen_capture"))
        self.assertNotIn(result.verdict_code, (Verdict.MANIPULATION_EVIDENCE, Verdict.AUTHENTICITY_EVIDENCE))

    def test_landscape_matches_and_other_sizes_do_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(png(2, 2))
            self.assertEqual(screen_capture_layers(path, {}, (2868, 1320)).evidence[0].title, "기기 화면 해상도와 같은 크기")
            self.assertEqual(screen_capture_layers(path, {}, (4032, 3024)).evidence, [])

    def test_signed_camera_manifest_adds_the_recapture_limitation(self) -> None:
        validation = {"present": True, "trusted": True, "source_type_declarations": [
            {"where": "action", "value": "http://cv.iptc.org/newscodes/digitalsourcetype/computationalCapture"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(png(2, 2))
            self.assertEqual(screen_capture_layers(path, {}, (2, 2), validation).limitations, [CAMERA_C2PA_LIMITATION])
            self.assertEqual(screen_capture_layers(path, {}, (2, 2), {**validation, "trusted": False}).limitations, [])


if __name__ == "__main__":
    unittest.main()

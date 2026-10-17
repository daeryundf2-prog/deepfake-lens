"""TB-3: device AI-edit labels — the Samsung SEFT trailer is listed as a fact; Google AI credit labels are moderate self-declarations."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from deepfake_lens.core import analyze_file
from deepfake_lens.decision import decide
from deepfake_lens.device_labels import DEVICE_LABEL_CHECK, device_label_layers, read_seft
from deepfake_lens.result_types import CoverageStatus, EvidenceDirection, EvidenceStrength, Grade, Verdict


def seft_trailer(entries: list[tuple[int, str, bytes]]) -> bytes:
    """A trailer in ExifTool Samsung.pm's layout: data blocks, then the SEFH directory, its length and "SEFT"."""
    blocks = b""
    offsets = []
    for type_id, name, data in entries:
        block = struct.pack("<HHI", 0, type_id, len(name)) + name.encode() + data
        offsets.append((type_id, len(blocks), len(block)))
        blocks += block
    directory = b"SEFH" + struct.pack("<II", 101, len(entries))
    for type_id, start, size in offsets:
        noff = len(blocks) - start  # negative offset from the directory start
        directory += struct.pack("<HHII", 0, type_id, noff, size)
    return blocks + directory + struct.pack("<I", len(directory)) + b"SEFT"


def png(extra_xmp: str = "") -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes([200, 100, 50]) * 8 for _ in range(8))
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
    if extra_xmp:
        out += chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + extra_xmp.encode())
    return out + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


XMP_CREDIT = (
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/" photoshop:Credit="Edited with Google AI"/>'
    "</rdf:RDF></x:xmpmeta>"
)


class SeftTest(unittest.TestCase):
    def test_reads_entry_names_and_candidate_keys(self) -> None:
        trailer = seft_trailer([
            (0x0A01, "Image_UTC_Data", b"1700000000000"),
            (0x0BA1, "PhotoEditor_Re_Edit_Data", json.dumps({"isScaleAI": True, "sepVersion": 1}).encode()),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(png() + trailer)
            entries = read_seft(path, wanted=frozenset({"PhotoEditor_Re_Edit_Data"}))
            layers = device_label_layers(path, {})
        assert entries is not None
        self.assertEqual([e.name for e in entries], ["Image_UTC_Data", "PhotoEditor_Re_Edit_Data"])
        self.assertEqual(entries[1].json_keys["isScaleAI"], True)
        titles = [i.title for i in layers.evidence]
        self.assertEqual(titles, ["삼성 기기 편집·촬영 기록(SEFT 트레일러)", "삼성 편집 기록의 AI 관련 후보 필드(의미 미확인)"])
        self.assertTrue(all(i.direction == EvidenceDirection.NEUTRAL for i in layers.evidence))

    def test_no_trailer_and_a_broken_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(png())
            self.assertIsNone(read_seft(path))
            path.write_bytes(png() + b"\xff\xff\x00\x00SEFT")
            self.assertIsNone(read_seft(path))
            layers = device_label_layers(path, {})
        self.assertEqual(layers.coverage[0].status, CoverageStatus.RAN)
        self.assertEqual(layers.evidence, [])


class GoogleLabelTest(unittest.TestCase):
    def test_credit_label_is_a_moderate_self_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "edit.png"
            path.write_bytes(png(XMP_CREDIT))
            result = analyze_file(path).result
        assert result is not None
        [entry] = [c for c in result.coverage if c.check == DEVICE_LABEL_CHECK]
        self.assertEqual(entry.status, CoverageStatus.RAN)
        [item] = [e for e in result.evidence if e.title == "Google AI 편집 라벨(자기 신고)"]
        self.assertEqual((item.direction, item.strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE))
        # Alone it never concludes (rule 2 needs a strong item) and it blocks an authenticity conclusion.
        self.assertEqual(decide([item], [], Grade.EVIDENCE), Verdict.UNDETERMINED)
        self.assertNotEqual(result.verdict_code, Verdict.AUTHENTICITY_EVIDENCE)


if __name__ == "__main__":
    unittest.main()

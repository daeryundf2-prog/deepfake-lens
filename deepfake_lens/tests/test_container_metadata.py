"""P0R-22 / P0R-23: the container/bitstream metadata layer (threat G2) and the video source statement.

Every fixture is built here, byte by byte (MP4 box trees, H.264/HEVC SEI NAL
units, a WAV with LIST/INFO, an MP3 ID3v2 header, a PNG with an AIGC text
chunk); the ffmpeg-made files are used only when ffmpeg is on PATH.
"""

from __future__ import annotations

import json
import random
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path

from deepfake_lens import container_metadata as cm
from deepfake_lens.core import analyze_file
from deepfake_lens.decision import decide
from deepfake_lens.evidence_rules import (
    CONTAINER_AIGC_FACT_TITLE,
    CONTAINER_AIGC_TITLE,
    CONTAINER_BRAND_TITLE,
    CONTAINER_COMFYUI_FREE_TITLE,
    CONTAINER_COMFYUI_TITLE,
    CONTAINER_FACTS_TITLE,
    CONTAINER_SEI_TITLE,
    CONTAINER_SEI_VENDOR_TITLE,
    SELF_DECLARED_NOTE,
)
from deepfake_lens.result_types import (
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceStrength,
    Grade,
    Verdict,
)
from deepfake_lens.serialization import _evidence_item_from_json

AIGC_KLING = json.dumps({
    "Label": "1", "ContentProducer": "001191110108335469089C10100", "ProduceID": "SGP_PROD_ai_web_1",
    "ReservedCode1": "", "ContentPropagator": "", "PropagateID": "", "ReservedCode2": "",
})
COMFY_PROMPT = json.dumps({
    "3": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20, "model": ["4", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "wan2.1.safetensors"}},
})
X264 = "x264 - core 164 r3108 31e19f9 - H.264/MPEG-4 AVC codec - Copyleft 2003-2023 - options: cabac=1 ref=3 crf=23.0"


# --- Fixture builders -----------------------------------------------------------------


def box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def full(kind: bytes, payload: bytes) -> bytes:
    return box(kind, b"\x00\x00\x00\x00" + payload)


def data_box(text: str, type_code: int = 1) -> bytes:
    return box(b"data", struct.pack(">I", type_code) + b"\x00\x00\x00\x00" + text.encode("utf-8"))


def hdlr(handler: bytes, name: str) -> bytes:
    return full(b"hdlr", b"\x00" * 4 + handler + b"\x00" * 12 + name.encode() + b"\x00")


def mdta_meta(pairs: list[tuple[str, str]]) -> bytes:
    """QuickTime metadata (meta/hdlr mdta/keys/ilst) — what ffmpeg -movflags use_metadata_tags writes."""
    keys = full(b"keys", struct.pack(">I", len(pairs)) + b"".join(struct.pack(">I", 8 + len(k)) + b"mdta" + k.encode() for k, _ in pairs))
    ilst = box(b"ilst", b"".join(box(struct.pack(">I", i + 1), data_box(v)) for i, (_, v) in enumerate(pairs)))
    return box(b"meta", hdlr(b"mdta", "") + keys + ilst)


def itunes_meta(items: list[tuple[bytes, str]]) -> bytes:
    """ISO meta (a FullBox) with an iTunes ilst — what ffmpeg writes by default (©too=Lavf…)."""
    ilst = box(b"ilst", b"".join(box(k, data_box(v)) for k, v in items))
    return full(b"meta", hdlr(b"mdir", "") + ilst)


def sei_nal(text: str, *, codec: str = "H.264", uuid: bytes = b"\xdc\x45\xe9\xbd\xe6\xd9\x48\xb7\x96\x2c\xd8\x20\xd9\x23\xee\xef") -> bytes:
    body = uuid + text.encode() + b"\x00"
    size = len(body)
    rbsp = b"\x05" + b"\xff" * (size // 255) + bytes([size % 255]) + body + b"\x80"
    return (b"\x06" if codec == "H.264" else b"\x4e\x01") + rbsp


def visual_entry(codec: bytes, compressor: str) -> bytes:
    name = compressor.encode()[:31]
    fields = (
        b"\x00" * 6 + struct.pack(">H", 1) + b"\x00" * 16 + struct.pack(">HH", 320, 240) + struct.pack(">II", 0x480000, 0x480000)
        + b"\x00" * 4 + struct.pack(">H", 1) + bytes([len(name)]) + name.ljust(31, b"\x00") + struct.pack(">Hh", 0x18, -1)
    )
    config = box(b"avcC", bytes([1, 0x42, 0, 0x1E, 0xFF, 0xE0])) if codec == b"avc1" else box(b"hvcC", b"\x01" + b"\x00" * 20 + b"\x0f" + b"\x00")
    return box(codec, fields + config)


def mp4(*, udta: bytes = b"", sei: tuple[str, ...] = (), codec: bytes = b"avc1", compressor: str = "Lavc60.3.100 libx264",
        created: int = 3_800_000_000, extra_top: bytes = b"", mdat_prefix: bytes = b"") -> bytes:
    nal_codec = "H.264" if codec == b"avc1" else "HEVC"
    nals = mdat_prefix + b"".join(struct.pack(">I", len(n)) + n for n in [*(sei_nal(t, codec=nal_codec) for t in sei), b"\x65\x88\x84\x00\x10"])
    head = box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41")

    def moov(offset: int) -> bytes:
        stbl = box(b"stbl", full(b"stsd", struct.pack(">I", 1) + visual_entry(codec, compressor)) + full(b"stco", struct.pack(">II", 1, offset)))
        trak = box(b"trak", box(b"mdia", hdlr(b"vide", "VideoHandler") + box(b"minf", stbl)))
        mvhd = full(b"mvhd", struct.pack(">II", created, created) + b"\x00" * 92)
        return box(b"moov", mvhd + trak + (box(b"udta", udta) if udta else b""))

    first = moov(0)
    offset = len(head) + len(first) + len(extra_top) + 8
    return head + moov(offset) + extra_top + box(b"mdat", nals)


def write(folder: Path, name: str, data: bytes) -> Path:
    path = folder / name
    path.write_bytes(data)
    return path


def wav_with_info(info: list[tuple[bytes, str]]) -> bytes:
    fmt = box_le(b"fmt ", struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16))
    data = box_le(b"data", b"\x00\x01" * 800)
    entries = b"".join(box_le(k, v.encode() + b"\x00") for k, v in info)
    listing = box_le(b"LIST", b"INFO" + entries)
    body = b"WAVE" + fmt + data + listing
    return b"RIFF" + struct.pack("<I", len(body)) + body


def box_le(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) % 2 else b"")


def synchsafe(value: int) -> bytes:
    return bytes([(value >> 21) & 0x7F, (value >> 14) & 0x7F, (value >> 7) & 0x7F, value & 0x7F])


def id3_frame(frame_id: str, payload: bytes) -> bytes:
    return frame_id.encode() + struct.pack(">I", len(payload)) + b"\x00\x00" + payload


def mp3(frames: list[bytes]) -> bytes:
    body = b"".join(frames) + b"\x00" * 16
    return b"ID3\x03\x00\x00" + synchsafe(len(body)) + body + b"\xff\xfb\x90\x00" * 64


def png_with_text(chunks: list[tuple[bytes, bytes]]) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + b"\x80\x80\x80" * 2 + b"\x00" + b"\x80\x80\x80" * 2)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + b"".join(chunk(k, v) for k, v in chunks) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def xmp_packet(aigc: str) -> bytes:
    escaped = aigc.replace("&", "&amp;").replace('"', "&quot;")
    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f'<rdf:Description rdf:about="" xmlns:TC260="http://www.tc260.org.cn/ns/AIGC/1.0/" TC260:AIGC="{escaped}"/>'
        "</rdf:RDF></x:xmpmeta>"
    ).encode()


def layers(path: Path):
    return cm.media_layers(path)


def entry_of(result_or_layers) -> CoverageEntry:
    [entry] = [e for e in result_or_layers.coverage if e.check == cm.CHECK]
    return entry


class _Tmp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def assert_self_declared(self, items: list[EvidenceItem]) -> None:
        for item in items:
            self.assertNotEqual(item.direction, EvidenceDirection.AUTHENTIC, item.title)
            if item.direction == EvidenceDirection.SYNTHETIC:
                self.assertTrue(item.self_declared, item.title)
                self.assertIn(SELF_DECLARED_NOTE, item.detail)
                self.assertEqual(item.kind, EvidenceKind.DETERMINISTIC)


# --- Facts ------------------------------------------------------------------------------


class Mp4FactsTest(_Tmp):
    def test_udta_meta_hdlr_mvhd_stsd_and_sei_facts_are_read(self) -> None:
        udta = itunes_meta([(b"\xa9too", "Lavf60.3.100")]) + mdta_meta([("comment", "family trip"), ("com.apple.quicktime.make", "Apple")])
        path = write(self.dir, "a.mp4", mp4(udta=udta, sei=(X264,)))
        facts = cm.read_container_metadata(path)
        found = {(f.key, f.value) for f in facts.fields}
        self.assertIn(("©too", "Lavf60.3.100"), found)
        self.assertIn(("comment", "family trip"), found)
        self.assertIn(("com.apple.quicktime.make", "Apple"), found)
        self.assertIn(("hdlr(vide)", "VideoHandler"), found)
        self.assertIn(("compressorname", "Lavc60.3.100 libx264"), found)
        self.assertIn(("creation_time", "2024-05-31 11:33:20 UTC"), found)  # 3 800 000 000 s after 1904-01-01
        [sei] = facts.sei
        self.assertEqual((sei.codec, sei.text), ("H.264", X264))
        reason = facts.coverage_reason()
        self.assertIn("MP4/MOV 메타데이터 판독(udta·meta·keys·ilst·hdlr·mvhd·stsd·uuid·XMP)", reason)
        self.assertIn("SEI 사용자 데이터(H.264, 첫 4 MiB)", reason)

    def test_quicktime_udta_text_atoms_and_c2pa_uuid_presence(self) -> None:
        text_atom = box(b"\xa9swr", struct.pack(">HH", 12, 0) + b"Lavf61.1.100")
        c2pa = box(b"uuid", cm.C2PA_UUID + b"\x00" * 32)
        facts = cm.read_container_metadata(write(self.dir, "a.mov", mp4(udta=text_atom, extra_top=c2pa)))
        self.assertIn(("©swr", "Lavf61.1.100"), {(f.key, f.value) for f in facts.fields})
        self.assertTrue(facts.c2pa_uuid)

    def test_hevc_sei_and_annex_b_nal_units(self) -> None:
        facts = cm.read_container_metadata(write(self.dir, "h.mp4", mp4(codec=b"hvc1", sei=("x265 (build 199) - 3.5",))))
        self.assertEqual([(s.codec, s.text) for s in facts.sei], [("HEVC", "x265 (build 199) - 3.5")])
        annex = cm.ContainerFacts("test")
        cm.scan_nal_units(b"\x00\x00\x00\x01" + sei_nal("kling-ai v2") + b"\x00\x00\x01\x65\x88\x84", "H.264", annex)
        self.assertEqual([s.text for s in annex.sei], ["kling-ai v2"])

    def test_sei_emulation_prevention_bytes_are_removed(self) -> None:
        nal = sei_nal("ab")
        escaped = nal.replace(b"\x05", b"\x05", 1)
        facts = cm.ContainerFacts("test")
        cm._sei_from_nal(escaped[:1] + cm._unescape_rbsp(b"\x00\x00\x03\x01")[:0] + escaped[1:], "H.264", facts)
        self.assertEqual(cm._unescape_rbsp(b"\x00\x00\x03\x01\x00\x00\x03"), b"\x00\x00\x01\x00\x00")
        self.assertEqual([s.text for s in facts.sei], ["ab"])

    def test_sei_scan_is_bounded_to_the_first_window(self) -> None:
        big = struct.pack(">I", cm.SEI_SCAN_BYTES + 1024) + b"\x65" + b"\x00" * (cm.SEI_SCAN_BYTES + 1023)
        facts = cm.read_container_metadata(write(self.dir, "late.mp4", mp4(sei=("kling-ai",), mdat_prefix=big)))
        self.assertEqual(facts.sei, [])  # the SEI after the 4 MiB window is not read
        self.assertIn("첫 4 MiB", facts.coverage_reason())

    def test_audio_only_mp4_says_no_sei_was_checked(self) -> None:
        head = box(b"ftyp", b"M4A \x00\x00\x02\x00M4A mp42isom")
        moov = box(b"moov", box(b"trak", box(b"mdia", hdlr(b"soun", "SoundHandler"))) + box(b"udta", itunes_meta([(b"\xa9nam", "song")])))
        facts = cm.read_container_metadata(write(self.dir, "a.m4a", head + moov + box(b"mdat", b"\x00" * 64)))
        self.assertIn("SEI 미확인(H.264/HEVC 트랙 없음)", facts.coverage_reason())

    def test_value_over_the_cap_is_a_partial_note(self) -> None:
        huge = "x" * (cm.MAX_VALUE_BYTES + 1)
        facts = cm.read_container_metadata(write(self.dir, "big.mp4", mp4(udta=mdta_meta([("comment", huge)]))))
        self.assertIn("일부만 판독", facts.coverage_reason())
        self.assertIn("값 상한(8 MiB)", facts.coverage_reason())


class AudioFactsTest(_Tmp):
    def test_wav_list_info(self) -> None:
        facts = cm.read_container_metadata(write(self.dir, "a.wav", wav_with_info([(b"ISFT", "Lavf60.3.100"), (b"ICMT", "memo")])))
        self.assertEqual({(f.key, f.value, f.kind) for f in facts.fields}, {("ISFT", "Lavf60.3.100", cm.KIND_TOOL), ("ICMT", "memo", cm.KIND_FREE)})
        self.assertEqual(facts.coverage_reason(), "RIFF(WAV) 판독(LIST/INFO) — 필드 2개")

    def test_mp3_id3v2_frames(self) -> None:
        frames = [
            id3_frame("TSSE", b"\x00Lavf60.3.100"),
            id3_frame("TIT2", b"\x03" + "제목".encode()),
            id3_frame("TXXX", b"\x00AIGC\x00" + AIGC_KLING.encode()),
            id3_frame("COMM", b"\x00eng\x00comment text"),
        ]
        facts = cm.read_container_metadata(write(self.dir, "a.mp3", mp3(frames)))
        found = {(f.key, f.value) for f in facts.fields}
        self.assertIn(("TSSE", "Lavf60.3.100"), found)
        self.assertIn(("TIT2", "제목"), found)
        self.assertIn(("TXXX:AIGC", AIGC_KLING), found)
        self.assertIn(("COMM", "comment text"), found)
        self.assertIn("ID3v2.3", facts.coverage_reason())

    def test_mp3_without_tag_ran_and_says_so(self) -> None:
        facts = cm.read_container_metadata(write(self.dir, "a.mp3", b"\xff\xfb\x90\x00" * 64))
        self.assertEqual(facts.coverage_reason(), "MPEG 오디오(ID3v2 태그 확인) 판독(태그 없음) — 필드 0개")

    def test_flac_vorbis_comments(self) -> None:
        vendor = b"reference libFLAC 1.4.3"
        comments = [b"ENCODER=Suno v4", b"TITLE=t"]
        block = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments)) + b"".join(struct.pack("<I", len(c)) + c for c in comments)
        streaminfo = b"\x00" + (34).to_bytes(3, "big") + b"\x00" * 34
        data = b"fLaC" + streaminfo + bytes([0x84]) + len(block).to_bytes(3, "big") + block
        facts = cm.read_container_metadata(write(self.dir, "a.flac", data))
        self.assertIn(("ENCODER", "Suno v4", cm.KIND_TOOL), {(f.key, f.value, f.kind) for f in facts.fields})


# --- Evidence ------------------------------------------------------------------------------


class EvidenceTest(_Tmp):
    def _items(self, data: bytes, name: str = "a.mp4") -> list[EvidenceItem]:
        result = layers(write(self.dir, name, data))
        self.assertEqual(entry_of(result).status, CoverageStatus.RAN, entry_of(result).reason)
        self.assert_self_declared(result.evidence)
        return result.evidence

    def test_aigc_label_is_strong_self_declared_with_the_producer_attributed(self) -> None:
        items = self._items(mp4(udta=mdta_meta([("AIGC", AIGC_KLING)])))
        [aigc] = [i for i in items if i.title == CONTAINER_AIGC_TITLE]
        self.assertEqual((aigc.kind, aigc.direction, aigc.strength), (EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG))
        self.assertTrue(aigc.self_declared)
        self.assertIn("Kling AI(쿠아이쇼우 Kuaishou)", aigc.detail)
        self.assertIn("컨테이너 생성 표식 등록표 2026-10-17.1", aigc.detail)

    def test_unknown_producer_is_a_fact_without_attribution(self) -> None:
        label = json.dumps({"Label": "1", "ContentProducer": "0011ABCDEF", "ProduceID": "p"})
        [aigc] = [i for i in self._items(mp4(udta=mdta_meta([("AIGC", label)]))) if i.title == CONTAINER_AIGC_TITLE]
        self.assertIn("ContentProducer 「0011ABCDEF」는 등록표에 없어 서비스를 귀속하지 않습니다", aigc.detail)
        self.assertNotIn("Kling", aigc.detail)

    def test_other_label_values_are_facts_only(self) -> None:
        label = json.dumps({"Label": "2", "ContentProducer": "kling"})
        items = self._items(mp4(udta=mdta_meta([("AIGC", label)])))
        self.assertIn(CONTAINER_AIGC_FACT_TITLE, [i.title for i in items])
        self.assertFalse(any(i.direction == EvidenceDirection.SYNTHETIC for i in items))

    def test_comfyui_prompt_is_strong_but_not_in_a_comment(self) -> None:
        items = self._items(mp4(udta=mdta_meta([("prompt", COMFY_PROMPT), ("workflow", COMFY_PROMPT)])))
        comfy = [i for i in items if i.title == CONTAINER_COMFYUI_TITLE]
        self.assertEqual(len(comfy), 1)
        self.assertEqual(comfy[0].strength, EvidenceStrength.STRONG)
        free = self._items(mp4(udta=mdta_meta([("comment", json.dumps({"prompt": json.loads(COMFY_PROMPT), "workflow": {}}))])), "c.mp4")
        self.assertIn(CONTAINER_COMFYUI_FREE_TITLE, [i.title for i in free])
        self.assertFalse(any(i.direction == EvidenceDirection.SYNTHETIC for i in free))

    def test_a_prompt_field_that_is_not_a_graph_is_not_evidence(self) -> None:
        items = self._items(mp4(udta=mdta_meta([("prompt", "a cat on a sofa, KSampler mentioned in prose")])))
        self.assertFalse(any(i.direction == EvidenceDirection.SYNTHETIC for i in items))

    def test_registered_vendor_sei_is_strong(self) -> None:
        items = self._items(mp4(sei=("kling-ai 2.1 build",)))
        [vendor] = [i for i in items if i.title == CONTAINER_SEI_VENDOR_TITLE]
        self.assertEqual(vendor.strength, EvidenceStrength.STRONG)

    def test_brand_in_an_encoder_field_is_moderate_and_never_strong(self) -> None:
        items = self._items(mp4(udta=mdta_meta([("encoder", "Kling 2.1 exporter")])))
        [brand] = [i for i in items if i.title == CONTAINER_BRAND_TITLE]
        self.assertEqual((brand.direction, brand.strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE))
        self.assertTrue(brand.self_declared)

    def test_brand_in_a_free_text_field_is_nothing(self) -> None:
        items = self._items(mp4(udta=mdta_meta([("comment", "Made with Sora and Kling"), ("title", "Midjourney"), ("artist", "Veo")])))
        self.assertFalse(any(i.direction == EvidenceDirection.SYNTHETIC for i in items))

    def test_lavf_and_x264_are_neutral_facts_only(self) -> None:
        items = self._items(mp4(udta=itunes_meta([(b"\xa9too", "Lavf60.3.100")]), sei=(X264,)))
        self.assertEqual(sorted(i.title for i in items), sorted([CONTAINER_FACTS_TITLE, CONTAINER_SEI_TITLE]))
        for item in items:
            self.assertEqual((item.kind, item.direction, item.strength), (EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK))
            self.assertFalse(item.self_declared)
        self.assertIn("ffmpeg", items[0].detail)
        # A video with only these facts stays undetermined (rule 6) even when every check ran.
        coverage = [CoverageEntry("video_analysis", CoverageStatus.RAN), CoverageEntry(cm.CHECK, CoverageStatus.RAN, "x")]
        self.assertEqual(decide(items, coverage, Grade.EVIDENCE), Verdict.UNDETERMINED)

    def test_mp3_aigc_in_txxx(self) -> None:
        items = self._items(mp3([id3_frame("TXXX", b"\x00AIGC\x00" + AIGC_KLING.encode())]), "a.mp3")
        self.assertIn(CONTAINER_AIGC_TITLE, [i.title for i in items])

    def test_wav_brand_in_isft_is_moderate(self) -> None:
        items = self._items(wav_with_info([(b"ISFT", "ElevenLabs")]), "a.wav")
        self.assertIn(CONTAINER_BRAND_TITLE, [i.title for i in items])


# --- Malformed / unsupported ---------------------------------------------------------------


class MalformedTest(_Tmp):
    def assert_failed(self, data: bytes, fragment: str, name: str = "bad.mp4") -> None:
        result = layers(write(self.dir, name, data))
        entry = entry_of(result)
        self.assertEqual(entry.status, CoverageStatus.FAILED, entry.reason)
        self.assertTrue(entry.reason.startswith("AnalyzerError: "), entry.reason)
        self.assertIn(fragment, entry.reason)
        self.assertEqual(result.findings[-1].outcome, "failed")

    def test_box_size_smaller_than_its_header(self) -> None:
        self.assert_failed(box(b"ftyp", b"isom") + struct.pack(">I", 4) + b"moov" + b"\x00" * 8, "헤더(8바이트)보다 작습니다")

    def test_truncated_file(self) -> None:
        self.assert_failed(mp4()[:-10], "잘렸습니다")

    def test_nested_size_zero(self) -> None:
        inner = struct.pack(">I", 0) + b"udta" + b"\x00" * 8
        self.assert_failed(box(b"ftyp", b"isom") + box(b"moov", inner), "크기가 0입니다")

    def test_top_level_size_zero_runs_to_the_end_of_the_file(self) -> None:
        # ISO/IEC 14496-12 §4.2: a top-level box of size 0 extends to the end of the file (the last mdat).
        data = mp4()
        mdat = data.rfind(b"mdat") - 4
        result = layers(write(self.dir, "zero.mp4", data[:mdat] + b"\x00\x00\x00\x00" + data[mdat + 4 :]))
        self.assertEqual(entry_of(result).status, CoverageStatus.RAN)

    def test_64_bit_size_beyond_the_file(self) -> None:
        self.assert_failed(box(b"ftyp", b"isom") + struct.pack(">I", 1) + b"moov" + struct.pack(">Q", 2**64 - 1), "파일 끝")

    def test_64_bit_size_smaller_than_its_header(self) -> None:
        self.assert_failed(box(b"ftyp", b"isom") + struct.pack(">I", 1) + b"moov" + struct.pack(">Q", 8), "헤더(16바이트)보다 작습니다")

    def test_deep_nesting(self) -> None:
        inner = b""
        for _ in range(cm.MAX_DEPTH + 5):
            inner = box(b"udta", inner)
        self.assert_failed(box(b"ftyp", b"isom") + box(b"moov", inner), f"{cm.MAX_DEPTH}단계를 넘습니다")

    def test_child_past_its_parent(self) -> None:
        child = struct.pack(">I", 400) + b"udta" + b"\x00" * 8
        self.assert_failed(box(b"ftyp", b"isom") + box(b"moov", child) + box(b"free", b"\x00" * 500), "상위 박스 경계")

    def test_empty_file(self) -> None:
        self.assert_failed(b"", "빈 파일")

    def test_id3_frame_past_its_tag(self) -> None:
        body = b"TSSE" + struct.pack(">I", 500) + b"\x00\x00" + b"\x00Lavf"
        data = b"ID3\x03\x00\x00" + synchsafe(len(body)) + body + b"\xff\xfb" * 300
        self.assert_failed(data, "태그 경계를 넘습니다", "bad.mp3")

    def test_id3_tag_past_the_file(self) -> None:
        self.assert_failed(b"ID3\x03\x00\x00" + synchsafe(10_000) + b"TSSE", "파일 끝을 넘습니다", "bad.mp3")

    def test_riff_chunk_past_the_file(self) -> None:
        data = wav_with_info([(b"ISFT", "x")])
        self.assert_failed(data[:-6], "RIFF 청크", "bad.wav")

    def test_no_exception_escapes_truncations_and_bit_flips(self) -> None:
        good = mp4(udta=itunes_meta([(b"\xa9too", "Lavf")]) + mdta_meta([("AIGC", AIGC_KLING)]), sei=(X264,))
        rng = random.Random(22)
        variants = [good[:cut] for cut in range(0, len(good), 7)]
        for _ in range(150):
            mutated = bytearray(good)
            for _ in range(3):
                mutated[rng.randrange(len(mutated))] = rng.randrange(256)
            variants.append(bytes(mutated))
        for index, data in enumerate(variants):
            result = layers(write(self.dir, f"v{index}.mp4", data))
            self.assertIn(entry_of(result).status, (CoverageStatus.RAN, CoverageStatus.FAILED, CoverageStatus.SKIPPED))
            if entry_of(result).status == CoverageStatus.FAILED:
                self.assertTrue(entry_of(result).reason.startswith("AnalyzerError: "), entry_of(result).reason)
            self.assert_self_declared(result.evidence)


class UnsupportedTest(_Tmp):
    def test_formats_it_does_not_parse_are_skipped_with_a_reason(self) -> None:
        cases = {
            "a.mkv": (b"\x1a\x45\xdf\xa3" + b"\x00" * 32, "Matroska/WebM"),
            "a.ogg": (b"OggS" + b"\x00" * 32, "Ogg"),
            "a.webm": (b"\x1a\x45\xdf\xa3" + b"\x00" * 32, "Matroska/WebM"),
            "a.wma": (b"\x30\x26\xb2\x75" + b"\x00" * 32, "ASF"),
            "a.mp4": (b"not a container at all, just text", "형식 식별 불가"),
        }
        for name, (data, fragment) in cases.items():
            result = layers(write(self.dir, name, data))
            entry = entry_of(result)
            self.assertEqual(entry.status, CoverageStatus.SKIPPED, name)
            self.assertIn(fragment, entry.reason)
            self.assertEqual(result.findings[-1].outcome, "skipped")


# --- Scan wiring ---------------------------------------------------------------------------


class ScanWiringTest(_Tmp):
    def test_video_row_with_an_aigc_label_concludes_and_round_trips(self) -> None:
        item = analyze_file(write(self.dir, "kling.mp4", mp4(udta=mdta_meta([("AIGC", AIGC_KLING)]))))
        result = item.result
        assert result is not None
        self.assertEqual(entry_of(result).status, CoverageStatus.RAN)
        self.assertEqual(result.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        payload = json.loads(json.dumps(result.to_json(), ensure_ascii=False))
        [aigc] = [e for e in payload["evidence"] if e["title"] == CONTAINER_AIGC_TITLE]
        self.assertIs(aigc["self_declared"], True)
        self.assertTrue(_evidence_item_from_json(aigc).self_declared)
        self.assertTrue(all("self_declared" not in e for e in payload["evidence"] if e["title"] != CONTAINER_AIGC_TITLE))
        assert result.marking_context is not None
        [finding] = [f for f in result.marking_context["checked"] if f["check"] == cm.CHECK]
        self.assertEqual(finding["outcome"], "found")

    def test_audio_row_has_the_check(self) -> None:
        result = analyze_file(write(self.dir, "a.wav", wav_with_info([(b"ISFT", "Lavf60.3.100")]))).result
        assert result is not None
        self.assertEqual(entry_of(result).reason, "RIFF(WAV) 판독(LIST/INFO) — 필드 1개")
        assert result.marking_context is not None
        self.assertEqual(result.marking_context["checked"][-1]["check"], cm.CHECK)
        self.assertEqual(result.marking_context["checked"][-1]["outcome"], "not_found")

    def test_png_aigc_text_chunk_is_strong_self_declared(self) -> None:
        result = analyze_file(write(self.dir, "a.png", png_with_text([(b"tEXt", b"AIGC\x00" + AIGC_KLING.encode())]))).result
        assert result is not None
        entry = entry_of(result)
        self.assertEqual(entry.status, CoverageStatus.RAN)
        self.assertEqual(entry.reason, "AIGC 암묵 라벨 판독(PNG 텍스트 청크(tEXt·zTXt·iTXt)·XMP) — AIGC 필드 1개")
        [aigc] = [e for e in result.evidence if e.title == CONTAINER_AIGC_TITLE]
        self.assertTrue(aigc.self_declared)
        self.assertIn("PNG 텍스트 청크 AIGC", aigc.detail)
        self.assertIn(SELF_DECLARED_NOTE, aigc.detail)
        self.assertEqual(result.verdict_code, Verdict.MANIPULATION_EVIDENCE)

    def test_png_xmp_aigc_property(self) -> None:
        xmp = b"XML:com.adobe.xmp\x00\x00\x00\x00\x00" + xmp_packet(AIGC_KLING)
        result = analyze_file(write(self.dir, "x.png", png_with_text([(b"iTXt", xmp)]))).result
        assert result is not None
        [aigc] = [e for e in result.evidence if e.title == CONTAINER_AIGC_TITLE]
        self.assertIn("XMP AIGC", aigc.detail)

    def test_image_without_a_label_ran_and_bmp_is_skipped(self) -> None:
        result = analyze_file(write(self.dir, "plain.png", png_with_text([]))).result
        assert result is not None
        self.assertEqual(entry_of(result).reason, "AIGC 암묵 라벨 판독(PNG 텍스트 청크(tEXt·zTXt·iTXt)·XMP) — AIGC 필드 없음")
        bmp = b"BM" + struct.pack("<IHHI", 70, 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, 2, 2, 1, 24, 0, 16, 0, 0, 0, 0) + b"\x00" * 16
        result = analyze_file(write(self.dir, "a.bmp", bmp)).result
        assert result is not None
        self.assertEqual(entry_of(result).status, CoverageStatus.SKIPPED)
        self.assertIn("BMP", entry_of(result).reason)

    def test_damaged_png_fails_the_check(self) -> None:
        data = png_with_text([(b"tEXt", b"AIGC\x00" + AIGC_KLING.encode())])
        result = analyze_file(write(self.dir, "cut.png", data[:-12])).result  # IEND cut off
        assert result is not None
        self.assertEqual(entry_of(result).status, CoverageStatus.FAILED)
        self.assertTrue(entry_of(result).reason.startswith("AnalyzerError: 이미지 메타데이터를 끝까지 읽지 못해"))

    def test_malformed_video_row_is_failed_not_raised(self) -> None:
        item = analyze_file(write(self.dir, "bad.mp4", box(b"ftyp", b"isom") + struct.pack(">I", 1) + b"moov" + struct.pack(">Q", 2**64 - 1)))
        assert item.result is not None
        self.assertEqual(entry_of(item.result).status, CoverageStatus.FAILED)
        self.assertEqual(item.result.verdict_code, Verdict.UNDETERMINED)

    def test_registry_is_in_the_scan_cache_key(self) -> None:
        tokens = cm.registry_cache_tokens()
        self.assertEqual(len(tokens), 1)
        self.assertTrue(tokens[0].startswith("container-registry:2026-10-17.1:"))


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg 없음")
class FfmpegMadeFileTest(_Tmp):
    def test_ffmpeg_x264_mp4_has_only_neutral_facts_and_stays_undetermined(self) -> None:
        path = self.dir / "edit.mp4"
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=5:duration=1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
            check=True, timeout=120,
        )
        result = analyze_file(path).result
        assert result is not None
        self.assertEqual(entry_of(result).status, CoverageStatus.RAN)
        items = [e for e in result.evidence if e.layer == cm.LAYER]
        self.assertEqual(sorted(i.title for i in items), sorted([CONTAINER_FACTS_TITLE, CONTAINER_SEI_TITLE]))
        self.assertTrue(any("x264" in i.detail for i in items))
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)


class SelfDeclaredFieldTest(unittest.TestCase):
    def _item(self, **extra: object) -> EvidenceItem:
        return EvidenceItem(
            "t", f"d {SELF_DECLARED_NOTE}", EvidenceKind.DETERMINISTIC, EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG,
            "container_metadata", **extra,  # type: ignore[arg-type]
        )

    def test_the_field_is_written_only_when_true(self) -> None:
        self.assertNotIn("self_declared", self._item().to_json())
        self.assertIs(self._item(self_declared=True).to_json()["self_declared"], True)

    def test_round_trip_and_old_records_load(self) -> None:
        marked = self._item(self_declared=True)
        self.assertTrue(_evidence_item_from_json(json.loads(json.dumps(marked.to_json()))).self_declared)
        old = self._item().to_json()  # a record written before the field existed has no key at all
        self.assertFalse(_evidence_item_from_json(old).self_declared)
        # Only a JSON true marks an item (a truthy string does not).
        self.assertFalse(_evidence_item_from_json({**old, "self_declared": "yes"}).self_declared)

    def test_note_text_is_fixed(self) -> None:
        self.assertEqual(SELF_DECLARED_NOTE, "자기 선언 메타데이터 — 심거나 지울 수 있음")


if __name__ == "__main__":
    unittest.main()


class VideoStatesNoCheckItDidNotRunTest(unittest.TestCase):
    """P0R-23: a video row said 「영상 파일의 컨테이너 메타데이터에서 출처 단서를 찾지 못했습니다」 although no container
    metadata check existed. No result may carry that sentence; the row's source guess points to the checks that ran."""

    OLD = "영상 파일의 컨테이너 메타데이터에서 출처 단서를 찾지 못했습니다"

    def test_the_false_sentence_is_gone_from_the_package(self) -> None:
        package = Path(__file__).resolve().parents[1]
        offenders = [path.name for path in package.glob("*.py") if self.OLD in path.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [])

    def test_the_video_source_guess_names_no_unrun_check(self) -> None:
        from deepfake_lens import core

        self.assertNotIn("찾지 못했습니다", core.VIDEO_SOURCE_NOT_GUESSED)
        self.assertIn("컨테이너 메타데이터", core.VIDEO_SOURCE_NOT_GUESSED)

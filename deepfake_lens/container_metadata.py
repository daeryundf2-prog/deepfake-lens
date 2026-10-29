"""Container and bitstream metadata facts (P0R-22, threat G2).

A pure-Python reader — no ffprobe — of the metadata a media container
carries about the tool that wrote it:

- ISO-BMFF (mp4/mov/m4v/m4a/3gp): the box tree ``moov/udta/meta/keys/ilst``
  (QuickTime ``mdta`` keys such as ``AIGC``, ``prompt``, ``workflow``,
  ``comment``, ``com.apple.quicktime.*``; iTunes-style ``©too``/``©swr``/
  ``©enc`` items), QuickTime ``udta`` text atoms, ``hdlr`` handler names,
  the ``mvhd`` creation time, the visual sample entry's compressor name,
  an XMP ``uuid``/``XMP_`` packet and the presence of a C2PA ``uuid`` box
  (a fact only — validation is the ``c2pa`` check's job);
- the H.264/HEVC SEI ``user_data_unregistered`` payload strings (the x264
  option string, vendor strings such as ``kling-ai``) of the NAL units at
  the start of the first video chunk;
- audio: ID3v2 frames (TXXX, TSSE, TENC, COMM, TIT2 …), RIFF ``LIST/INFO``
  (ISFT, ICMT …) and an ``id3`` chunk of WAV/AVI, FLAC Vorbis comments.

It extracts **facts** only. What a fact means — almost always nothing
(``Lavf``/``x264`` are in every ffmpeg edit) — is decided in
:mod:`deepfake_lens.evidence_rules` (``container_metadata_evidence``); a
synthetic item exists only for a registered generator declaration
(``models/container-generators.json``) and is always self-declared.

Bounded reads: box payloads read for metadata total at most
:data:`MAX_PAYLOAD_BYTES` (64 MiB), one value at most
:data:`MAX_VALUE_BYTES`, the SEI scan reads at most :data:`SEI_SCAN_BYTES`
(the first 4 MiB of the first video chunk); the box walk visits at most
:data:`MAX_BOXES` boxes and :data:`MAX_DEPTH` levels. A cap reached is a
``partial`` note in the coverage reason, never silence. A structure that
cannot be walked (a box size under its header, a nested size-0 box, a box
past its parent or the end of the file, 64-bit sizes beyond the file,
nesting past the depth cap, an ID3 frame past its tag) raises
``AnalyzerError`` with a Korean reason — the check is ``failed``.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

from .check_skipped import CheckSkipped
from .checks import AnalyzerError

CHECK = "container_metadata"
LAYER = "container_metadata"

REGISTRY_PATH = Path(__file__).resolve().parent / "models" / "container-generators.json"
REGISTRY_SCHEMA = "container-generator-registry-v1"

MIB = 1024 * 1024
# Total box/frame payload bytes read for metadata in one file.
MAX_PAYLOAD_BYTES = 64 * MIB
# One metadata value (a ComfyUI workflow JSON is usually well under 1 MiB).
MAX_VALUE_BYTES = 8 * MIB
# Bytes of the first video chunk scanned for SEI NAL units.
SEI_SCAN_BYTES = 4 * MIB
MAX_BOXES = 100_000
MAX_DEPTH = 16
MAX_FIELDS = 512
MAX_SEI_STRINGS = 32
MAX_SEI_TEXT_CHARS = 2048
# An ID3v2 tag larger than this is read only up to it (APIC images make tags large).
MAX_ID3_BYTES = 16 * MIB
# An XMP packet in an MP4 uuid/XMP_ box (image_metadata.MAX_XMP_BYTES).
MAX_XMP_BYTES = 1 * MIB

# ISO/IEC 14496-12 box types a top-level box of an ISO-BMFF / QuickTime file may have.
BMFF_TOP_TYPES = frozenset({
    b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pnot", b"uuid", b"styp", b"sidx", b"moof", b"meta", b"junk", b"pdin",
})
# Boxes descended into (their payload is a list of boxes).
BMFF_CONTAINERS = frozenset({b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"edts", b"moof", b"traf"})
# C2PA 2.x §11.3 (BMFF): the manifest store's uuid box; XMP's uuid box (XMP Spec Part 3 §1.1.4).
C2PA_UUID = bytes.fromhex("d8fec3d61b0e483c92975828877ec481")
XMP_UUID = bytes.fromhex("be7acfcb97a942e89c71999491e3afac")
# What the ISO-BMFF walk reads (the coverage reason lists it).
BMFF_PARTS = ("udta", "meta·keys·ilst", "hdlr", "mvhd", "stsd", "uuid·XMP")
VISUAL_SAMPLE_ENTRIES = {b"avc1": "H.264", b"avc3": "H.264", b"hvc1": "HEVC", b"hev1": "HEVC"}
# 1904-01-01 UTC: the epoch of mvhd times.
MP4_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)

KIND_TOOL = "tool"  # names the writing tool (encoder/software fields)
KIND_FREE = "free"  # free text (comment/title/artist …) — never evidence
KIND_HANDLER = "handler"
KIND_TIME = "time"

# Keys (lower case, after the "TXXX:" prefix) whose value names the writing tool.
TOOL_KEYS = frozenset({
    "©too", "©swr", "©enc", "encoder", "software", "com.apple.quicktime.software", "tsse", "tenc", "isft",
    "encoded_by", "encoded-by", "encodedby", "vendor", "compressorname", "xmp.creatortool",
})


@dataclass
class Field:
    source: str  # where it was read, e.g. "MP4 mdta" / "ID3v2.4" / "RIFF INFO"
    key: str
    value: str
    kind: str = KIND_FREE

    @property
    def name(self) -> str:
        """The key without its "TXXX:" / "xmp." prefix, lower case (structured-field matching)."""
        lowered = self.key.lower()
        for prefix in ("txxx:", "xmp."):
            if lowered.startswith(prefix):
                return lowered[len(prefix):]
        return lowered


@dataclass
class SeiString:
    codec: str
    uuid: str
    text: str


# ISO/IEC 14496-10 Annex A profile_idc / ISO/IEC 23008-2 Annex A general_profile_idc.
AVC_PROFILES = {66: "Baseline", 77: "Main", 88: "Extended", 100: "High", 110: "High 10", 122: "High 4:2:2", 244: "High 4:4:4 Predictive"}
HEVC_PROFILES = {1: "Main", 2: "Main 10", 3: "Main Still Picture", 4: "Range Extensions"}


@dataclass(frozen=True)
class VideoTrack:
    """The first H.264/HEVC track's visual sample entry: coded size and the decoder configuration's profile/level."""

    codec: str
    width: int
    height: int
    profile_idc: int
    level_idc: int

    @property
    def profile(self) -> str | None:
        names = AVC_PROFILES if self.codec == "H.264" else HEVC_PROFILES
        return names.get(self.profile_idc)

    @property
    def level(self) -> str:
        if self.codec == "HEVC":
            return f"{self.level_idc / 30:g}"
        return f"{self.level_idc / 10:g}"


@dataclass
class ContainerFacts:
    format_label: str
    parts: list[str] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    sei: list[SeiString] = field(default_factory=list)
    sei_note: str = ""
    c2pa_uuid: bool = False
    partial: list[str] = field(default_factory=list)
    video: VideoTrack | None = None

    def coverage_reason(self) -> str:
        text = f"{self.format_label} 판독({'·'.join(self.parts)})" if self.parts else f"{self.format_label} 판독"
        if self.sei_note:
            text += f" · {self.sei_note}"
        text += f" — 필드 {len(self.fields)}개"
        if self.sei:
            text += f", SEI 문자열 {len(self.sei)}개"
        if self.partial:
            text += " — 일부만 판독: " + "; ".join(dict.fromkeys(self.partial))
        return text


def _size_text(count: int) -> str:
    return f"{count / MIB:g} MiB" if count >= MIB else f"{count}바이트"


def _box_name(kind: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else ("©" if b == 0xA9 else f"\\x{b:02x}") for b in kind)


def _clean(text: str) -> str:
    return "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32).replace("\x7f", "").strip()


def _decode_text(data: bytes) -> str:
    """UTF-8 (else Latin-1) text up to its first NUL, control characters removed."""
    data = data.split(b"\x00", 1)[0]
    try:
        return _clean(data.decode("utf-8"))
    except UnicodeDecodeError:
        return _clean(data.decode("latin-1"))


# --- Registry --------------------------------------------------------------------


class RegistryError(RuntimeError):
    """The container generator registry cannot be read (Korean)."""


@dataclass(frozen=True)
class Registry:
    version: str
    sha256: str
    payload: dict[str, Any]

    @property
    def label(self) -> str:
        return f"컨테이너 생성 표식 등록표 {self.version}(sha256 {self.sha256[:12]}…)"

    @property
    def generated_labels(self) -> tuple[str, ...]:
        return tuple(str(v) for v in self.payload.get("aigc_generated_labels") or ())

    def producer(self, value: str) -> dict[str, Any] | None:
        lowered = value.strip().lower()
        for entry in self.payload.get("aigc_producers") or ():
            if not isinstance(entry, dict):
                continue
            if any(lowered == str(name).lower() for name in entry.get("names") or ()):
                return entry
            if any(str(code).lower() in lowered for code in entry.get("codes_containing") or () if str(code)):
                return entry
        return None

    def sei_vendor(self, text: str) -> dict[str, Any] | None:
        lowered = text.lower()
        for entry in self.payload.get("sei_vendor_strings") or ():
            if isinstance(entry, dict) and str(entry.get("contains") or "") and str(entry["contains"]).lower() in lowered:
                return entry
        return None

    @property
    def brands(self) -> tuple[str, ...]:
        return tuple(str(name) for name in self.payload.get("tool_field_brands") or () if str(name))


_REGISTRY: dict[tuple[str, int, int], Registry] = {}


def load_registry(path: Path | None = None) -> Registry:
    target = path or REGISTRY_PATH
    try:
        info = target.stat()
        key = (str(target), info.st_mtime_ns, info.st_size)
        cached = _REGISTRY.get(key)
        if cached is not None:
            return cached
        data = target.read_bytes()
        payload = json.loads(data.decode("utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise RegistryError(f"컨테이너 생성 표식 등록표를 읽을 수 없습니다: {type(exc).__name__}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != REGISTRY_SCHEMA:
        raise RegistryError(f"컨테이너 생성 표식 등록표 형식이 {REGISTRY_SCHEMA}가 아닙니다")
    registry = Registry(str(payload.get("version", "")), hashlib.sha256(data).hexdigest(), payload)
    _REGISTRY[key] = registry
    return registry


def registry_cache_tokens() -> list[str]:
    """Scan-cache token of the registry (a new table version re-analyzes); never raises."""
    try:
        registry = load_registry()
    except RegistryError as exc:
        return [f"container-registry:error:{exc}"]
    from .encoder_fingerprints import table_cache_tokens

    return [f"container-registry:{registry.version}:{registry.sha256}", *table_cache_tokens()]


# --- Reader state -------------------------------------------------------------------


class _Reader:
    """A bounded reader over one open file: every metadata payload read is counted."""

    def __init__(self, handle: BinaryIO, size: int, facts: ContainerFacts) -> None:
        self.handle = handle
        self.size = size
        self.facts = facts
        self.used = 0
        self.boxes = 0

    def read_at(self, offset: int, length: int) -> bytes:
        self.handle.seek(offset)
        return self.handle.read(length)

    def payload(self, offset: int, length: int, what: str, *, cap: int = MAX_VALUE_BYTES) -> bytes | None:
        """``length`` bytes at ``offset`` for metadata, or None (noted) when a cap would be passed."""
        if length > cap:
            self.facts.partial.append(f"{what}({_size_text(length)})가 값 상한({_size_text(cap)})을 넘어 읽지 않음")
            return None
        if self.used + length > MAX_PAYLOAD_BYTES:
            self.facts.partial.append(f"메타데이터 판독량 상한({_size_text(MAX_PAYLOAD_BYTES)})에 도달해 {what} 이후는 읽지 않음")
            return None
        self.used += length
        data = self.read_at(offset, length)
        if len(data) < length:
            raise AnalyzerError(f"{what}를 읽는 중 파일 끝에 도달했습니다(잘린 파일)")
        return data

    def add(self, source: str, key: str, value: str, kind: str) -> None:
        value = _clean(value)
        if not value:
            return
        if len(self.facts.fields) >= MAX_FIELDS:
            note = f"필드가 {MAX_FIELDS}개를 넘어 이후 필드는 기록하지 않음"
            if note not in self.facts.partial:
                self.facts.partial.append(note)
            return
        self.facts.fields.append(Field(source, key, value, kind))


def _kind_for(key: str) -> str:
    lowered = key.lower()
    if lowered.startswith("txxx:"):
        lowered = lowered[5:]
    return KIND_TOOL if lowered in TOOL_KEYS else KIND_FREE


# --- ISO-BMFF ---------------------------------------------------------------------------


@dataclass
class _Track:
    handler: bytes = b""
    codec: str = ""
    width: int = 0
    height: int = 0
    profile_idc: int = 0
    level_idc: int = 0
    nal_length_size: int = 4
    first_chunk: int | None = None
    config_sei: list[bytes] = field(default_factory=list)


@dataclass
class _BmffState:
    tracks: list[_Track] = field(default_factory=list)
    first_mdat: int | None = None


def _iter_boxes(reader: _Reader, start: int, end: int, depth: int):
    """(type, payload start, box end, usertype) of each box in [start, end); raises AnalyzerError on a broken size."""
    pos = start
    while pos < end:
        remaining = end - pos
        if remaining < 8:
            tail = reader.read_at(pos, remaining)
            if tail.strip(b"\x00"):
                raise AnalyzerError(f"MP4 박스 헤더가 잘렸습니다(위치 {pos}, 남은 {remaining}바이트)")
            return  # QuickTime allows a 32-bit zero terminator at the end of a container
        reader.boxes += 1
        if reader.boxes > MAX_BOXES:
            reader.facts.partial.append(f"박스가 {MAX_BOXES}개를 넘어 이후 박스는 확인하지 않음")
            return
        header = reader.read_at(pos, 16 if remaining >= 16 else 8)
        if len(header) < 8:
            raise AnalyzerError(f"MP4 박스 헤더를 읽는 중 파일 끝에 도달했습니다(위치 {pos})")
        size = int.from_bytes(header[:4], "big")
        kind = header[4:8]
        header_size = 8
        if size == 1:
            if len(header) < 16:
                raise AnalyzerError(f"MP4 박스 {_box_name(kind)}의 64비트 크기 필드가 잘렸습니다(위치 {pos})")
            size = int.from_bytes(header[8:16], "big")
            header_size = 16
        elif size == 0:
            # ISO/IEC 14496-12 §4.2: size 0 = "the box extends to the end of the file" — the last top-level box only.
            if depth != 0:
                raise AnalyzerError(f"MP4 박스 {_box_name(kind)}의 크기가 0입니다(파일 끝까지 크기는 최상위 박스에서만 허용, 위치 {pos})")
            size = end - pos
        if size < header_size:
            raise AnalyzerError(f"MP4 박스 {_box_name(kind)}의 크기({size}바이트)가 헤더({header_size}바이트)보다 작습니다(위치 {pos})")
        if size > remaining:
            where = "파일 끝" if depth == 0 else "상위 박스 경계"
            raise AnalyzerError(
                f"MP4 박스 {_box_name(kind)}의 크기({size}바이트)가 {where}를 넘습니다(남은 {remaining}바이트, 위치 {pos}) — 잘렸거나 손상된 파일"
            )
        usertype = b""
        payload_start = pos + header_size
        if kind == b"uuid":
            if size < header_size + 16:
                raise AnalyzerError(f"MP4 uuid 박스가 16바이트 사용자 형식보다 짧습니다(위치 {pos})")
            usertype = reader.read_at(payload_start, 16)
            payload_start += 16
        yield kind, payload_start, pos + size, usertype
        pos += size


def _walk_bmff(reader: _Reader, start: int, end: int, depth: int, state: _BmffState, track: _Track | None) -> None:
    if depth > MAX_DEPTH:
        raise AnalyzerError(f"MP4 박스 중첩이 {MAX_DEPTH}단계를 넘습니다 — 손상되었거나 조작된 구조")
    for kind, payload, box_end, usertype in _iter_boxes(reader, start, end, depth):
        length = box_end - payload
        if depth == 0 and kind == b"mdat" and state.first_mdat is None:
            state.first_mdat = payload
        if kind in BMFF_CONTAINERS:
            current = track
            if kind == b"trak":
                current = _Track()
                state.tracks.append(current)
            _walk_bmff(reader, payload, box_end, depth + 1, state, current)
        elif kind == b"meta":
            _read_meta(reader, payload, box_end, depth + 1, state, track)
        elif kind == b"hdlr":
            handler, name = _read_hdlr(reader, payload, length)
            if track is not None and handler:
                track.handler = handler
            if name:
                reader.add("MP4 hdlr", f"hdlr({_box_name(handler)})", name, KIND_HANDLER)
        elif kind == b"mvhd":
            _read_mvhd(reader, payload, length)
        elif kind == b"stsd" and track is not None:
            _read_stsd(reader, payload, length, depth + 1, track)
        elif kind in (b"stco", b"co64") and track is not None and track.first_chunk is None:
            head = reader.payload(payload, min(length, 16), "stco")
            if head is not None and len(head) >= 8 and int.from_bytes(head[4:8], "big") > 0:
                width = 4 if kind == b"stco" else 8
                if len(head) >= 8 + width:
                    track.first_chunk = int.from_bytes(head[8 : 8 + width], "big")
        elif kind == b"uuid":
            if usertype == C2PA_UUID:
                reader.facts.c2pa_uuid = True
            elif usertype == XMP_UUID:
                _read_xmp(reader, payload, length, "MP4 XMP(uuid)")
        elif kind == b"XMP_":
            _read_xmp(reader, payload, length, "MP4 XMP(XMP_)")
        elif kind[:1] == b"\xa9" and depth >= 1:
            _read_quicktime_text(reader, kind, payload, box_end, depth)


def _read_hdlr(reader: _Reader, payload: int, length: int) -> tuple[bytes, str]:
    data = reader.payload(payload, length, "hdlr", cap=64 * 1024)
    if data is None or len(data) < 24:
        return b"", ""
    handler = data[8:12]
    raw = data[24:]
    # QuickTime writes a Pascal string, ISO a NUL-terminated UTF-8 string.
    if raw and raw[0] == len(raw) - 1 and raw[0] > 0:
        raw = raw[1:]
    return handler, _decode_text(raw.split(b"\x00", 1)[0])


def _read_mvhd(reader: _Reader, payload: int, length: int) -> None:
    data = reader.payload(payload, min(length, 32), "mvhd")
    if data is None or len(data) < 8:
        return
    version = data[0]
    if version == 1 and len(data) >= 12:
        created = int.from_bytes(data[4:12], "big")
    else:
        created = int.from_bytes(data[4:8], "big")
    if created == 0:
        reader.add("MP4 mvhd", "creation_time", "0(미기록)", KIND_TIME)
        return
    try:
        stamp = MP4_EPOCH + timedelta(seconds=created)
        text = stamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    except OverflowError:
        text = f"{created}(범위 밖 값)"
    reader.add("MP4 mvhd", "creation_time", text, KIND_TIME)


def _read_meta(reader: _Reader, payload: int, end: int, depth: int, state: _BmffState, track: _Track | None) -> None:
    if depth > MAX_DEPTH:
        raise AnalyzerError(f"MP4 박스 중첩이 {MAX_DEPTH}단계를 넘습니다 — 손상되었거나 조작된 구조")
    # ISO meta is a FullBox (4 bytes version/flags before its children); QuickTime's is a plain container.
    peek = reader.read_at(payload, 12)
    start = payload if len(peek) >= 8 and peek[4:8] in (b"hdlr", b"keys", b"ilst", b"XMP_", b"free") else payload + 4
    if start > end:
        raise AnalyzerError(f"MP4 meta 박스가 너무 짧습니다(위치 {payload})")
    keys: list[str] = []
    for kind, child, child_end, _usertype in _iter_boxes(reader, start, end, depth):
        if kind == b"hdlr":
            handler, name = _read_hdlr(reader, child, child_end - child)
            if name:
                reader.add("MP4 meta hdlr", f"hdlr({_box_name(handler)})", name, KIND_HANDLER)
        elif kind == b"keys":
            keys = _read_keys(reader, child, child_end - child)
        elif kind == b"ilst":
            _read_ilst(reader, child, child_end, depth + 1, keys)
        elif kind == b"XMP_":
            _read_xmp(reader, child, child_end - child, "MP4 XMP(XMP_)")
        elif kind == b"uuid" and _usertype == XMP_UUID:
            _read_xmp(reader, child, child_end - child, "MP4 XMP(uuid)")


def _read_keys(reader: _Reader, payload: int, length: int) -> list[str]:
    data = reader.payload(payload, length, "keys", cap=1 * MIB)
    if data is None or len(data) < 8:
        return []
    count = int.from_bytes(data[4:8], "big")
    keys: list[str] = []
    pos = 8
    for _ in range(min(count, MAX_FIELDS)):
        if pos + 8 > len(data):
            raise AnalyzerError("MP4 keys 박스의 항목이 박스 경계를 넘습니다")
        size = int.from_bytes(data[pos : pos + 4], "big")
        if size < 8 or pos + size > len(data):
            raise AnalyzerError(f"MP4 keys 박스의 항목 크기({size}바이트)가 올바르지 않습니다")
        keys.append(_decode_text(data[pos + 8 : pos + size]))
        pos += size
    return keys


def _data_value(data: bytes) -> str:
    """The value of an ilst ``data`` box payload (type indicator, locale, value)."""
    if len(data) < 8:
        return ""
    type_code = int.from_bytes(data[1:4], "big")
    value = data[8:]
    if type_code == 1:
        return _decode_text(value)
    if type_code == 2:
        return _clean(value.decode("utf-16-be", errors="replace"))
    if type_code in (21, 22) and 0 < len(value) <= 8:
        return str(int.from_bytes(value, "big", signed=type_code == 21))
    if type_code in (13, 14, 27):  # JPEG / PNG / BMP cover art
        return ""
    return _decode_text(value) if value and all(32 <= b < 127 or b in (9, 10, 13) for b in value[:256]) else ""


def _read_ilst(reader: _Reader, start: int, end: int, depth: int, keys: list[str]) -> None:
    if depth > MAX_DEPTH:
        raise AnalyzerError(f"MP4 박스 중첩이 {MAX_DEPTH}단계를 넘습니다 — 손상되었거나 조작된 구조")
    for kind, payload, item_end, _usertype in _iter_boxes(reader, start, end, depth):
        index = int.from_bytes(kind, "big")
        if keys and 1 <= index <= len(keys):
            key, source = keys[index - 1], "MP4 mdta"
        else:
            key, source = _box_name(kind), "MP4 ilst"
        mean = name = ""
        values: list[str] = []
        for child, child_payload, child_end, _ in _iter_boxes(reader, payload, item_end, depth + 1):
            if child == b"data":
                data = reader.payload(child_payload, child_end - child_payload, f"ilst {key}")
                if data is not None:
                    values.append(_data_value(data))
            elif child in (b"mean", b"name") and kind == b"----":
                data = reader.payload(child_payload, child_end - child_payload, "ilst ----", cap=64 * 1024)
                text = _decode_text(data[4:]) if data is not None and len(data) > 4 else ""
                if child == b"mean":
                    mean = text
                else:
                    name = text
        if kind == b"----":
            key = name or mean or key
        for value in values:
            reader.add(source, key, value, _kind_for(key))


def _read_quicktime_text(reader: _Reader, kind: bytes, payload: int, end: int, depth: int) -> None:
    """A QuickTime ``udta`` text atom (©too, ©cmt …): 16-bit length, 16-bit language, text — or a ``data`` box."""
    length = end - payload
    data = reader.payload(payload, length, f"udta {_box_name(kind)}", cap=1 * MIB)
    if data is None:
        return
    key = _box_name(kind)
    if len(data) >= 16 and data[4:8] == b"data":
        size = int.from_bytes(data[:4], "big")
        if 16 <= size <= len(data):
            reader.add("MP4 udta", key, _data_value(data[8:size]), _kind_for(key))
            return
    if len(data) >= 4:
        text_length = int.from_bytes(data[:2], "big")
        if 4 + text_length <= len(data):
            reader.add("MP4 udta", key, _decode_text(data[4 : 4 + text_length]), _kind_for(key))
            return
    reader.add("MP4 udta", key, _decode_text(data), _kind_for(key))


def _read_xmp(reader: _Reader, payload: int, length: int, source: str) -> None:
    data = reader.payload(payload, length, source, cap=MAX_XMP_BYTES)
    if data is None:
        return
    from .image_metadata import parse_xmp_fields

    for key, value in parse_xmp_fields(data).items():
        reader.add(source, key, value, KIND_TOOL if key == "xmp.CreatorTool" else KIND_FREE)


def _read_stsd(reader: _Reader, payload: int, length: int, depth: int, track: _Track) -> None:
    data = reader.payload(payload, length, "stsd", cap=1 * MIB)
    if data is None or len(data) < 16:
        return
    size = int.from_bytes(data[8:12], "big")
    fmt = data[12:16]
    entry = data[8 : 8 + size] if 16 <= size <= len(data) - 8 else b""
    codec = VISUAL_SAMPLE_ENTRIES.get(fmt)
    if not codec or len(entry) < 86:
        return
    track.codec = codec
    track.width = int.from_bytes(entry[8 + 24 : 8 + 26], "big")
    track.height = int.from_bytes(entry[8 + 26 : 8 + 28], "big")
    name_length = entry[8 + 42]
    if 0 < name_length <= 31:
        reader.add("MP4 stsd", "compressorname", _decode_text(entry[8 + 43 : 8 + 43 + name_length]), KIND_TOOL)
    pos = 8 + 78
    while pos + 8 <= len(entry):
        child_size = int.from_bytes(entry[pos : pos + 4], "big")
        child = entry[pos + 4 : pos + 8]
        if child_size < 8 or pos + child_size > len(entry):
            break
        body = entry[pos + 8 : pos + child_size]
        if child == b"avcC" and len(body) >= 5:
            track.profile_idc, track.level_idc = body[1], body[3]
            track.nal_length_size = (body[4] & 0x03) + 1
        elif child == b"hvcC" and len(body) >= 23:
            track.profile_idc, track.level_idc = body[1] & 0x1F, body[12]
            track.nal_length_size = (body[21] & 0x03) + 1
            track.config_sei.extend(_hvcc_sei_nals(body))
        pos += child_size


def _hvcc_sei_nals(body: bytes) -> list[bytes]:
    """SEI NAL units (types 39/40) stored in an hvcC configuration record's arrays."""
    out: list[bytes] = []
    count = body[22]
    pos = 23
    for _ in range(count):
        if pos + 3 > len(body):
            break
        nal_type = body[pos] & 0x3F
        units = int.from_bytes(body[pos + 1 : pos + 3], "big")
        pos += 3
        for _ in range(units):
            if pos + 2 > len(body):
                return out
            size = int.from_bytes(body[pos : pos + 2], "big")
            nal = body[pos + 2 : pos + 2 + size]
            pos += 2 + size
            if nal_type in (39, 40):
                out.append(nal)
    return out


def _read_bmff(reader: _Reader) -> None:
    facts = reader.facts
    facts.format_label = "MP4/MOV 메타데이터"
    facts.parts = list(BMFF_PARTS)
    state = _BmffState()
    _walk_bmff(reader, 0, reader.size, 0, state, None)
    video = next((t for t in state.tracks if t.codec), None)
    if video is None:
        facts.sei_note = "SEI 미확인(H.264/HEVC 트랙 없음)"
        return
    facts.video = VideoTrack(video.codec, video.width, video.height, video.profile_idc, video.level_idc)
    for nal in video.config_sei:
        _sei_from_nal(nal, video.codec, facts)
    offset = video.first_chunk if video.first_chunk is not None and video.first_chunk < reader.size else state.first_mdat
    if offset is None:
        facts.sei_note = f"SEI 미확인({video.codec} 샘플 위치 없음)"
        return
    window = reader.read_at(offset, min(SEI_SCAN_BYTES, reader.size - offset))
    scan_nal_units(window, video.codec, facts, nal_length_size=video.nal_length_size)
    facts.sei_note = f"SEI 사용자 데이터({video.codec}, 첫 {_size_text(SEI_SCAN_BYTES)})"


# --- H.264 / HEVC SEI -------------------------------------------------------------------


def _unescape_rbsp(data: bytes) -> bytes:
    """Remove emulation-prevention bytes (00 00 03 -> 00 00)."""
    if b"\x00\x00\x03" not in data:
        return data
    out = bytearray()
    zeros = 0
    for byte in data:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def _sei_from_nal(nal: bytes, codec: str, facts: ContainerFacts) -> None:
    if not nal:
        return
    if codec == "H.264":
        if nal[0] & 0x1F != 6:
            return
        rbsp = _unescape_rbsp(nal[1:])
    else:
        if len(nal) < 2 or (nal[0] >> 1) & 0x3F not in (39, 40):
            return
        rbsp = _unescape_rbsp(nal[2:])
    pos = 0
    while pos < len(rbsp) and rbsp[pos] != 0x80:
        payload_type = 0
        while pos < len(rbsp) and rbsp[pos] == 0xFF:
            payload_type += 255
            pos += 1
        if pos >= len(rbsp):
            return
        payload_type += rbsp[pos]
        pos += 1
        payload_size = 0
        while pos < len(rbsp) and rbsp[pos] == 0xFF:
            payload_size += 255
            pos += 1
        if pos >= len(rbsp):
            return
        payload_size += rbsp[pos]
        pos += 1
        body = rbsp[pos : pos + payload_size]
        pos += payload_size
        if payload_type == 5 and len(body) >= 16 and len(facts.sei) < MAX_SEI_STRINGS:
            text = _decode_text(body[16:])[:MAX_SEI_TEXT_CHARS]
            if text:
                facts.sei.append(SeiString(codec, body[:16].hex(), text))


def scan_nal_units(window: bytes, codec: str, facts: ContainerFacts, *, nal_length_size: int = 4) -> None:
    """SEI user_data_unregistered strings of the NAL units in ``window`` (Annex-B or length-prefixed)."""
    if window.startswith(b"\x00\x00\x01") or window.startswith(b"\x00\x00\x00\x01"):
        positions: list[int] = []
        index = window.find(b"\x00\x00\x01")
        while index >= 0 and len(positions) < 100_000:
            positions.append(index + 3)
            index = window.find(b"\x00\x00\x01", index + 3)
        for number, begin in enumerate(positions):
            stop = positions[number + 1] - 3 if number + 1 < len(positions) else len(window)
            _sei_from_nal(window[begin:stop].rstrip(b"\x00"), codec, facts)
        return
    pos = 0
    while pos + nal_length_size <= len(window):
        size = int.from_bytes(window[pos : pos + nal_length_size], "big")
        if size == 0 or pos + nal_length_size + size > len(window):
            return  # end of the chunk's NAL units (or of the scanned window)
        _sei_from_nal(window[pos + nal_length_size : pos + nal_length_size + size], codec, facts)
        pos += nal_length_size + size


# --- ID3v2 -----------------------------------------------------------------------------


def _synchsafe(data: bytes) -> int | None:
    if any(b & 0x80 for b in data):
        return None
    value = 0
    for byte in data:
        value = (value << 7) | byte
    return value


def _id3_text(encoding: int, data: bytes) -> str:
    if encoding == 1:
        text = data.decode("utf-16", errors="replace")
    elif encoding == 2:
        text = data.decode("utf-16-be", errors="replace")
    elif encoding == 3:
        text = data.decode("utf-8", errors="replace")
    else:
        text = data.decode("latin-1")
    return _clean(text.replace("\x00", " / ").strip(" /"))


def _id3_split(encoding: int, data: bytes) -> tuple[bytes, bytes]:
    """(first NUL-terminated string, rest) in the frame's encoding."""
    if encoding in (1, 2):
        for index in range(0, len(data) - 1, 2):
            if data[index : index + 2] == b"\x00\x00":
                return data[:index], data[index + 2 :]
        return data, b""
    head, _, rest = data.partition(b"\x00")
    return head, rest


ID3_V22_NAMES = {"TT2": "TIT2", "TSS": "TSSE", "TEN": "TENC", "TXX": "TXXX", "COM": "COMM", "TP1": "TPE1", "TAL": "TALB"}


def _read_id3(reader: _Reader, offset: int, limit: int, source_prefix: str = "ID3v2") -> None:
    header = reader.read_at(offset, 10)
    if len(header) < 10 or header[:3] != b"ID3":
        raise AnalyzerError("ID3v2 태그 헤더가 잘렸습니다")
    major, flags = header[3], header[5]
    size = _synchsafe(header[6:10])
    if major not in (2, 3, 4):
        raise CheckSkipped(f"ID3v2.{major} 태그 — 판독하지 않는 버전")
    if size is None:
        raise AnalyzerError("ID3v2 태그 크기 필드가 올바르지 않습니다(synchsafe 정수가 아님)")
    if offset + 10 + size > limit:
        raise AnalyzerError(f"ID3v2 태그 크기({size}바이트)가 파일 끝을 넘습니다 — 잘린 파일")
    source = f"{source_prefix}.{major}"
    reader.facts.parts.append(f"ID3v2.{major}(TXXX·TSSE·TENC·COMM·TIT2 등 텍스트 프레임)")
    body = reader.payload(offset + 10, min(size, MAX_ID3_BYTES), "ID3v2 태그", cap=MAX_ID3_BYTES)
    if body is None:
        return
    if size > MAX_ID3_BYTES:
        reader.facts.partial.append(f"ID3v2 태그({_size_text(size)}) 앞 {_size_text(MAX_ID3_BYTES)}만 판독")
    if flags & 0x80 and major < 4:
        body = body.replace(b"\xff\x00", b"\xff")
    pos = 0
    if flags & 0x40 and major >= 3:
        ext = _synchsafe(body[:4]) if major == 4 else int.from_bytes(body[:4], "big")
        if ext is None or ext > len(body):
            raise AnalyzerError("ID3v2 확장 헤더 크기가 올바르지 않습니다")
        pos = ext if major == 4 else 4 + ext
    header_size = 6 if major == 2 else 10
    id_size = 3 if major == 2 else 4
    while pos + header_size <= len(body):
        frame_id = body[pos : pos + id_size]
        if frame_id[:1] == b"\x00":
            return  # padding
        if not all(48 <= b <= 57 or 65 <= b <= 90 for b in frame_id):
            reader.facts.partial.append("ID3 프레임 식별자가 올바르지 않아 이후 프레임은 읽지 않음")
            return
        if major == 2:
            frame_size = int.from_bytes(body[pos + 3 : pos + 6], "big")
            frame_flags = 0
        elif major == 4:
            frame_size = _synchsafe(body[pos + 4 : pos + 8]) or int.from_bytes(body[pos + 4 : pos + 8], "big")
            frame_flags = body[pos + 9]
        else:
            frame_size = int.from_bytes(body[pos + 4 : pos + 8], "big")
            frame_flags = body[pos + 9]
        start = pos + header_size
        if start + frame_size > len(body):
            if len(body) < size:
                return  # the read stopped at MAX_ID3_BYTES — already noted
            raise AnalyzerError(f"ID3 프레임 {frame_id.decode('latin-1')}의 크기({frame_size}바이트)가 태그 경계를 넘습니다")
        data = body[start : start + frame_size]
        pos = start + frame_size
        name = frame_id.decode("latin-1")
        name = ID3_V22_NAMES.get(name, name) if major == 2 else name
        if major == 4:
            if frame_flags & 0x0C:  # compressed / encrypted
                continue
            if frame_flags & 0x02:
                data = data.replace(b"\xff\x00", b"\xff")
            if frame_flags & 0x01:
                data = data[4:]
        elif major == 3 and frame_flags & 0xC0:  # v2.3 compression / encryption
            continue
        if not data:
            continue
        encoding = data[0]
        if name == "TXXX":
            description, value = _id3_split(encoding, data[1:])
            key = f"TXXX:{_id3_text(encoding, description)}"
            reader.add(source, key, _id3_text(encoding, value), _kind_for(key))
        elif name == "COMM" and len(data) >= 4:
            _description, value = _id3_split(encoding, data[4:])
            reader.add(source, name, _id3_text(encoding, value), KIND_FREE)
        elif name.startswith("T"):
            reader.add(source, name, _id3_text(encoding, data[1:]), _kind_for(name))


# --- RIFF (WAV / AVI) ------------------------------------------------------------------


def _read_riff(reader: _Reader, form: bytes) -> None:
    facts = reader.facts
    facts.format_label = "RIFF(WAV)" if form == b"WAVE" else "RIFF(AVI)"
    facts.parts.append("LIST/INFO")
    pos = 12
    while pos + 8 <= reader.size:
        reader.boxes += 1
        if reader.boxes > MAX_BOXES:
            facts.partial.append(f"RIFF 청크가 {MAX_BOXES}개를 넘어 이후 청크는 확인하지 않음")
            return
        header = reader.read_at(pos, 12)
        kind = header[:4]
        size = int.from_bytes(header[4:8], "little")
        end = pos + 8 + size
        if end > reader.size:
            # A streamed WAV's data chunk is often written with size 0 or 0xFFFFFFFF ("to the end").
            if kind == b"data" and size in (0, 0xFFFFFFFF):
                return
            raise AnalyzerError(
                f"RIFF 청크 {_box_name(kind)}의 크기({size}바이트)가 파일 끝을 넘습니다(남은 {reader.size - pos - 8}바이트) — 잘린 파일"
            )
        if kind == b"LIST" and header[8:12] == b"INFO" and size >= 4:
            data = reader.payload(pos + 12, size - 4, "RIFF LIST/INFO", cap=1 * MIB)
            if data is not None:
                _read_info(reader, data)
        elif kind in (b"id3 ", b"ID3 "):
            if "id3" not in facts.parts:
                facts.parts.append("id3")
            _read_id3(reader, pos + 8, end, "RIFF id3 ID3v2")
        pos = end + (size & 1)


def _read_info(reader: _Reader, data: bytes) -> None:
    pos = 0
    while pos + 8 <= len(data):
        kind = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 8], "little")
        if pos + 8 + size > len(data):
            raise AnalyzerError(f"RIFF INFO 항목 {_box_name(kind)}의 크기가 LIST 경계를 넘습니다")
        key = _box_name(kind)
        reader.add("RIFF INFO", key, _decode_text(data[pos + 8 : pos + 8 + size]), _kind_for(key))
        pos += 8 + size + (size & 1)


# --- FLAC -----------------------------------------------------------------------------------


def _read_flac(reader: _Reader) -> None:
    facts = reader.facts
    facts.format_label = "FLAC 메타데이터"
    facts.parts.append("Vorbis 주석")
    pos = 4
    for _ in range(1024):
        header = reader.read_at(pos, 4)
        if len(header) < 4:
            raise AnalyzerError("FLAC 메타데이터 블록 헤더가 잘렸습니다")
        last, kind, size = header[0] & 0x80, header[0] & 0x7F, int.from_bytes(header[1:4], "big")
        if kind == 127:
            raise AnalyzerError("FLAC 메타데이터 블록 형식 127(금지된 값)")
        if pos + 4 + size > reader.size:
            raise AnalyzerError(f"FLAC 메타데이터 블록({size}바이트)이 파일 끝을 넘습니다 — 잘린 파일")
        if kind == 4:
            data = reader.payload(pos + 4, size, "FLAC Vorbis 주석")
            if data is not None:
                read_vorbis_comments(reader, data, "FLAC Vorbis 주석")
        pos += 4 + size
        if last:
            return
    facts.partial.append("FLAC 메타데이터 블록이 1024개를 넘어 이후는 확인하지 않음")


def read_vorbis_comments(reader: _Reader, data: bytes, source: str) -> None:
    if len(data) < 8:
        raise AnalyzerError("Vorbis 주석 블록이 너무 짧습니다")
    vendor_length = int.from_bytes(data[:4], "little")
    if 4 + vendor_length + 4 > len(data):
        raise AnalyzerError("Vorbis 주석 vendor 문자열 길이가 블록 경계를 넘습니다")
    reader.add(source, "vendor", data[4 : 4 + vendor_length].decode("utf-8", errors="replace"), KIND_TOOL)
    pos = 4 + vendor_length
    count = int.from_bytes(data[pos : pos + 4], "little")
    pos += 4
    for _ in range(min(count, MAX_FIELDS)):
        if pos + 4 > len(data):
            raise AnalyzerError("Vorbis 주석 항목이 블록 경계를 넘습니다")
        length = int.from_bytes(data[pos : pos + 4], "little")
        if pos + 4 + length > len(data):
            raise AnalyzerError("Vorbis 주석 항목 길이가 블록 경계를 넘습니다")
        entry = data[pos + 4 : pos + 4 + length].decode("utf-8", errors="replace")
        pos += 4 + length
        key, sep, value = entry.partition("=")
        if sep:
            reader.add(source, key.upper(), value, _kind_for(key))


# --- Entry point --------------------------------------------------------------------------


def _not_read(label: str) -> CheckSkipped:
    return CheckSkipped(f"{label} — 이 형식의 컨테이너 메타데이터는 판독하지 않음(지원: MP4/MOV·RIFF(WAV/AVI)·ID3v2(MP3)·FLAC)")


def read_container_metadata(path: Path) -> ContainerFacts:
    """The container facts of one media file.

    Raises :class:`CheckSkipped` for a format it does not parse,
    ``AnalyzerError`` (Korean) for a structure it could not walk and
    ``OSError`` when the file cannot be read.
    """
    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        head = handle.read(16)
        facts = ContainerFacts("컨테이너 메타데이터")
        reader = _Reader(handle, size, facts)
        if not head:
            raise AnalyzerError("빈 파일 — 컨테이너 메타데이터를 읽을 수 없습니다")
        if len(head) >= 8 and head[4:8] in BMFF_TOP_TYPES:
            _read_bmff(reader)
        elif head[:4] == b"RIFF" and head[8:12] in (b"WAVE", b"AVI "):
            _read_riff(reader, head[8:12])
        elif head[:3] == b"ID3":
            facts.format_label = "ID3v2 태그"
            _read_id3(reader, 0, size)
            facts.parts = list(dict.fromkeys(facts.parts))
        elif head[:4] == b"fLaC":
            _read_flac(reader)
        elif len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
            facts.format_label = "MPEG 오디오(ID3v2 태그 확인)"
            facts.parts.append("태그 없음")
        elif head[:4] == b"OggS":
            raise _not_read("Ogg(Vorbis·Opus)")
        elif head[:4] == b"\x1a\x45\xdf\xa3":
            raise _not_read("Matroska/WebM")
        elif head[:3] == b"FLV":
            raise _not_read("FLV")
        elif head[:4] == b"\x30\x26\xb2\x75":
            raise _not_read("ASF(WMA/WMV)")
        else:
            raise CheckSkipped("형식 식별 불가 — 파일 헤더가 지원하는 컨테이너(MP4/MOV·RIFF(WAV/AVI)·ID3v2(MP3)·FLAC)가 아님")
    return facts


# --- Scan wiring (coverage entry, evidence, marking-context finding) -------------------------

MARK_ALGORITHM = "컨테이너·비트스트림 생성 표식(GB 45438 AIGC 라벨·ComfyUI 워크플로·등록 SEI 문자열·생성 도구 필드)"
MARK_NOT_FOUND = "판독한 필드·SEI에 생성 표식 없음 — 부재는 무정보(재저장으로 사라질 수 있음)"
IMAGE_AIGC_PARTS = {
    "png": "PNG 텍스트 청크(tEXt·zTXt·iTXt)·XMP",
    "jpeg": "XMP(APP1)",
    "webp": "XMP 청크",
    "tiff": "XMP",
}
# Keys of image_metadata's dict that hold an AIGC label, with where they were read.
IMAGE_AIGC_KEYS = (("png.aigc", "PNG 텍스트 청크 AIGC"), ("xmp.AIGC", "XMP AIGC"))


def _layers(entry: Any, items: list[Any], *, ran_limitation: bool) -> Any:
    from .evidence_rules import CONTAINER_AIGC_FACT_TITLE, CONTAINER_AIGC_UNPARSED_TITLE, CONTAINER_LIMITATION
    from .media_marks import MarkFinding, MarkLayers
    from .result_types import CoverageStatus, EvidenceDirection

    layers = MarkLayers(evidence=list(items), coverage=[entry])
    if entry.status != CoverageStatus.RAN:
        layers.findings.append(MarkFinding(CHECK, MARK_ALGORITHM, entry.status.value, entry.reason))
        return layers
    marks = [
        item.title for item in items
        if item.direction == EvidenceDirection.SYNTHETIC or item.title in (CONTAINER_AIGC_FACT_TITLE, CONTAINER_AIGC_UNPARSED_TITLE)
    ]
    if marks:
        layers.findings.append(MarkFinding(CHECK, MARK_ALGORITHM, "found", "; ".join(dict.fromkeys(marks))))
    else:
        layers.findings.append(MarkFinding(CHECK, MARK_ALGORITHM, "not_found", MARK_NOT_FOUND))
    if ran_limitation:
        layers.limitations.append(CONTAINER_LIMITATION)
    return layers


def media_layers(path: Path) -> Any:
    """The ``container_metadata`` check of an audio or video file, as ``media_marks.MarkLayers``."""
    from .checks import run_check
    from .encoder_fingerprints import TableError, load_table
    from .evidence_rules import container_metadata_evidence, messenger_video_evidence
    from .result_types import CoverageEntry, CoverageStatus

    def check() -> tuple[ContainerFacts, list[Any], list[Any]]:
        registry = load_registry()
        facts = read_container_metadata(path)
        messenger: list[Any] = []
        if facts.video is not None:
            try:
                messenger = messenger_video_evidence(facts, load_table())
            except TableError as exc:
                facts.partial.append(f"메신저 전송본 형식 대조 안 함: {exc}")
        return facts, container_metadata_evidence(facts, registry), messenger

    outcome, entry = run_check(CHECK, check)
    if outcome is None:
        return _layers(entry, [], ran_limitation=False)
    facts, items, messenger = outcome
    layers = _layers(CoverageEntry(CHECK, CoverageStatus.RAN, facts.coverage_reason()), [*items, *messenger], ran_limitation=True)
    if messenger:
        from .platform_traces import PATH_LIMITATION

        layers.limitations.append(PATH_LIMITATION)
    return layers


def _jpeg_table_items(path: Path, metadata: dict[str, str]) -> tuple[list[Any], str]:
    """The IJG quantization-table facts of a JPEG and the coverage-reason note; a table that cannot be compared is a note."""
    from .encoder_fingerprints import TableError, jpeg_ijg_comparison, load_table
    from .evidence_rules import jpeg_table_evidence

    try:
        table = load_table()
        comparison = jpeg_ijg_comparison(path, table)
    except (TableError, CheckSkipped) as exc:
        return [], f"JPEG 양자화표 대조 안 함: {exc}"
    except OSError as exc:
        return [], f"JPEG 양자화표 대조 안 함: 파일을 읽지 못함({type(exc).__name__})"
    if comparison.matched:
        note = f"JPEG 양자화표: IJG 표준 표(품질 {comparison.qualities[0]})와 일치"
    else:
        note = f"JPEG 양자화표: IJG 표준 표 아님(가장 가까운 품질 {comparison.nearest}, {comparison.differing}개 값 다름)"
    return jpeg_table_evidence(comparison, metadata, table.label), note


def image_aigc_layers(
    metadata: dict[str, str], image_format: str | None, read_error: str | None, partial: str | None, path: Path | None = None,
) -> Any:
    """The ``container_metadata`` check of an image — the GB 45438 ``AIGC`` label in PNG text chunks and XMP only.

    The fields come from the ``metadata`` check's read (image_metadata); a
    read that failed makes this check ``failed`` too (whatever was read is
    still evaluated, as for the other metadata items).
    """
    from .checks import failed, run_check, skipped
    from .evidence_rules import aigc_label_evidence
    from .result_types import CoverageEntry, CoverageStatus

    if read_error is None and image_format not in IMAGE_AIGC_PARTS:
        label = (image_format or "알 수 없음").upper()
        return _layers(skipped(CHECK, f"이 이미지 형식({label})에는 PNG 텍스트 청크·XMP가 없어 AIGC 라벨을 판독하지 않음"), [], ran_limitation=False)

    def check() -> tuple[list[Any], str]:
        registry = load_registry()
        items: list[Any] = []
        for key, where in IMAGE_AIGC_KEYS:
            if metadata.get(key):
                items.extend(aigc_label_evidence(where, metadata[key], registry))
        note = ""
        if path is not None and image_format == "jpeg":
            tables, note = _jpeg_table_items(path, metadata)
            items.extend(tables)
        return items, note

    outcome, entry = run_check(CHECK, check)
    if outcome is None:
        return _layers(entry, [], ran_limitation=False)
    items, jpeg_note = outcome
    if read_error is not None:
        entry = failed(CHECK, AnalyzerError(f"이미지 메타데이터를 끝까지 읽지 못해 AIGC 라벨 확인이 불완전합니다({read_error})"))
        return _layers(entry, items, ran_limitation=False)
    reason = f"AIGC 암묵 라벨 판독({IMAGE_AIGC_PARTS[str(image_format)]})"
    found = sum(1 for key, _ in IMAGE_AIGC_KEYS if metadata.get(key))
    reason += f" — AIGC 필드 {found}개" if found else " — AIGC 필드 없음"
    if jpeg_note:
        reason += f" · {jpeg_note}"
    if partial:
        reason += f" — 일부만 판독: {partial}"
    return _layers(CoverageEntry(CHECK, CoverageStatus.RAN, reason), items, ran_limitation=False)


__all__ = [
    "CHECK",
    "ContainerFacts",
    "Field",
    "KIND_FREE",
    "KIND_HANDLER",
    "KIND_TIME",
    "KIND_TOOL",
    "LAYER",
    "MAX_PAYLOAD_BYTES",
    "REGISTRY_PATH",
    "Registry",
    "RegistryError",
    "SEI_SCAN_BYTES",
    "SeiString",
    "VideoTrack",
    "image_aigc_layers",
    "load_registry",
    "media_layers",
    "read_container_metadata",
    "registry_cache_tokens",
    "scan_nal_units",
]

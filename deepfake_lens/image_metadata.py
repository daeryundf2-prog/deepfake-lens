"""Image metadata readers and generator-tool fingerprints.

Extracted from ``core`` — header sniffing, JPEG/WebP/TIFF dimension
parsing, and AI-generator metadata pattern matching.

D7 (phase-0 verification): JPEG/WebP/TIFF/PNG EXIF is read with Pillow's
``getexif()`` (IFD0 Make/Model/Software/DateTime, Exif IFD
DateTimeOriginal/LensModel, GPS IFD) into ``exif.*`` keys, and the XMP
packet (the JPEG APP1 ``http://ns.adobe.com/xap/1.0/`` segment, the PNG
iTXt ``XML:com.adobe.xmp`` chunk, the WebP ``XMP `` chunk, else Pillow's
``info["xmp"]``) is parsed here into ``xmp.*`` keys: ``xmp:CreatorTool``,
``Iptc4xmpExt:DigitalSourceType``, ``dc:creator`` and
``photoshop:Credit``. Pillow's own ``Image.getxmp()`` needs the optional
``defusedxml`` package and returns ``{}`` without it, so the packet is
parsed by one in-module parser in every environment (a packet with a
DOCTYPE/ENTITY declaration is refused, so no entity expansion happens).

:func:`read_image_metadata_full` also reports whether the read was
complete (D16). A truncated JPEG (no SOS marker), a PNG without IEND, an
empty file, an unrecognized header behind an image extension or a Pillow
EXIF error is a failed metadata read — the caller records the
``metadata`` check as failed instead of claiming the file has no metadata.
"""


from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path

from .png import read_png_dimensions, read_png_metadata
from .result_types import SourceConfidence, SourceGuess
from .scan_cache import _read_prefix
from .text_heuristics import (
    _contains_generation_fields,
    _direct_tool_guess,
    _looks_like_a1111,
    _looks_like_comfyui,
)

DEFAULT_METADATA_BYTES = 4 * 1024 * 1024


# XMP packet identifier in a JPEG APP1 segment (XMP Specification Part 3,
# 1.1.3) and the PNG iTXt keyword for an XMP packet (Part 3, 1.1.5) as
# png.read_png_metadata keys it.
JPEG_XMP_IDENTIFIER = b"http://ns.adobe.com/xap/1.0/\x00"
PNG_XMP_KEY = "png.xml:com.adobe.xmp"
# Upper bound on a parsed XMP packet (a JPEG APP1 segment holds at most
# 65,533 bytes; extended XMP is not followed).
MAX_XMP_BYTES = 1024 * 1024

# Namespaces of the XMP fields read (XMP Spec Part 1 §8.4; IPTC Photo
# Metadata Standard 2023.1 "Digital Source Type"; Dublin Core; Photoshop).
XMP_NS = "http://ns.adobe.com/xap/1.0/"
IPTC_EXT_NS = "http://iptc.org/std/Iptc4xmpExt/2008-02-29/"
DC_NS = "http://purl.org/dc/elements/1.1/"
PHOTOSHOP_NS = "http://ns.adobe.com/photoshop/1.0/"
XMP_FIELDS = {
    (XMP_NS, "CreatorTool"): "xmp.CreatorTool",
    (IPTC_EXT_NS, "DigitalSourceType"): "xmp.DigitalSourceType",
    (DC_NS, "creator"): "xmp.creator",
    (PHOTOSHOP_NS, "Credit"): "xmp.Credit",
}

# EXIF tag ids (EXIF 2.32 / CIPA DC-008): IFD0, the Exif sub-IFD, the GPS IFD.
EXIF_IFD0_TAGS = {271: "exif.Make", 272: "exif.Model", 305: "exif.Software", 306: "exif.DateTime"}
EXIF_SUB_IFD = 0x8769
EXIF_SUB_TAGS = {36867: "exif.DateTimeOriginal", 42035: "exif.LensMake", 42036: "exif.LensModel"}
EXIF_GPS_IFD = 0x8825
GPS_LATITUDE_REF, GPS_LATITUDE, GPS_LONGITUDE_REF, GPS_LONGITUDE, GPS_DATESTAMP = 1, 2, 3, 4, 29
# Longest metadata string kept: camera strings are short; a stuffed tag is
# truncated so it cannot bloat the report.
MAX_EXIF_VALUE_CHARS = 256
# Containers whose EXIF Pillow reads.
EXIF_CAPABLE_FORMATS = frozenset({"jpeg", "png", "webp", "tiff"})


@dataclass
class ImageMetadataRead:
    """Parsed metadata plus whether the read was complete (D7, D16).

    ``error`` is set when the metadata could not be read completely
    (truncated structure, empty file, unrecognized header, EXIF read
    error); ``notes`` lists non-fatal gaps (EXIF not read because Pillow is
    absent) shown as limitations. ``exif_read`` is True when Pillow read
    the EXIF block (which may be empty).
    """

    metadata: dict[str, str] = field(default_factory=dict)
    dimensions: tuple[int, int] | None = None
    image_format: str | None = None
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    exif_read: bool = False


def read_image_metadata(path: Path, *, metadata_bytes: int = DEFAULT_METADATA_BYTES) -> tuple[dict[str, str], tuple[int, int] | None]:
    read = read_image_metadata_full(path, metadata_bytes=metadata_bytes)
    return read.metadata, read.dimensions


def read_image_metadata_full(path: Path, *, metadata_bytes: int = DEFAULT_METADATA_BYTES) -> ImageMetadataRead:
    """Header, text chunks, EXIF and XMP of one image (D7).

    Raises ``OSError`` only when the file cannot be opened; a damaged
    structure is reported through ``error``.
    """
    data = _read_prefix(path, metadata_bytes)
    # The structural completeness checks only apply when the prefix holds
    # the whole file — a prefix cut by ``metadata_bytes`` is not truncation.
    whole_file = len(data) < max(1, metadata_bytes)
    read = ImageMetadataRead()
    metadata = read.metadata
    xmp_packet: bytes | None = None

    if not data:
        read.error = "빈 파일 — 메타데이터를 읽을 수 없습니다"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        read.image_format = "png"
        metadata.update(read_png_metadata(data))
        read.dimensions = read_png_dimensions(data)
        packet = metadata.get(PNG_XMP_KEY)
        xmp_packet = packet.encode("utf-8", errors="replace") if packet else None
        if whole_file and not _png_reaches_iend(data):
            read.error = "PNG 구조 불완전(IEND 청크 없음) — 잘린 파일로 메타데이터를 끝까지 읽지 못했습니다"
    elif data.startswith(b"\xff\xd8"):
        read.image_format = "jpeg"
        read.dimensions = _read_jpeg_dimensions(data)
        xmp_packet, reached_scan = _scan_jpeg_segments(data)
        if whole_file and not reached_scan:
            read.error = "JPEG 구조 불완전(SOS 마커 전에 파일 끝) — 잘린 파일로 메타데이터를 끝까지 읽지 못했습니다"
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        read.image_format = "webp"
        read.dimensions = _read_webp_dimensions(data)
        xmp_packet = _webp_xmp_chunk(data)
    elif data.startswith(b"BM") and len(data) >= 26:
        read.image_format = "bmp"
        width, height = struct.unpack_from("<ii", data, 18)
        if width > 0:
            read.dimensions = (width, abs(height))
    elif data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        read.image_format = "gif"
        read.dimensions = struct.unpack_from("<HH", data, 6)
    elif data[:4] in (b"II*\x00", b"MM\x00*"):
        read.image_format = "tiff"
        read.dimensions = _read_tiff_dimensions(data)
    else:
        read.error = "이미지 형식 식별 불가(파일 헤더가 이미지 형식이 아님 — 확장자 위장 가능성)"

    if read.image_format in EXIF_CAPABLE_FORMATS and read.error is None:
        pillow_xmp, exif_error = _read_exif_into(path, metadata, read)
        if exif_error:
            read.error = exif_error
        if xmp_packet is None:
            xmp_packet = pillow_xmp
    if xmp_packet:
        metadata.update(parse_xmp_fields(xmp_packet))

    header_text = _extract_header_text(data)
    if header_text:
        metadata["header.text"] = header_text
    return read


def _read_exif_into(path: Path, metadata: dict[str, str], read: ImageMetadataRead) -> tuple[bytes | None, str | None]:
    """Fill ``exif.*`` keys with Pillow.

    Returns ``(xmp packet from Pillow's info, error reason or None)``.
    Pillow absent is a note, not an error.
    """
    try:
        from PIL import Image
    except ImportError:
        read.notes.append("EXIF 미판독: 의존성 부재: PIL(Pillow) — 카메라 EXIF 일관성 검사를 하지 않았습니다.")
        return None, None
    values: dict[str, str] = {}
    packet: bytes | None = None
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            for tag, key in EXIF_IFD0_TAGS.items():
                _put_exif(values, key, exif.get(tag))
            sub = exif.get_ifd(EXIF_SUB_IFD)
            for tag, key in EXIF_SUB_TAGS.items():
                _put_exif(values, key, sub.get(tag))
            gps = exif.get_ifd(EXIF_GPS_IFD)
            if gps:
                values["exif.GPSInfo"] = "present"
                latitude = _gps_decimal(gps.get(GPS_LATITUDE), gps.get(GPS_LATITUDE_REF))
                longitude = _gps_decimal(gps.get(GPS_LONGITUDE), gps.get(GPS_LONGITUDE_REF))
                if latitude is not None:
                    values["exif.GPSLatitude"] = f"{latitude:.6f}"
                if longitude is not None:
                    values["exif.GPSLongitude"] = f"{longitude:.6f}"
                _put_exif(values, "exif.GPSDateStamp", gps.get(GPS_DATESTAMP))
            raw_xmp = image.info.get("xmp")
            if isinstance(raw_xmp, str):
                packet = raw_xmp.encode("utf-8", errors="replace")
            elif isinstance(raw_xmp, bytes):
                packet = raw_xmp
    except Exception as exc:  # noqa: BLE001 - Pillow raises many decoder-specific types; recorded as a failed read
        from .error_text import exception_text

        return None, f"EXIF 판독 실패: {type(exc).__name__}: {exception_text(exc, 160)}"
    metadata.update(values)
    read.exif_read = True
    return packet, None


def _put_exif(values: dict[str, str], key: str, raw: object) -> None:
    if raw is None:
        return
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    text = str(raw).replace("\x00", "").strip()
    if text:
        values[key] = text[:MAX_EXIF_VALUE_CHARS]


def _gps_decimal(value: object, ref: object) -> float | None:
    """Degrees/minutes/seconds rationals -> signed decimal degrees, or None."""
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        return None
    try:
        degrees, minutes, seconds = (float(part) for part in value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    if isinstance(ref, bytes):
        ref = ref.decode("ascii", errors="ignore")
    if str(ref).strip().upper() in {"S", "W"}:
        decimal = -decimal
    return decimal


def _png_reaches_iend(data: bytes) -> bool:
    offset = 8
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        if data[offset + 4 : offset + 8] == b"IEND":
            return True
        offset += 12 + length
    return False


def _scan_jpeg_segments(data: bytes) -> tuple[bytes | None, bool]:
    """(XMP packet from APP1, whether the SOS marker was reached)."""
    offset = 2
    xmp: bytes | None = None
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            return xmp, False
        marker = data[offset + 1]
        if marker == 0xFF:  # fill byte
            offset += 1
            continue
        if marker == 0xDA:  # start of scan — every metadata segment precedes it
            return xmp, True
        if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        length = int.from_bytes(data[offset + 2 : offset + 4], "big")
        if length < 2 or offset + 2 + length > len(data):
            return xmp, False
        body = data[offset + 4 : offset + 2 + length]
        if marker == 0xE1 and xmp is None and body.startswith(JPEG_XMP_IDENTIFIER):
            xmp = body[len(JPEG_XMP_IDENTIFIER):]
        offset += 2 + length
    return xmp, False


def _webp_xmp_chunk(data: bytes) -> bytes | None:
    offset = 12
    while offset + 8 <= len(data):
        kind = data[offset : offset + 4]
        size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        start = offset + 8
        if start + size > len(data):
            return None
        if kind == b"XMP ":
            return data[start : start + size]
        offset = start + size + (size % 2)
    return None


_XMP_DECLARATION = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.I)


def parse_xmp_fields(packet: bytes) -> dict[str, str]:
    """``xmp.*`` keys for the generator-relevant XMP fields (D7).

    Values may be attributes (``xmp:CreatorTool="…"``) or elements; list
    values (``dc:creator`` as ``rdf:Seq``) are joined with "; ". A packet
    with a DTD/entity declaration, an oversized one or one that does not
    parse yields ``{}`` — none of its fields are used.
    """
    if not packet or len(packet) > MAX_XMP_BYTES or _XMP_DECLARATION.search(packet):
        return {}
    for opener, closer in ((b"<x:xmpmeta", b"</x:xmpmeta>"), (b"<rdf:RDF", b"</rdf:RDF>")):
        start, end = packet.find(opener), packet.rfind(closer)
        if 0 <= start < end:
            packet = packet[start : end + len(closer)]
            break
    try:
        root = ElementTree.fromstring(packet)
    except ElementTree.ParseError:
        return {}
    found: dict[str, list[str]] = {}
    for element in root.iter():
        for attribute, value in element.attrib.items():
            key = XMP_FIELDS.get(_split_qname(attribute))
            if key and value.strip():
                found.setdefault(key, []).append(value.strip())
        key = XMP_FIELDS.get(_split_qname(element.tag))
        if key:
            texts = [text.strip() for text in element.itertext() if text and text.strip()]
            found.setdefault(key, []).extend(texts)
    return {
        key: "; ".join(dict.fromkeys(values))[:MAX_EXIF_VALUE_CHARS]
        for key, values in found.items()
        if values
    }


def _split_qname(name: str) -> tuple[str, str]:
    if name.startswith("{") and "}" in name:
        namespace, local = name[1:].split("}", 1)
        return namespace, local
    return "", name


def _read_tiff_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read width/height from a TIFF header's first IFD (tags 256/257)."""
    little = data[:2] == b"II"
    order = "<" if little else ">"
    try:
        ifd_offset = struct.unpack_from(order + "I", data, 4)[0]
        count = struct.unpack_from(order + "H", data, ifd_offset)[0]
        if count > 200:
            return None
        width = height = None
        for index in range(count):
            base = ifd_offset + 2 + index * 12
            if base + 12 > len(data):
                break
            tag, field_type, num = struct.unpack_from(order + "HHI", data, base)
            if tag not in (256, 257) or field_type not in (3, 4) or num != 1:
                continue
            value = (
                struct.unpack_from(order + "H", data, base + 8)[0]
                if field_type == 3
                else struct.unpack_from(order + "I", data, base + 8)[0]
            )
            if tag == 256:
                width = value
            else:
                height = value
        if width and height:
            return width, height
    except (struct.error, IndexError):
        return None
    return None


def guess_image_source(metadata: dict[str, str]) -> SourceGuess:
    blob = "\n".join(f"{key}: {value}" for key, value in metadata.items())
    normalized = blob.lower()
    if not normalized.strip():
        return SourceGuess.unknown()

    if _looks_like_comfyui(normalized):
        return SourceGuess("ComfyUI 추정", SourceConfidence.HIGH, ["ComfyUI workflow/prompt 구조가 발견되었습니다."])
    if _looks_like_a1111(normalized):
        return SourceGuess("Stable Diffusion / A1111 추정", SourceConfidence.HIGH, ["프롬프트와 `Steps`, `Sampler`, `CFG scale`, `Seed` 같은 A1111 생성 파라미터가 발견되었습니다."])

    direct = _direct_tool_guess(normalized)
    if direct:
        return direct

    if _contains_generation_fields(normalized):
        return SourceGuess("AI 생성 메타데이터 추정", SourceConfidence.MEDIUM, ["prompt/model/seed/CFG 계열 필드가 발견되었습니다."])
    return SourceGuess.unknown()


def _extract_header_text(data: bytes) -> str:
    text = data.decode("latin-1", errors="ignore")
    strings = re.findall(r"[ -~]{4,}", text)
    useful = [item for item in strings if any(marker in item.lower() for marker in ["prompt", "seed", "sampler", "stable", "comfy", "midjourney", "openai", "firefly", "runway", "novelai", "leonardo"])]
    return "\n".join(useful[:80])


def _read_jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        return None
    offset = 2
    sof_markers = set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0))
    while offset + 9 < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return None
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            return None
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return width, height
        offset += segment_length
    return None


def _read_webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        return None
    offset = 12
    while offset + 8 <= len(data):
        chunk_type = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        chunk_start = offset + 8
        if chunk_start + chunk_size > len(data):
            return None
        chunk = data[chunk_start : chunk_start + chunk_size]
        if chunk_type == b"VP8X" and len(chunk) >= 10:
            width = 1 + int.from_bytes(chunk[4:7], "little")
            height = 1 + int.from_bytes(chunk[7:10], "little")
            return width, height
        offset = chunk_start + chunk_size + (chunk_size % 2)
    return None

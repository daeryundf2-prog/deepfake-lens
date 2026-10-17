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

import os
import re
import struct
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
# P0R-22: the China GB 45438-2025 implicit label in XMP — a property named
# ``AIGC`` (TC260 namespace; any namespace is accepted) whose value is the
# label JSON. Kept up to XMP_AIGC_MAX_CHARS (the 7-key JSON is longer than
# MAX_EXIF_VALUE_CHARS); read by container_metadata.image_aigc_layers.
XMP_AIGC_KEY = "xmp.AIGC"
XMP_AIGC_LOCAL_NAME = "AIGC"
XMP_AIGC_MAX_CHARS = 4096

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
    # R18-4 (round 18): why only part of the file's metadata was checked
    # (the ``metadata`` coverage entry stays ``ran`` with this reason, and it
    # is a limitation) — None when every metadata structure was read.
    partial: str | None = None


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
        # R18-4 (round 18): the chunks are walked through the whole file
        # (IDAT skipped by seeking) — a tEXt chunk after a 4 MB prefix (or
        # after an IDAT chunk over MAX_CHUNK_BYTES) was never read, and the
        # check still said "ran".
        walk = _walk_png(path, budget=metadata_bytes)
        metadata.update(read_png_metadata(walk.data))
        read.dimensions = read_png_dimensions(walk.data)
        packet = metadata.get(PNG_XMP_KEY)
        xmp_packet = packet.encode("utf-8", errors="replace") if packet else None
        if not walk.complete:
            read.error = "PNG 구조 불완전(IEND 청크 없음) — 잘린 파일로 메타데이터를 끝까지 읽지 못했습니다"
        read.partial = walk.partial
    elif data.startswith(b"\xff\xd8"):
        read.image_format = "jpeg"
        read.dimensions = _read_jpeg_dimensions(data)
        xmp_packet, reached_scan = _scan_jpeg_segments(data)
        if whole_file and not reached_scan:
            read.error = "JPEG 구조 불완전(SOS 마커 전에 파일 끝) — 잘린 파일로 메타데이터를 끝까지 읽지 못했습니다"
        elif not reached_scan:
            # R18-4: every JPEG metadata segment precedes SOS — not reached inside the prefix.
            read.partial = metadata_prefix_reason(metadata_bytes, "SOS 마커 전 세그먼트를 끝까지 읽지 못함")
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        read.image_format = "webp"
        read.dimensions = _read_webp_dimensions(data)
        # R18-4: the XMP chunk of an extended WebP follows the image data — walked through the file.
        xmp_packet, read.partial = _walk_webp_xmp(path, budget=metadata_bytes)
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
    if read.image_format in ("bmp", "gif", "tiff") and not whole_file:
        # R18-4: these are searched for metadata text in the prefix only.
        read.partial = metadata_prefix_reason(metadata_bytes, "이후 부분의 문자열 메타데이터는 검사하지 않음")
    if read.partial:
        read.notes.append(read.partial)

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
        try:
            with Image.open(path) as image:
                _exif_values(image.getexif(), values)
                raw_xmp = image.info.get("xmp")
                if isinstance(raw_xmp, str):
                    packet = raw_xmp.encode("utf-8", errors="replace")
                elif isinstance(raw_xmp, bytes):
                    packet = raw_xmp
        except Image.DecompressionBombError:
            # R18-6 (round 18): Pillow's pixel-count limit (a decompression-bomb
            # guard) is raised by Image.open itself, so a 200 MP camera photo
            # (16320×12240, Galaxy S23/S24 Ultra) lost its EXIF: "EXIF 판독 실패:
            # DecompressionBombError". Reading tags decodes no pixel — the EXIF
            # block is taken from the file's own structure and parsed alone.
            values.clear()
            block = _raw_exif_block(path, read.image_format)
            if block:
                exif = Image.Exif()
                exif.load(block)
                _exif_values(exif, values)
            read.notes.append(PIXEL_LIMIT_EXIF_NOTE)
    except Exception as exc:  # noqa: BLE001 - Pillow raises many decoder-specific types; recorded as a failed read
        from .error_text import exception_text

        return None, f"EXIF 판독 실패: {type(exc).__name__}: {exception_text(exc, 160)}"
    metadata.update(values)
    read.exif_read = True
    return packet, None


# R18-6: the limitation of an image over Pillow's pixel-count limit (its EXIF was read without opening it).
PIXEL_LIMIT_EXIF_NOTE = "픽셀 수 상한 초과 이미지 — EXIF는 디코드 없이 파일 구조에서 읽었습니다(픽셀 디코드가 필요한 검사는 생략)"


def _exif_values(exif: Any, values: dict[str, str]) -> None:
    """The ``exif.*`` keys of a Pillow ``Exif`` (IFD0, Exif sub-IFD, GPS IFD) into ``values``."""
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


# R18-6: the largest EXIF block read without Pillow (a JPEG APP1 segment holds at most 65,533 bytes).
MAX_RAW_EXIF_BYTES = 1024 * 1024


def _raw_exif_block(path: Path, image_format: str | None) -> bytes | None:
    """R18-6: the file's EXIF block (TIFF-structured, "Exif\\0\\0" allowed) found without decoding — None when there is none.

    JPEG: the APP1 "Exif" segment before SOS; PNG: the eXIf chunk; WebP:
    the EXIF chunk; TIFF: the file's own header and IFDs (its first MiB).
    """
    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        if image_format == "tiff":
            return handle.read(MAX_RAW_EXIF_BYTES)
        if image_format == "jpeg":
            offset = 2
            for _ in range(MAX_WALKED_CHUNKS):
                handle.seek(offset)
                head = handle.read(4)
                if len(head) < 4 or head[0] != 0xFF:
                    return None
                marker = head[1]
                if marker == 0xFF:
                    offset += 1
                    continue
                if marker == 0xDA or marker == 0xD9:
                    return None
                if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
                    offset += 2
                    continue
                length = int.from_bytes(head[2:4], "big")
                if marker == 0xE1 and length > 8:
                    body = handle.read(length - 2)
                    if body.startswith(b"Exif\x00\x00"):
                        return body
                offset += 2 + length
            return None
        if image_format in ("png", "webp"):
            offset, wanted = (8, b"eXIf") if image_format == "png" else (12, b"EXIF")
            for _ in range(MAX_WALKED_CHUNKS):
                handle.seek(offset)
                header = handle.read(8)
                if len(header) < 8:
                    return None
                if image_format == "png":
                    length, kind, step = int.from_bytes(header[:4], "big"), header[4:8], 4
                else:
                    kind, length = header[:4], int.from_bytes(header[4:8], "little")
                    step = length % 2
                if offset + 8 + length > size:
                    return None
                if kind == wanted:
                    return handle.read(length) if length <= MAX_RAW_EXIF_BYTES else None
                if kind == b"IEND":
                    return None
                offset += 8 + length + step
    return None


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


# R18-4 (round 18): bounds of the PNG / WebP chunk walk — chunk headers
# visited (a 1 GB PNG in libpng's default 8 KiB IDAT chunks has ~131,000) and,
# per format, the metadata bytes read (``--metadata-bytes``, default 4 MiB).
MAX_WALKED_CHUNKS = 262_144


def _size_text(count: int) -> str:
    return f"{count / (1024 * 1024):g} MB" if count >= 1024 * 1024 else f"{count}바이트"


def metadata_prefix_reason(metadata_bytes: int, what: str) -> str:
    """R18-4: "메타데이터 앞 N바이트만 확인(--metadata-bytes) — <what>" (N in MB when ≥ 1 MiB)."""
    return f"메타데이터 앞 {_size_text(metadata_bytes)}만 확인(--metadata-bytes) — {what}"


@dataclass
class _PngWalk:
    data: bytes  # signature + every chunk read (IDAT and oversized chunks left out)
    complete: bool  # IEND reached
    partial: str | None  # why some metadata chunk was not read


# R19-7 (round 19): the chunks holding image data, skipped by seeking — IDAT and an APNG's frame data
# fdAT (it was read as metadata against --metadata-bytes: a 4-frame APNG without metadata was
# "메타데이터 청크를 4 MB까지만 읽음 … fdAT", and a tEXt after the frames was missed).
PNG_IMAGE_DATA_CHUNKS = (b"IDAT", b"fdAT")


def _walk_png(path: Path, *, budget: int) -> _PngWalk:
    """R18-4: the PNG's chunks through the whole file — image data (IDAT, R19-7: APNG fdAT) skipped by seeking, other chunks read.

    ``budget`` bounds the bytes of non-IDAT chunks read; a chunk over
    ``png.MAX_CHUNK_BYTES``, a chunk past the budget or more than
    :data:`MAX_WALKED_CHUNKS` chunks leave ``partial`` set (the rest of the
    walk still looks for IEND only when the chunk count allows it).
    """
    from .png import MAX_CHUNK_BYTES, PNG_SIGNATURE

    parts = [PNG_SIGNATURE]
    used = 0
    partial: str | None = None
    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        offset = len(PNG_SIGNATURE)
        for _ in range(MAX_WALKED_CHUNKS):
            handle.seek(offset)
            header = handle.read(8)
            if len(header) < 8:
                return _PngWalk(b"".join(parts), False, partial)
            length = int.from_bytes(header[:4], "big")
            kind = header[4:8]
            end = offset + 12 + length
            if end > size:
                return _PngWalk(b"".join(parts), False, partial)
            if kind == b"IEND":
                parts.append(header + handle.read(length + 4))
                return _PngWalk(b"".join(parts), True, partial)
            if kind == b"IHDR" and length <= 64:
                parts.append(header + handle.read(length + 4))  # the dimensions, outside the budget
            elif kind not in PNG_IMAGE_DATA_CHUNKS:
                name = kind.decode("latin-1")
                if length > MAX_CHUNK_BYTES:
                    partial = partial or f"메타데이터 청크 {name}({length}바이트)가 청크 상한({MAX_CHUNK_BYTES}바이트)을 넘어 읽지 않음"
                elif used + length > budget:
                    partial = partial or (
                        f"메타데이터 청크를 {_size_text(budget)}까지만 읽음(--metadata-bytes) — 이후 {name} 청크({length}바이트)를 읽지 않음"
                    )
                else:
                    parts.append(header + handle.read(length + 4))
                    used += length
            offset = end
    return _PngWalk(b"".join(parts), True, f"PNG 청크가 {MAX_WALKED_CHUNKS}개를 넘어 이후 청크의 메타데이터는 검사하지 않음")


def _walk_webp_xmp(path: Path, *, budget: int) -> tuple[bytes | None, str | None]:
    """R18-4: (XMP chunk body, partial reason) of a WebP — its RIFF chunks walked through the file."""
    with path.open("rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        offset = 12
        for _ in range(MAX_WALKED_CHUNKS):
            handle.seek(offset)
            header = handle.read(8)
            if len(header) < 8:
                return None, None
            kind = header[:4]
            length = int.from_bytes(header[4:8], "little")
            if offset + 8 + length > size:
                return None, None
            if kind == b"XMP ":
                if length > min(MAX_XMP_BYTES, budget):
                    return None, f"XMP 청크({length}바이트)가 상한을 넘어 읽지 않음"
                return handle.read(length), None
            offset += 8 + length + (length % 2)
    return None, f"WebP 청크가 {MAX_WALKED_CHUNKS}개를 넘어 이후 청크의 메타데이터는 검사하지 않음"


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
            key = _xmp_key(attribute)
            if key and value.strip():
                found.setdefault(key, []).append(value.strip())
        key = _xmp_key(element.tag)
        if key:
            texts = [text.strip() for text in element.itertext() if text and text.strip()]
            found.setdefault(key, []).extend(texts)
    return {
        key: "; ".join(dict.fromkeys(values))[: XMP_AIGC_MAX_CHARS if key == XMP_AIGC_KEY else MAX_EXIF_VALUE_CHARS]
        for key, values in found.items()
        if values
    }


def _xmp_key(name: str) -> str | None:
    qname = _split_qname(name)
    return XMP_FIELDS.get(qname) or (XMP_AIGC_KEY if qname[1] == XMP_AIGC_LOCAL_NAME else None)


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

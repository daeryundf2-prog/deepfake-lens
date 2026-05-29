from __future__ import annotations

import struct
import zlib


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_CHUNK_BYTES = 2 * 1024 * 1024


def read_png_metadata(data: bytes) -> dict[str, str]:
    if not data.startswith(PNG_SIGNATURE):
        return {}

    metadata: dict[str, str] = {}
    offset = len(PNG_SIGNATURE)
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big", signed=False)
        chunk_type = data[offset + 4 : offset + 8].decode("latin-1", errors="ignore")
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        next_offset = chunk_end + 4
        if length > MAX_CHUNK_BYTES or chunk_end > len(data) or next_offset > len(data):
            break

        chunk = data[chunk_start:chunk_end]
        parsed = None
        if chunk_type == "tEXt":
            parsed = _parse_text(chunk)
        elif chunk_type == "zTXt":
            parsed = _parse_compressed_text(chunk)
        elif chunk_type == "iTXt":
            parsed = _parse_international_text(chunk)
        elif chunk_type == "IEND":
            return metadata

        if parsed is not None:
            key, value = parsed
            metadata[f"png.{_normalize_key(key)}"] = value
        offset = next_offset
    return metadata


def read_png_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(PNG_SIGNATURE) or len(data) < 24:
        return None
    if data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _parse_text(chunk: bytes) -> tuple[str, str] | None:
    if b"\x00" not in chunk:
        return None
    key, value = chunk.split(b"\x00", 1)
    return _clean(key.decode("latin-1", errors="ignore"), value.decode("latin-1", errors="ignore"))


def _parse_compressed_text(chunk: bytes) -> tuple[str, str] | None:
    parts = chunk.split(b"\x00", 1)
    if len(parts) != 2 or len(parts[1]) < 2:
        return None
    key, rest = parts
    compression_method = rest[0]
    if compression_method != 0:
        return None
    decompressed = _decompress_limited(rest[1:])
    if decompressed is None:
        return None
    value = decompressed.decode("utf-8", errors="replace")
    return _clean(key.decode("latin-1", errors="ignore"), value)


def _parse_international_text(chunk: bytes) -> tuple[str, str] | None:
    cursor = 0
    keyword_end = chunk.find(b"\x00", cursor)
    if keyword_end <= 0 or keyword_end + 3 > len(chunk):
        return None
    key = chunk[:keyword_end].decode("latin-1", errors="ignore")
    compression_flag = chunk[keyword_end + 1]
    compression_method = chunk[keyword_end + 2]
    cursor = keyword_end + 3

    language_end = chunk.find(b"\x00", cursor)
    if language_end < 0:
        return None
    cursor = language_end + 1
    translated_end = chunk.find(b"\x00", cursor)
    if translated_end < 0:
        return None
    cursor = translated_end + 1

    text_bytes = chunk[cursor:]
    if compression_flag == 0:
        value = text_bytes.decode("utf-8", errors="replace")
    elif compression_flag == 1 and compression_method == 0:
        decompressed = _decompress_limited(text_bytes)
        if decompressed is None:
            return None
        value = decompressed.decode("utf-8", errors="replace")
    else:
        return None
    return _clean(key, value)


def _clean(key: str, value: str) -> tuple[str, str] | None:
    key = key.strip()
    value = value.strip()
    if not key or not value:
        return None
    return key, value


def _decompress_limited(data: bytes) -> bytes | None:
    decompressor = zlib.decompressobj()
    try:
        output = decompressor.decompress(data, MAX_CHUNK_BYTES + 1)
        if decompressor.unconsumed_tail:
            return None
        remaining = MAX_CHUNK_BYTES + 1 - len(output)
        if remaining <= 0:
            return None
        output += decompressor.flush(remaining)
    except zlib.error:
        return None
    if len(output) > MAX_CHUNK_BYTES or not decompressor.eof:
        return None
    return output


def _normalize_key(key: str) -> str:
    return "_".join(key.strip().lower().split())

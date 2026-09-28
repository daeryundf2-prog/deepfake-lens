"""Image metadata readers and generator-tool fingerprints.

Extracted from ``core`` — header sniffing, JPEG/WebP/TIFF dimension
parsing, and AI-generator metadata pattern matching.
"""


from __future__ import annotations

import re
import struct
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


def read_image_metadata(path: Path, *, metadata_bytes: int = DEFAULT_METADATA_BYTES) -> tuple[dict[str, str], tuple[int, int] | None]:
    data = _read_prefix(path, metadata_bytes)
    metadata: dict[str, str] = {}
    dimensions: tuple[int, int] | None = None

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        metadata.update(read_png_metadata(data))
        dimensions = read_png_dimensions(data)
    elif data.startswith(b"\xff\xd8"):
        dimensions = _read_jpeg_dimensions(data)
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        dimensions = _read_webp_dimensions(data)
    elif data.startswith(b"BM") and len(data) >= 26:
        width, height = struct.unpack_from("<ii", data, 18)
        if width > 0:
            dimensions = (width, abs(height))
    elif data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        dimensions = struct.unpack_from("<HH", data, 6)
    elif data[:4] in (b"II*\x00", b"MM\x00*"):
        dimensions = _read_tiff_dimensions(data)

    header_text = _extract_header_text(data)
    if header_text:
        metadata["header.text"] = header_text
    return metadata, dimensions


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
        return SourceGuess("Stable Diffusion / A1111 추정", SourceConfidence.HIGH, ["프롬프트, steps, sampler, CFG, seed 같은 A1111 생성 파라미터가 발견되었습니다."])

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

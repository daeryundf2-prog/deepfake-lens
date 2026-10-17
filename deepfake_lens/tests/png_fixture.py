"""A small PNG writer for tests that must run without Pillow (the API venv has no Pillow on some hosts)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def write_png(path: Path, text: dict[str, str] | None = None, *, size: tuple[int, int] = (64, 48), seed: int = 0) -> Path:
    """An RGB PNG with optional ``tEXt`` chunks (Latin-1 keys and values, as PNG tEXt requires)."""
    width, height = size

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    rows = b"".join(b"\x00" + bytes(((x * 7 + y * 3 + seed) % 256) for x in range(width) for _ in range(3)) for y in range(height))
    texts = b"".join(chunk(b"tEXt", key.encode("latin-1") + b"\x00" + value.encode("latin-1")) for key, value in (text or {}).items())
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + texts + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    return path

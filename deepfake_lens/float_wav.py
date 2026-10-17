"""32-bit float WAV reader for the files ffmpeg writes (``pcm_f32le``) — shared by TB-2 and TB-4 (no soundfile needed)."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any


def read_float_wav(path: Path) -> tuple[Any, int]:
    """(samples [frames, channels] float32, rate) of a 32-bit float WAV written by ffmpeg (pcm_f32le)."""
    import numpy as np

    data = path.read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("WAV 머리가 아님")
    pos, channels, rate, fmt, bits = 12, 0, 0, 0, 0
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack_from("<I", data, pos + 4)[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt, channels, rate = struct.unpack_from("<HHI", body, 0)
            bits = struct.unpack_from("<H", body, 14)[0]
            if fmt == 0xFFFE and len(body) >= 26:
                fmt = struct.unpack_from("<H", body, 24)[0]
        elif cid == b"data":
            if fmt != 3 or bits != 32 or channels < 1:
                raise ValueError(f"예상하지 않은 WAV 형식(format {fmt}, {bits}비트)")
            usable = len(body) - len(body) % (4 * channels)
            return np.frombuffer(body[:usable], dtype="<f4").reshape(-1, channels), rate
        pos += 8 + size + (size & 1)
    raise ValueError("WAV data 청크 없음")


__all__ = ["read_float_wav"]

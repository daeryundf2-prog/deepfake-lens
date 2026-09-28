"""Raster loading, PNG unfiltering, and low-level luminance math.

Extracted from ``pixel.py`` — this module owns the decode layer and the
numeric primitives the experts share; ``pixel.py`` keeps the expert
implementations and fusion. Kept dependency-free (stdlib zlib/struct) so
the decode path works without Pillow.
"""


from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MAX_DECOMPRESSED_IMAGE_BYTES = 96 * 1024 * 1024


@dataclass(frozen=True)
class PixelRaster:
    width: int
    height: int
    pixels: tuple[tuple[int, int, int], ...]
    source: str


def _load_raster(path: Path, *, max_side: int) -> tuple[PixelRaster | None, list[str]]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, [f"이미지 픽셀을 읽지 못했습니다: {exc}"]

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        raster, limitation = _load_png_raster(data, max_side=max_side)
        if raster:
            return raster, []
        return None, [limitation or "지원하지 않는 PNG 픽셀 형식입니다."]

    pillow_raster, pillow_limitations = _load_with_optional_pillow(path, max_side=max_side)
    return pillow_raster, pillow_limitations


def _load_with_optional_pillow(path: Path, *, max_side: int) -> tuple[PixelRaster | None, list[str]]:
    try:
        from PIL import Image
    except ImportError:
        return None, ["PNG가 아닌 이미지는 Pillow가 설치되어 있을 때만 픽셀 분석할 수 있습니다."]

    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side))
            width, height = image.size
            if hasattr(image, "get_flattened_data"):
                raw = image.get_flattened_data()
                if raw and isinstance(raw[0], (tuple, list)):
                    pixels = tuple((int(p[0]), int(p[1]), int(p[2])) for p in raw)
                else:
                    pixels = tuple((int(raw[i]), int(raw[i + 1]), int(raw[i + 2])) for i in range(0, len(raw), 3))
            else:
                pixels = tuple((int(r), int(g), int(b)) for r, g, b in image.getdata())
            return PixelRaster(width, height, pixels, "pillow"), []
    except Exception as exc:  # pragma: no cover - depends on optional Pillow codecs
        return None, [f"Pillow로 이미지 픽셀을 읽지 못했습니다: {exc}"]


def _load_png_raster(data: bytes, *, max_side: int) -> tuple[PixelRaster | None, str | None]:
    if len(data) < 33:
        return None, "PNG 파일이 너무 짧습니다."

    offset = 8
    width = height = bit_depth = color_type = interlace = None
    idat_parts: list[bytes] = []
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        chunk_start = offset + 8
        chunk_end = chunk_start + length
        next_offset = chunk_end + 4
        if length < 0 or chunk_end > len(data) or next_offset > len(data):
            return None, "PNG 청크 구조가 손상되었습니다."
        chunk = data[chunk_start:chunk_end]
        if chunk_type == b"IHDR":
            if len(chunk) != 13:
                return None, "PNG IHDR 청크가 손상되었습니다."
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk)
        elif chunk_type == b"IEND":
            break
        offset = next_offset

    if not width or not height or bit_depth != 8 or interlace != 0:
        return None, "8비트 비인터레이스 PNG만 픽셀 분석을 지원합니다."
    if color_type not in {0, 2, 4, 6}:
        return None, "팔레트 PNG 등 일부 색상 형식은 아직 픽셀 분석 대상이 아닙니다."

    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    row_bytes = width * channels
    expected = height * (row_bytes + 1)
    if expected > MAX_DECOMPRESSED_IMAGE_BYTES:
        return None, "픽셀 분석 안전 한도를 넘는 큰 PNG입니다."

    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(b"".join(idat_parts), expected + 1)
        raw += decompressor.flush(max(0, expected + 1 - len(raw)))
    except zlib.error:
        return None, "PNG 픽셀 압축을 해제하지 못했습니다."
    if len(raw) != expected or not decompressor.eof:
        return None, "PNG 픽셀 데이터 크기가 예상과 다릅니다."

    # Sample rows/pixels while decoding so memory stays proportional to the
    # output raster instead of the full image (a full-resolution tuple raster
    # of a max-size PNG can approach the gigabyte range).
    step = 1
    if max(width, height) > max_side:
        step = int(math.ceil(max(width, height) / max_side))
    out_width = max(1, width // step)
    out_height = max(1, height // step)
    kept_rows = {min(height - 1, y * step) for y in range(out_height)}

    pixels: list[tuple[int, int, int]] = []
    cursor = 0
    previous = bytearray(row_bytes)
    for row_index in range(height):
        filter_type = raw[cursor]
        cursor += 1
        scanline = bytearray(raw[cursor : cursor + row_bytes])
        cursor += row_bytes
        if filter_type == 0:
            reconstructed = scanline
        elif filter_type == 1:
            reconstructed = _unfilter_sub(scanline, channels)
        elif filter_type == 2:
            reconstructed = _unfilter_up(scanline, previous)
        elif filter_type == 3:
            reconstructed = _unfilter_average(scanline, previous, channels)
        elif filter_type == 4:
            reconstructed = _unfilter_paeth(scanline, previous, channels)
        else:
            return None, "지원하지 않는 PNG 필터입니다."
        if row_index in kept_rows:
            for source_x in (min(width - 1, x * step) for x in range(out_width)):
                index = source_x * channels
                if color_type in {0, 4}:
                    value = reconstructed[index]
                    pixels.append((value, value, value))
                else:
                    pixels.append((reconstructed[index], reconstructed[index + 1], reconstructed[index + 2]))
        previous = reconstructed

    return PixelRaster(out_width, out_height, tuple(pixels), "png" if step == 1 else "png-sampled"), None


def _unfilter_sub(scanline: bytearray, bpp: int) -> bytearray:
    for index in range(len(scanline)):
        left = scanline[index - bpp] if index >= bpp else 0
        scanline[index] = (scanline[index] + left) & 0xFF
    return scanline


def _unfilter_up(scanline: bytearray, previous: bytearray) -> bytearray:
    for index, up in enumerate(previous):
        scanline[index] = (scanline[index] + up) & 0xFF
    return scanline


def _unfilter_average(scanline: bytearray, previous: bytearray, bpp: int) -> bytearray:
    for index, up in enumerate(previous):
        left = scanline[index - bpp] if index >= bpp else 0
        scanline[index] = (scanline[index] + ((left + up) // 2)) & 0xFF
    return scanline


def _unfilter_paeth(scanline: bytearray, previous: bytearray, bpp: int) -> bytearray:
    for index, up in enumerate(previous):
        left = scanline[index - bpp] if index >= bpp else 0
        up_left = previous[index - bpp] if index >= bpp else 0
        scanline[index] = (scanline[index] + _paeth(left, up, up_left)) & 0xFF
    return scanline


def _paeth(left: int, up: int, up_left: int) -> int:
    estimate = left + up - up_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    up_left_distance = abs(estimate - up_left)
    if left_distance <= up_distance and left_distance <= up_left_distance:
        return left
    if up_distance <= up_left_distance:
        return up
    return up_left


def _luminance_values(raster: PixelRaster) -> list[float]:
    return [0.2126 * red + 0.7152 * green + 0.0722 * blue for red, green, blue in raster.pixels]


def _edge_values(luminance: list[float], width: int, height: int) -> list[float]:
    values: list[float] = []
    for y in range(height):
        row = y * width
        for x in range(width):
            current = luminance[row + x]
            if x + 1 < width:
                values.append(abs(current - luminance[row + x + 1]))
            if y + 1 < height:
                values.append(abs(current - luminance[row + width + x]))
    return values or [0.0]


def _basic_stats(values: Iterable[float]) -> tuple[float, float]:
    data = list(values)
    if not data:
        return 0.0, 0.0
    average = sum(data) / len(data)
    variance = sum((value - average) ** 2 for value in data) / len(data)
    return average, math.sqrt(variance)


def _tile_edge_score(luminance: list[float], width: int, height: int, x0: int, y0: int, tile_size: int) -> float:
    values: list[float] = []
    x1 = min(width, x0 + tile_size)
    y1 = min(height, y0 + tile_size)
    for y in range(y0, y1):
        for x in range(x0, x1):
            current = luminance[y * width + x]
            if x + 1 < x1:
                values.append(abs(current - luminance[y * width + x + 1]))
            if y + 1 < y1:
                values.append(abs(current - luminance[(y + 1) * width + x]))
    average, stdev = _basic_stats(values)
    return average + 0.35 * stdev


def _normalize_grid(grid: list[list[int]], tile_scores: list[float]) -> list[list[int]]:
    if not grid:
        return []
    minimum = min(tile_scores) if tile_scores else 0.0
    maximum = max(tile_scores) if tile_scores else 0.0
    cursor = 0
    normalized: list[list[int]] = []
    for row in grid:
        normalized_row = []
        for _ in row:
            value = tile_scores[cursor]
            cursor += 1
            normalized_row.append(int(round(255 * (value - minimum) / max(1.0, maximum - minimum))))
        normalized.append(normalized_row)
    return normalized


def _neighbor_correlation(luminance: list[float], width: int, height: int, dx: int, dy: int) -> float:
    pairs: list[tuple[float, float]] = []
    for y in range(0, height - dy):
        row = y * width
        shifted_row = (y + dy) * width
        for x in range(0, width - dx):
            pairs.append((luminance[row + x], luminance[shifted_row + x + dx]))
    if len(pairs) < 2:
        return 0.0
    left_values = [left for left, _ in pairs]
    right_values = [right for _, right in pairs]
    left_average, left_stdev = _basic_stats(left_values)
    right_average, right_stdev = _basic_stats(right_values)
    if left_stdev == 0 or right_stdev == 0:
        return 1.0
    covariance = sum((left - left_average) * (right - right_average) for left, right in pairs) / len(pairs)
    return _clamp(covariance / (left_stdev * right_stdev), -1.0, 1.0)


def _box_count_fractal_dimension(luminance: list[float], width: int, height: int) -> float:
    if width < 16 or height < 16:
        return 0.0
    average, stdev = _basic_stats(luminance)
    threshold = average + 0.25 * stdev
    sizes = [size for size in (2, 4, 8, 16, 32) if size < min(width, height)]
    points: list[tuple[float, float]] = []
    for size in sizes:
        boxes = 0
        for y in range(0, height, size):
            for x in range(0, width, size):
                has_high = False
                has_low = False
                for yy in range(y, min(height, y + size)):
                    row = yy * width
                    for xx in range(x, min(width, x + size)):
                        if luminance[row + xx] >= threshold:
                            has_high = True
                        else:
                            has_low = True
                        if has_high and has_low:
                            boxes += 1
                            break
                    if has_high and has_low:
                        break
        if boxes > 0:
            points.append((math.log(1.0 / size), math.log(boxes)))
    if len(points) < 2:
        return 0.0
    x_average = sum(x for x, _ in points) / len(points)
    y_average = sum(y for _, y in points) / len(points)
    denominator = sum((x - x_average) ** 2 for x, _ in points)
    if denominator == 0:
        return 0.0
    slope = sum((x - x_average) * (y - y_average) for x, y in points) / denominator
    return abs(slope)


def _boundary_jump_score(luminance: list[float], width: int, height: int, tile_size: int) -> float:
    boundary_values: list[float] = []
    interior_values: list[float] = []
    for y in range(height):
        for x in range(width):
            current = luminance[y * width + x]
            is_boundary = x % tile_size in {0, tile_size - 1} or y % tile_size in {0, tile_size - 1}
            target = boundary_values if is_boundary else interior_values
            if x + 1 < width:
                target.append(abs(current - luminance[y * width + x + 1]))
            if y + 1 < height:
                target.append(abs(current - luminance[(y + 1) * width + x]))
    boundary_mean, _ = _basic_stats(boundary_values)
    interior_mean, _ = _basic_stats(interior_values)
    return boundary_mean / max(1.0, interior_mean)


def _euclidean(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _shift_difference(luminance: list[float], width: int, height: int, dx: int, dy: int) -> float:
    values: list[float] = []
    for y in range(0, height - dy):
        row = y * width
        shifted_row = (y + dy) * width
        for x in range(0, width - dx):
            values.append(abs(luminance[row + x] - luminance[shifted_row + x + dx]))
    return sum(values) / max(1, len(values))


def _quantized_luminance_ratio(luminance: list[float]) -> float:
    if not luminance:
        return 0.0
    buckets: dict[int, int] = {}
    for value in luminance:
        bucket = int(round(value / 8.0))
        buckets[bucket] = buckets.get(bucket, 0) + 1
    common = sorted(buckets.values(), reverse=True)[:8]
    return sum(common) / len(luminance)


def _write_png_heatmap(path: Path, grid: list[list[int]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    height = len(grid)
    width = max((len(row) for row in grid), default=0)
    raw_rows = []
    for row in grid:
        padded = row + [0] * (width - len(row))
        raw_rows.append(b"\x00" + bytes(max(0, min(255, value)) for value in padded))
    raw = b"".join(raw_rows)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x00\x00\x00\x00")
        + _png_chunk(b"IDAT", zlib.compress(raw))
        + _png_chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    return path


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    import binascii

    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return len(payload).to_bytes(4, "big") + kind + payload + checksum.to_bytes(4, "big")

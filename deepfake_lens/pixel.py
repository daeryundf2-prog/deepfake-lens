from __future__ import annotations

import math
import json
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


PIXEL_MODEL_NAME = "local-multiexpert-pixel-v1"
SUPPORTED_PIXEL_MODES = {"off", "fast", "deep"}
DEFAULT_PIXEL_MAX_SIDE = 192
MAX_DECOMPRESSED_IMAGE_BYTES = 96 * 1024 * 1024


@dataclass(frozen=True)
class PixelExpertResult:
    name: str
    family: str
    score: int
    weight: float
    available: bool
    detail: str
    reference: str = ""
    implementation: str = "local"


@dataclass(frozen=True)
class PixelAnalysis:
    mode: str
    available: bool
    score: int
    confidence: str
    model: str
    experts: list[PixelExpertResult] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    fusion: str = "weighted_mean"
    evidence_chain: list[str] = field(default_factory=list)
    implemented_references: list[str] = field(default_factory=list)
    heatmap_path: str | None = None


@dataclass(frozen=True)
class PixelRaster:
    width: int
    height: int
    pixels: tuple[tuple[int, int, int], ...]
    source: str


def analyze_image_pixels(
    path: Path | str,
    *,
    mode: str = "fast",
    max_side: int = DEFAULT_PIXEL_MAX_SIDE,
    heatmap_path: Path | None = None,
) -> PixelAnalysis:
    if mode not in SUPPORTED_PIXEL_MODES:
        raise ValueError(f"unsupported pixel mode: {mode}")
    if mode == "off":
        return PixelAnalysis(mode, False, 0, "off", PIXEL_MODEL_NAME, limitations=["픽셀 분석이 꺼져 있습니다."])

    image_path = Path(path)
    raster, load_limitations = _load_raster(image_path, max_side=max(16, max_side))
    if raster is None:
        return PixelAnalysis(mode, False, 0, "unavailable", PIXEL_MODEL_NAME, limitations=load_limitations)

    if raster.width < 8 or raster.height < 8:
        return PixelAnalysis(
            mode,
            False,
            0,
            "unavailable",
            PIXEL_MODEL_NAME,
            limitations=load_limitations + ["픽셀 분석을 하기에는 이미지가 너무 작습니다."],
        )

    luminance = _luminance_values(raster)
    edge_values = _edge_values(luminance, raster.width, raster.height)
    stats = _basic_stats(luminance)
    edge_stats = _basic_stats(edge_values)
    experts = [
        _pixel_baseline_expert(raster, luminance, stats, edge_stats),
        _spectral_statistics_expert(raster, luminance, stats, edge_stats),
        _difference_in_difference_expert(raster, luminance, stats),
        _spark_il_retrieval_expert(raster, luminance, stats, edge_stats),
        _low_correlation_fractal_expert(raster, luminance, stats, edge_stats),
        _alpha_blending_expert(raster, luminance),
        _vrag_dfd_expert(raster, luminance, stats, edge_stats),
        _ivy_xdetector_adapter(image_path),
    ]

    heatmap_grid: list[list[int]] | None = None
    if mode == "deep":
        localization, heatmap_grid = _safe_localization_expert(raster, luminance)
        experts.append(localization)

    available_experts = [expert for expert in experts if expert.available]
    if not available_experts:
        return PixelAnalysis(mode, False, 0, "unavailable", PIXEL_MODEL_NAME, experts=experts, limitations=load_limitations)

    fused, fusion_detail = _fuzzy_decision_tree_fusion(available_experts)
    experts.append(
        PixelExpertResult(
            "fuzzy_decision_tree_fusion",
            "fusion",
            fused,
            0.0,
            True,
            fusion_detail,
            "Rethinking AI-Generated Image Detection with Fuzzy Decision Tree",
        )
    )
    confidence = _confidence_for(fused, len(available_experts), mode)
    signals = [expert.detail for expert in available_experts if expert.score >= 45]
    evidence_chain = _reveal_evidence_chain(experts)
    agentfox_summary = _agentfox_explainable_summary(available_experts, fused, confidence)
    limitations = load_limitations + [
        "픽셀 분석은 로컬 multi-expert 앙상블입니다. 학습된 딥페이크 모델의 확률값으로 해석하면 안 됩니다.",
        "메타데이터가 제거된 파일도 볼 수 있지만, 카메라 원본/편집본/압축본을 구분하지 못할 수 있습니다.",
        agentfox_summary,
    ]

    written_heatmap = None
    if heatmap_path and heatmap_grid:
        written_heatmap = str(_write_png_heatmap(heatmap_path, heatmap_grid))

    return PixelAnalysis(
        mode=mode,
        available=True,
        score=fused,
        confidence=confidence,
        model=PIXEL_MODEL_NAME,
        experts=experts,
        signals=signals,
        limitations=limitations,
        fusion="fuzzy_decision_tree_v0",
        evidence_chain=evidence_chain,
        implemented_references=_implemented_references(mode),
        heatmap_path=written_heatmap,
    )


def _load_raster(path: Path, *, max_side: int) -> tuple[PixelRaster | None, list[str]]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, [f"이미지 픽셀을 읽지 못했습니다: {exc}"]

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        raster, limitation = _load_png_raster(data)
        if raster:
            return _downsample_raster(raster, max_side), []
        return None, [limitation or "지원하지 않는 PNG 픽셀 형식입니다."]

    pillow_raster, pillow_limitations = _load_with_optional_pillow(path, max_side=max_side)
    return pillow_raster, pillow_limitations


def _load_with_optional_pillow(path: Path, *, max_side: int) -> tuple[PixelRaster | None, list[str]]:
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None, ["PNG가 아닌 이미지는 Pillow가 설치되어 있을 때만 픽셀 분석할 수 있습니다."]

    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side))
            width, height = image.size
            pixels = tuple((int(r), int(g), int(b)) for r, g, b in image.getdata())
            return PixelRaster(width, height, pixels, "pillow"), []
    except Exception as exc:  # pragma: no cover - depends on optional Pillow codecs
        return None, [f"Pillow로 이미지 픽셀을 읽지 못했습니다: {exc}"]


def _load_png_raster(data: bytes) -> tuple[PixelRaster | None, str | None]:
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

    rows: list[bytearray] = []
    cursor = 0
    previous = bytearray(row_bytes)
    for _ in range(height):
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
        rows.append(reconstructed)
        previous = reconstructed

    pixels: list[tuple[int, int, int]] = []
    for row in rows:
        for index in range(0, row_bytes, channels):
            if color_type == 0:
                value = row[index]
                pixels.append((value, value, value))
            elif color_type == 2:
                pixels.append((row[index], row[index + 1], row[index + 2]))
            elif color_type == 4:
                value = row[index]
                pixels.append((value, value, value))
            else:
                pixels.append((row[index], row[index + 1], row[index + 2]))
    return PixelRaster(width, height, tuple(pixels), "png"), None


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


def _downsample_raster(raster: PixelRaster, max_side: int) -> PixelRaster:
    if max(raster.width, raster.height) <= max_side:
        return raster
    step = int(math.ceil(max(raster.width, raster.height) / max_side))
    width = max(1, raster.width // step)
    height = max(1, raster.height // step)
    pixels = []
    for y in range(height):
        source_y = min(raster.height - 1, y * step)
        row_start = source_y * raster.width
        for x in range(width):
            source_x = min(raster.width - 1, x * step)
            pixels.append(raster.pixels[row_start + source_x])
    return PixelRaster(width, height, tuple(pixels), f"{raster.source}-sampled")


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


def _pixel_baseline_expert(
    raster: PixelRaster,
    luminance: list[float],
    stats: tuple[float, float],
    edge_stats: tuple[float, float],
) -> PixelExpertResult:
    _, stdev = stats
    edge_mean, _ = edge_stats
    sample_step = max(1, len(raster.pixels) // 8192)
    unique_ratio = len(set(raster.pixels[::sample_step])) / max(1, len(raster.pixels[::sample_step]))
    quantized = _quantized_luminance_ratio(luminance)

    score = 0
    detail = "전역 픽셀 분포에서 강한 합성 단서를 찾지 못했습니다."
    if unique_ratio < 0.08 and stdev > 12:
        score = 62
        detail = "색상 종류가 비정상적으로 적은데 대비가 커서 그래픽/합성 패턴 가능성이 있습니다."
    elif quantized > 0.72 and stdev > 18:
        score = 56
        detail = "밝기 값이 특정 구간에 과도하게 몰려 양자화된 생성/편집 흔적일 수 있습니다."
    elif edge_mean < 1.8 and stdev > 10:
        score = 48
        detail = "대비는 있는데 이웃 픽셀 변화가 지나치게 낮아 과도한 평활화 신호가 있습니다."
    elif edge_mean > 58 and stdev > 35:
        score = 46
        detail = "이웃 픽셀 변화가 강해 고주파 합성/리샘플링 흔적을 추가 확인해야 합니다."
    return PixelExpertResult("pixel_baseline", "pixel", score, 0.28, True, detail)


def _spectral_statistics_expert(
    raster: PixelRaster,
    luminance: list[float],
    stats: tuple[float, float],
    edge_stats: tuple[float, float],
) -> PixelExpertResult:
    _, stdev = stats
    edge_mean, _ = edge_stats
    high_frequency_ratio = edge_mean / max(1.0, stdev)
    periodicity_score, periodicity_detail = _periodicity_signal(raster, luminance)

    score = 0
    detail = "주파수 대리 통계에서 강한 반복/고주파 신호를 찾지 못했습니다."
    if periodicity_score:
        score = periodicity_score
        detail = periodicity_detail
    elif high_frequency_ratio > 1.45 and edge_mean > 38:
        score = 70
        detail = "고주파 에너지가 전체 대비에 비해 커서 격자/업스케일/합성 흔적 후보입니다."
    elif high_frequency_ratio < 0.16 and stdev > 16:
        score = 52
        detail = "전체 대비는 있지만 고주파 변화가 낮아 생성 모델 특유의 매끈한 표면 후보입니다."
    return PixelExpertResult("spectral_statistics", "spectral", score, 0.30, True, detail)


def _difference_in_difference_expert(
    raster: PixelRaster,
    luminance: list[float],
    stats: tuple[float, float],
) -> PixelExpertResult:
    _, stdev = stats
    if raster.width < 16 or raster.height < 16:
        return PixelExpertResult(
            "difference_in_difference_reconstruction",
            "reconstruction",
            0,
            0.22,
            False,
            "이미지가 작아 재구성 차이를 안정적으로 볼 수 없습니다.",
            "A Difference-in-Difference Approach to Detecting AI-Generated Images",
        )

    residuals: list[float] = []
    second_pass_residuals: list[float] = []
    for y in range(0, raster.height - 1, 2):
        for x in range(0, raster.width - 1, 2):
            indexes = [
                y * raster.width + x,
                y * raster.width + x + 1,
                (y + 1) * raster.width + x,
                (y + 1) * raster.width + x + 1,
            ]
            block = [luminance[index] for index in indexes]
            average = sum(block) / 4.0
            residuals.extend(abs(value - average) for value in block)
            corner_average = (block[0] + block[3]) / 2.0
            second_pass_residuals.extend(abs(value - corner_average) for value in block)
    residual_mean, residual_stdev = _basic_stats(residuals)
    second_mean, _ = _basic_stats(second_pass_residuals)
    did_gap = abs(second_mean - residual_mean)

    score = 0
    detail = "difference-in-difference 재구성 차이가 일반 범위에 있습니다."
    if residual_mean < 1.2 and stdev > 12:
        score = 58
        detail = "2x2 재구성 차이가 매우 낮아 과평활/생성 표면 후보입니다."
    elif residual_mean > 36 and residual_stdev < 12:
        score = 54
        detail = "재구성 잔차가 전역적으로 균일하게 높아 반복적인 합성 노이즈 후보입니다."
    elif residual_mean > 48 or did_gap > 18:
        score = 50
        detail = "재구성 잔차 또는 DID gap이 커서 강한 리샘플링/합성 고주파 후보입니다."
    return PixelExpertResult(
        "difference_in_difference_reconstruction",
        "reconstruction",
        score,
        0.22,
        True,
        detail,
        "A Difference-in-Difference Approach to Detecting AI-Generated Images",
    )


def _spark_il_retrieval_expert(
    raster: PixelRaster,
    luminance: list[float],
    stats: tuple[float, float],
    edge_stats: tuple[float, float],
) -> PixelExpertResult:
    _, stdev = stats
    edge_mean, edge_stdev = edge_stats
    quantized = _quantized_luminance_ratio(luminance)
    periodicity_score, _ = _periodicity_signal(raster, luminance)
    feature = (
        min(1.0, stdev / 80.0),
        min(1.0, edge_mean / 80.0),
        min(1.0, edge_stdev / 80.0),
        min(1.0, quantized),
        min(1.0, periodicity_score / 100.0),
    )
    prototypes = {
        "synthetic_grid": (0.65, 0.82, 0.72, 0.80, 0.72),
        "synthetic_smooth": (0.34, 0.12, 0.18, 0.74, 0.10),
        "camera_like": (0.48, 0.38, 0.52, 0.38, 0.04),
    }
    distances = {name: _euclidean(feature, prototype) for name, prototype in prototypes.items()}
    nearest = min(distances, key=distances.get)
    synthetic_distance = min(distances["synthetic_grid"], distances["synthetic_smooth"])
    camera_distance = distances["camera_like"]
    score = int(round(_clamp((camera_distance - synthetic_distance + 0.35) / 0.9, 0.0, 1.0) * 78))
    detail = "SPARK-IL 스타일 검색 프로파일에서 카메라형 특징에 더 가깝습니다."
    if score >= 45:
        detail = f"SPARK-IL 스타일 검색 프로파일이 {nearest} 합성 기준점에 더 가깝습니다."
    return PixelExpertResult(
        "spark_il_spectral_retrieval",
        "retrieval",
        score,
        0.18,
        True,
        detail,
        "SPARK-IL spectral retrieval and incremental learning",
        "local_prototype_retrieval",
    )


def _low_correlation_fractal_expert(
    raster: PixelRaster,
    luminance: list[float],
    stats: tuple[float, float],
    edge_stats: tuple[float, float],
) -> PixelExpertResult:
    _, stdev = stats
    edge_mean, _ = edge_stats
    horizontal = _neighbor_correlation(luminance, raster.width, raster.height, 1, 0)
    vertical = _neighbor_correlation(luminance, raster.width, raster.height, 0, 1)
    fractal_dimension = _box_count_fractal_dimension(luminance, raster.width, raster.height)
    correlation = (horizontal + vertical) / 2.0
    roughness = edge_mean / max(1.0, stdev)

    score = 0
    detail = "저상관/프랙탈 통계가 일반 범위에 있습니다."
    if correlation < 0.18 and fractal_dimension > 1.72 and edge_mean > 22:
        score = 68
        detail = f"픽셀 상관이 낮고 fractal dimension={fractal_dimension:.2f}로 높아 합성 노이즈 후보입니다."
    elif correlation < 0.28 and roughness > 0.9:
        score = 54
        detail = f"이웃 픽셀 상관이 낮고 roughness={roughness:.2f}라 저상관 생성 신호를 확인할 만합니다."
    elif fractal_dimension < 1.08 and stdev > 18:
        score = 46
        detail = f"fractal dimension={fractal_dimension:.2f}로 낮아 과평활/편집 표면 후보입니다."
    return PixelExpertResult(
        "low_correlation_fractal_signal",
        "statistical",
        score,
        0.16,
        True,
        detail,
        "Low-Correlation Signal Detection for AI-Generated Image Identification",
    )


def _alpha_blending_expert(raster: PixelRaster, luminance: list[float]) -> PixelExpertResult:
    if raster.width < 24 or raster.height < 24:
        return PixelExpertResult(
            "alpha_blending_compositing",
            "compositing",
            0,
            0.16,
            False,
            "이미지가 작아 alpha-blending 경계 신호를 안정적으로 볼 수 없습니다.",
            "Training Detectors with Real-Only Data via Alpha Blending",
        )

    tile_size = max(8, min(20, min(raster.width, raster.height) // 8))
    tile_scores = [
        _tile_edge_score(luminance, raster.width, raster.height, x, y, tile_size)
        for y in range(0, raster.height, tile_size)
        for x in range(0, raster.width, tile_size)
    ]
    average, stdev = _basic_stats(tile_scores)
    high_tiles = sum(1 for value in tile_scores if value > average + 1.4 * stdev)
    high_ratio = high_tiles / max(1, len(tile_scores))
    boundary_jump = _boundary_jump_score(luminance, raster.width, raster.height, tile_size)

    score = 0
    detail = "alpha-blending/합성 경계 후보가 두드러지지 않습니다."
    if 0.04 <= high_ratio <= 0.22 and boundary_jump > 1.65:
        score = 66
        detail = "일부 타일 경계에서 주변보다 큰 잔차가 보여 alpha-blending/부분 합성 후보입니다."
    elif boundary_jump > 1.35 and stdev > 5:
        score = 49
        detail = "국소 경계 변화가 있어 alpha-blending 합성 여부를 추가 확인할 만합니다."
    return PixelExpertResult(
        "alpha_blending_compositing",
        "compositing",
        score,
        0.16,
        True,
        detail,
        "Rethinking Deepfake Detection: Training Detectors with Real-Only Data via Alpha Blending",
    )


def _vrag_dfd_expert(
    raster: PixelRaster,
    luminance: list[float],
    stats: tuple[float, float],
    edge_stats: tuple[float, float],
) -> PixelExpertResult:
    _, stdev = stats
    edge_mean, edge_stdev = edge_stats
    quantized = _quantized_luminance_ratio(luminance)
    periodicity_score, periodicity_detail = _periodicity_signal(raster, luminance)
    evidence = []
    if periodicity_score:
        evidence.append(periodicity_detail)
    if quantized > 0.68 and stdev > 15:
        evidence.append("밝기 분포가 소수 버킷에 몰립니다.")
    if edge_stdev > edge_mean * 1.6 and edge_mean > 18:
        evidence.append("edge residual 분포가 넓어 국소 합성 후보와 유사합니다.")

    score = min(74, 22 + 16 * len(evidence)) if evidence else 0
    detail = "VRAG-DFD 로컬 검색 근거와 일치하는 합성 사례가 부족합니다."
    if evidence:
        detail = "VRAG-DFD 스타일 근거 검색: " + " / ".join(evidence[:3])
    return PixelExpertResult(
        "vrag_dfd_local_retrieval",
        "retrieval",
        score,
        0.12,
        True,
        detail,
        "VRAG-DFD: Verifiable Retrieval-Augmented Generation for DeepFake Detection",
        "local_evidence_retrieval",
    )


def _ivy_xdetector_adapter(path: Path) -> PixelExpertResult:
    sidecars = [
        path.with_suffix(path.suffix + ".ivy.json"),
        path.with_suffix(".ivy.json"),
        path.parent / (path.name + ".ivy.json"),
    ]
    for sidecar in sidecars:
        if not sidecar.exists():
            continue
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return PixelExpertResult(
                "ivy_xdetector_adapter",
                "external_baseline",
                0,
                0.20,
                False,
                f"Ivy-xDetector sidecar를 읽지 못했습니다: {exc}",
                "Ivy-xDetector external explainable VLM baseline",
                "sidecar_adapter",
            )
        score = _score_from_ivy_payload(payload)
        explanation = str(payload.get("explanation") or payload.get("reason") or payload.get("label") or "Ivy-xDetector sidecar score")
        return PixelExpertResult(
            "ivy_xdetector_adapter",
            "external_baseline",
            score,
            0.20,
            True,
            f"Ivy-xDetector sidecar 기준선: {explanation}",
            "Ivy-xDetector external explainable VLM baseline",
            "sidecar_adapter",
        )
    return PixelExpertResult(
        "ivy_xdetector_adapter",
        "external_baseline",
        0,
        0.20,
        False,
        "Ivy-xDetector sidecar가 없어 외부 VLM 기준선은 fusion에서 제외했습니다.",
        "Ivy-xDetector external explainable VLM baseline",
        "sidecar_adapter",
    )


def _safe_localization_expert(raster: PixelRaster, luminance: list[float]) -> tuple[PixelExpertResult, list[list[int]]]:
    tile_size = max(8, min(24, min(raster.width, raster.height) // 6))
    grid: list[list[int]] = []
    tile_scores: list[float] = []
    for y in range(0, raster.height, tile_size):
        row_scores: list[int] = []
        for x in range(0, raster.width, tile_size):
            score = _tile_edge_score(luminance, raster.width, raster.height, x, y, tile_size)
            tile_scores.append(score)
            row_scores.append(0)
        grid.append(row_scores)

    average, stdev = _basic_stats(tile_scores)
    max_score = max(tile_scores) if tile_scores else 0.0
    anomaly_ratio = max_score / max(1.0, average)
    normalized_grid = _normalize_grid(grid, tile_scores)

    score = 0
    detail = "타일별 국소 이상 신호가 두드러지지 않습니다."
    if len(tile_scores) >= 12 and anomaly_ratio > 3.2 and stdev > 7:
        score = 67
        detail = "일부 영역의 고주파/잔차가 주변보다 커서 부분 편집 또는 합성 후보입니다."
    elif len(tile_scores) >= 12 and anomaly_ratio > 2.35 and stdev > 5:
        score = 52
        detail = "타일별 픽셀 통계 차이가 있어 국소 편집 가능성을 확인할 만합니다."
    return (
        PixelExpertResult(
            "safe_pixel_localization",
            "localization",
            score,
            0.18,
            True,
            detail,
            "SAFE Image Authenticity Challenge",
        ),
        normalized_grid,
    )


def _fuzzy_decision_tree_fusion(experts: list[PixelExpertResult]) -> tuple[int, str]:
    available = [expert for expert in experts if expert.available]
    if not available:
        return 0, "사용 가능한 전문가 점수가 없어 fusion을 수행하지 못했습니다."

    high = [expert for expert in available if expert.score >= 67]
    medium = [expert for expert in available if 45 <= expert.score < 67]
    retrieval = [expert for expert in available if expert.family == "retrieval" and expert.score >= 45]
    localization = [expert for expert in available if expert.family in {"localization", "compositing"} and expert.score >= 45]
    statistical = [expert for expert in available if expert.family in {"spectral", "statistical", "reconstruction"} and expert.score >= 45]
    weighted = sum(expert.score * expert.weight for expert in available) / max(0.001, sum(expert.weight for expert in available))

    if len(high) >= 2 or (high and retrieval and statistical):
        score = max(72, min(96, int(round(weighted + 16))))
        detail = "fuzzy rule: 복수 고신뢰 전문가 또는 검색+통계 고신호가 동시에 활성화되었습니다."
    elif high and (medium or localization):
        score = max(66, min(88, int(round(weighted + 10))))
        detail = "fuzzy rule: 한 고신뢰 전문가와 보조 신호가 함께 활성화되었습니다."
    elif len(medium) >= 3:
        score = max(54, min(78, int(round(weighted + 8))))
        detail = "fuzzy rule: 중간 신호가 여러 계열에서 누적되었습니다."
    elif len(medium) >= 1:
        score = max(34, min(62, int(round(weighted + 3))))
        detail = "fuzzy rule: 단일 또는 소수의 중간 신호만 있어 보수적으로 반영했습니다."
    else:
        score = max(0, min(32, int(round(weighted))))
        detail = "fuzzy rule: 전문가 신호가 낮아 낮은 점수로 유지했습니다."
    return score, detail


def _reveal_evidence_chain(experts: list[PixelExpertResult]) -> list[str]:
    chain = []
    for expert in sorted(experts, key=lambda item: item.score, reverse=True):
        if not expert.available or expert.score < 32:
            continue
        chain.append(f"{expert.name}:{expert.score} - {expert.detail}")
        if len(chain) >= 6:
            break
    if not chain:
        chain.append("REVEAL evidence chain: 활성화된 픽셀 증거가 약합니다.")
    return chain


def _agentfox_explainable_summary(experts: list[PixelExpertResult], fused: int, confidence: str) -> str:
    active = [expert for expert in experts if expert.available and expert.score >= 45]
    if not active:
        return "AgentFoX-style explanation: 활성 전문가가 적어 설명 신뢰도는 낮습니다."
    families = sorted({expert.family for expert in active})
    names = ", ".join(expert.name for expert in sorted(active, key=lambda item: item.score, reverse=True)[:4])
    return f"AgentFoX-style explanation: {len(active)}개 전문가({', '.join(families)})가 fused score={fused}, confidence={confidence}에 기여했습니다: {names}."


def _implemented_references(mode: str) -> list[str]:
    references = [
        "1. difference_in_difference_reconstruction",
        "2. spark_il_spectral_retrieval",
        "3. low_correlation_fractal_signal",
        "4. alpha_blending_compositing",
        "6. vrag_dfd_local_retrieval",
        "7. reveal_evidence_chain",
        "8. agentfox_explainable_summary",
        "9. fuzzy_decision_tree_fusion",
        "10. ivy_xdetector_adapter",
    ]
    if mode == "deep":
        references.insert(4, "5. safe_pixel_localization")
    else:
        references.insert(4, "5. safe_pixel_localization (--pixel deep)")
    return references


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


def _periodicity_signal(raster: PixelRaster, luminance: list[float]) -> tuple[int, str]:
    if raster.width < 32 or raster.height < 32:
        return 0, ""

    candidates = [step for step in (4, 8, 16, 32) if step < raster.width // 2 and step < raster.height // 2]
    if not candidates:
        return 0, ""

    base = _shift_difference(luminance, raster.width, raster.height, 1, 1)
    best_step = 0
    best_ratio = 1.0
    for step in candidates:
        shifted = (_shift_difference(luminance, raster.width, raster.height, step, 0) + _shift_difference(luminance, raster.width, raster.height, 0, step)) / 2
        ratio = shifted / max(1.0, base)
        if ratio < best_ratio:
            best_ratio = ratio
            best_step = step
    if best_step and best_ratio < 0.44 and base > 8:
        return 72, f"{best_step}px 간격 반복성이 강해 타일/생성 텍스처 후보입니다."
    if best_step and best_ratio < 0.58 and base > 12:
        return 55, f"{best_step}px 간격의 약한 반복성이 보여 생성/업스케일 흔적을 확인할 만합니다."
    return 0, ""


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


def _score_from_ivy_payload(payload: dict[str, object]) -> int:
    for key in ("score", "fake_score", "probability", "confidence"):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            return int(round(_clamp(float(value), 0.0, 1.0) * 100 if value <= 1 else _clamp(float(value), 0.0, 100.0)))
    label = str(payload.get("label") or payload.get("verdict") or "").lower()
    if any(marker in label for marker in ("fake", "ai", "synthetic", "generated")):
        return 72
    if any(marker in label for marker in ("real", "camera", "authentic")):
        return 8
    return 0


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


def _confidence_for(score: int, expert_count: int, mode: str) -> str:
    if score >= 70 and expert_count >= 3:
        return "high" if mode == "deep" else "medium"
    if score >= 45:
        return "medium"
    if score > 0:
        return "low"
    return "low"


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

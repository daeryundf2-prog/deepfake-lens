"""Canonical pixel analysis path: local multi-expert ensemble.

``analyze_image_pixels`` is what the scan pipeline (``core.py``) calls;
results are labelled ``analysis_tier="ensemble"``. ``pixel_analyzer.py``
is a separate cv2-based quick screen kept for the ``pixel-analysis`` CLI
command and webapp (``analysis_tier="pre-screen"``); see
``docs/consolidation-notes.md`` for the convergence plan.

Disposition (R-2): this ensemble measured AUROC 0.43-0.48 on ProGAN —
below chance — so it is a pre-screen/prioritization signal only. When an
external model (e.g. AIDE via ``--model-path``) supplies a score, the
scan-level fusion profile prefers it (``external_model`` 0.30 vs
``pixel`` 0.25); the pixel component still contributes — there is no
veto — and when no external signal ran, the result carries an explicit
"미검증 휴리스틱" limitation.
"""

from __future__ import annotations

import math
import json
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .pixel_raster import (  # noqa: F401
    MAX_DECOMPRESSED_IMAGE_BYTES,
    PixelRaster,
    _basic_stats,
    _boundary_jump_score,
    _box_count_fractal_dimension,
    _clamp,
    _edge_values,
    _euclidean,
    _load_png_raster,
    _load_raster,
    _load_with_optional_pillow,
    _luminance_values,
    _neighbor_correlation,
    _normalize_grid,
    _quantized_luminance_ratio,
    _shift_difference,
    _tile_edge_score,
    _write_png_heatmap,
)


PIXEL_MODEL_NAME = "local-multiexpert-pixel-v1"
SUPPORTED_PIXEL_MODES = {"off", "fast", "deep"}
DEFAULT_PIXEL_MAX_SIDE = 192


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


# D12: the ensemble is an unmeasured heuristic (ProGAN AUROC 0.43-0.48), so
# its output is a raw 0-100 value labeled 참고 — never a score or a
# low/medium/high confidence that reads like a finding.
PIXEL_REFERENCE_CONFIDENCE = "참고"


@dataclass(frozen=True)
class PixelAnalysis:
    """Pixel ensemble output — reference only (D12).

    ``raw_score`` is the uncalibrated 0-100 weighted mean of the experts;
    ``reference_confidence`` is ``"참고"`` when the ensemble ran, else
    ``"off"``/``"unavailable"``.
    """

    mode: str
    available: bool
    raw_score: int
    reference_confidence: str
    model: str
    experts: list[PixelExpertResult] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    fusion: str = "weighted_mean"
    evidence_chain: list[str] = field(default_factory=list)
    implemented_references: list[str] = field(default_factory=list)
    heatmap_path: str | None = None
    # "ensemble" here; the cv2 quick screen in pixel_analyzer.py reports
    # "pre-screen".
    analysis_tier: str = "ensemble"


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
        _frequency_forensics_expert(raster),
        _difference_in_difference_expert(raster, luminance, stats),
        _spark_il_retrieval_expert(raster, luminance, stats, edge_stats),
        _low_correlation_fractal_expert(raster, luminance, stats, edge_stats),
        _alpha_blending_expert(raster, luminance),
        _ela_expert(raster),
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
    signals = [expert.detail for expert in available_experts if expert.score >= 45]
    evidence_chain = _reveal_evidence_chain(experts)
    agentfox_summary = _agentfox_explainable_summary(available_experts, fused)
    limitations = load_limitations + [
        "픽셀 분석은 로컬 multi-expert 앙상블입니다. 학습된 딥페이크 모델의 확률값으로 해석하면 안 됩니다.",
        "메타데이터가 제거된 파일도 볼 수 있지만, 카메라 원본/편집본/압축본을 구분하지 못할 수 있습니다.",
        agentfox_summary,
    ]
    if not any(expert.available for expert in experts if expert.family == "external_baseline"):
        limitations.append(
            "픽셀 앙상블은 미검증 휴리스틱입니다(ProGAN 실측 AUROC 0.43-0.48 — 무작위 수준 이하). "
            "AIDE 등 외부 모델 점수 없이는 이 점수를 단독으로 신뢰할 수 없습니다."
        )

    written_heatmap = None
    if heatmap_path and heatmap_grid:
        written_heatmap = str(_write_png_heatmap(heatmap_path, heatmap_grid))

    return PixelAnalysis(
        mode=mode,
        available=True,
        raw_score=fused,
        reference_confidence=PIXEL_REFERENCE_CONFIDENCE,
        model=PIXEL_MODEL_NAME,
        experts=experts,
        signals=signals,
        limitations=limitations,
        fusion="fuzzy_decision_tree_v0",
        evidence_chain=evidence_chain,
        implemented_references=_implemented_references(mode),
        heatmap_path=written_heatmap,
    )


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


def _frequency_forensics_expert(raster: PixelRaster) -> PixelExpertResult:
    """FFT/DCT/NPR frequency measurements (numpy-based, optional).

    Replaces the former 'spectral_statistics' expert whose periodicity
    metric was a spatial shift-difference, not a spectral measurement.
    """
    reference = "NPR (Neighboring Pixel Relations); F3-Net frequency-aware learning"
    try:
        import numpy as np

        from .frequency import MIN_FREQUENCY_ANALYSIS_SIDE, frequency_features
    except ImportError:
        return PixelExpertResult(
            "frequency_forensics",
            "frequency",
            0,
            0.30,
            False,
            "numpy가 설치되어 있지 않아 주파수 분석을 건너뜁니다.",
            reference,
            "numpy-local-simplified",
        )

    if min(raster.width, raster.height) < MIN_FREQUENCY_ANALYSIS_SIDE:
        return PixelExpertResult(
            "frequency_forensics",
            "frequency",
            0,
            0.30,
            False,
            "주파수 분석을 하기에는 샘플링된 이미지가 너무 작습니다.",
            reference,
            "numpy-local-simplified",
        )

    gray = np.asarray(
        [[0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2] for p in raster.pixels[y * raster.width : (y + 1) * raster.width]] for y in range(raster.height)],
        dtype=np.float64,
    )
    features = frequency_features(gray)
    from .frequency import (
        copy_move_keypoint_score,
        copy_move_score,
        ela_metrics,
        jpeg_double_compression_score,
    )

    djc_strength, djc_detail = jpeg_double_compression_score(gray)
    ela_global, ela_region, ela_detail = ela_metrics(gray)
    cm_ratio, cm_detail = copy_move_score(gray)
    try:
        cmk_ratio, cmk_detail = copy_move_keypoint_score(gray)
    except ImportError:
        cmk_ratio, cmk_detail = 0.0, "cv2 미설치 — 회전/스케일 copy-move 분석을 건너뜁니다."

    score = 0
    detail = "방사형 스펙트럼/스파이크/NPR/DCT 측정에서 두드러진 생성 흔적을 찾지 못했습니다."
    if features.spike_count > 0 and features.max_spike_prominence >= 8.0:
        score = 72
        detail = f"방사형 평균 대비 스펙트럼 스파이크 {features.spike_count}개(최대 {features.max_spike_prominence:.1f}σ)가 관측됩니다. 업샘플링/체커보드 아티팩트 후보입니다."
    elif features.max_spike_prominence >= 5.0:
        score = 48
        detail = f"약한 스펙트럼 스파이크(최대 {features.max_spike_prominence:.1f}σ)가 보여 리샘플링 흔적을 확인할 만합니다."
    elif features.npr_consistency > 0.55:
        score = 55
        detail = f"이웃 픽셀 보간 일치율({features.npr_consistency:.2f})이 높아 리사이즈된 영역일 수 있습니다."
    elif features.dct_highfreq_ratio < 0.02 and features.dct_block_uniformity < 0.01:
        score = 45
        detail = "8x8 블록 고주파 에너지가 비정상적으로 낮아 과도하게 평활화된 표면입니다."
    if djc_strength >= 0.5:
        score = max(score, 50)
        detail += " " + djc_detail
    if ela_global > 0 and ela_region / max(ela_global, 1e-6) >= 3.0:
        score = max(score, 45)
        detail += " " + ela_detail + " — 국소 편집/합성 영역 후보."
    if cm_ratio >= 0.12:
        score = max(score, 55)
        detail += " " + cm_detail
    elif cm_ratio >= 0.05:
        detail += " " + cm_detail + " (약한 신호 — 확인 필요.)"
    if cmk_ratio >= 0.12:
        score = max(score, 55)
        detail += " " + cmk_detail
    elif cmk_ratio >= 0.05:
        detail += " " + cmk_detail + " (약한 신호 — 확인 필요.)"
    return PixelExpertResult(
        "frequency_forensics",
        "frequency",
        score,
        0.30,
        True,
        detail,
        reference,
        "numpy-local-simplified",
    )


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


def _ela_expert(raster: PixelRaster) -> PixelExpertResult:
    """Error-level analysis: re-save at fixed JPEG quality and look for
    blocks whose compression error deviates from the frame baseline.

    A spliced/composited region carries a different compression history
    than the rest of the image, so its re-save error level differs.
    Heuristic tier only — flags regions for review, never a verdict.
    Unreliable on uniform/flat content and on inputs that were never
    JPEG-compressed.
    """
    unavailable = PixelExpertResult(
        "ela_error_level",
        "forensic",
        0,
        0.12,
        False,
        "ELA를 계산할 수 없습니다 (PIL 필요 또는 이미지가 너무 작음).",
        "Error Level Analysis (Krawetz, 2007)",
    )
    if raster.width < 32 or raster.height < 32:
        return unavailable
    try:
        from PIL import Image
        import io
    except ImportError:
        return unavailable

    flat = bytes(channel for px in raster.pixels for channel in px)
    try:
        image = Image.frombytes("RGB", (raster.width, raster.height), flat)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=90)
        buffer.seek(0)
        resaved = Image.open(buffer).convert("RGB")
        resaved_pixels = resaved.tobytes()
    except Exception:
        return unavailable

    block = 16
    blocks_x = raster.width // block
    blocks_y = raster.height // block
    if blocks_x < 2 or blocks_y < 2:
        return unavailable

    block_means: list[float] = []
    for by in range(blocks_y):
        for bx in range(blocks_x):
            total = 0.0
            count = 0
            for y in range(by * block, (by + 1) * block):
                row = y * raster.width
                for x in range(bx * block, (bx + 1) * block):
                    i = (row + x) * 3
                    total += abs(flat[i] - resaved_pixels[i])
                    count += 1
            block_means.append(total / max(1, count))

    ordered = sorted(block_means)
    median = ordered[len(ordered) // 2]
    deviations = sorted(abs(v - median) for v in block_means)
    mad = deviations[len(deviations) // 2] or 1e-6
    # Outlier blocks deviate strongly from the baseline error level — in
    # either direction (a pasted region may carry *less* error if it was
    # compressed at higher quality before compositing).
    outlier_idx = [
        i for i, v in enumerate(block_means) if abs(v - median) > 4.5 * mad
    ]
    outlier_ratio = len(outlier_idx) / len(block_means)
    # Localization: outliers concentrated in a sub-region (bounding box
    # covering less than ~2/3 of the frame) are splice-like; scattered
    # outliers are usually texture noise.
    localized = False
    if outlier_idx:
        xs = [i % blocks_x for i in outlier_idx]
        ys = [i // blocks_x for i in outlier_idx]
        span_x = (max(xs) - min(xs) + 1) / blocks_x
        span_y = (max(ys) - min(ys) + 1) / blocks_y
        localized = span_x * span_y <= 0.66

    score = 0
    detail = "ELA 오차 수준이 프레임 전반에 걸쳐 고르게 분포합니다."
    if 0.02 <= outlier_ratio <= 0.30 and localized and mad > 0:
        score = 62
        detail = (
            f"일부 블록({len(outlier_idx)}개, {outlier_ratio:.0%})의 재압축 오차가 "
            "주변과 유의하게 달라 부분 편집/합성 후보입니다."
        )
    elif 0.01 <= outlier_ratio <= 0.40 and mad > 0:
        score = 41
        detail = (
            f"ELA 오차 이상 블록({len(outlier_idx)}개)이 있으나 국소성이 약해 "
            "텍스처 차이일 가능성도 있습니다."
        )
    return PixelExpertResult(
        "ela_error_level",
        "forensic",
        score,
        0.12,
        True,
        detail,
        "Error Level Analysis (Krawetz, 2007)",
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
    """Weighted mean of the available experts' scores — nothing else (G3).

    The former rule branches lifted the result to fixed floors
    (max(72|66|54|34, ...)) whenever a few experts fired, which turned
    noise, gradients and blur into a constant ~35-66 "medium" (AUROC
    0.43-0.48 measured on ProGAN). The activation pattern is still
    described in the detail string for the examiner, but it no longer
    changes the number. The score is a reference signal only.
    """
    available = [expert for expert in experts if expert.available]
    if not available:
        return 0, "사용 가능한 전문가 점수가 없어 fusion을 수행하지 못했습니다."

    high = [expert for expert in available if expert.score >= 67]
    medium = [expert for expert in available if 45 <= expert.score < 67]
    weighted = sum(expert.score * expert.weight for expert in available) / max(0.001, sum(expert.weight for expert in available))
    score = max(0, min(100, int(round(weighted))))
    detail = (
        f"전문가 {len(available)}개 점수의 가중평균 {score} "
        f"(고신호 {len(high)}개, 중간 신호 {len(medium)}개 — 하한·가산 규칙 없음, 미측정 참고값)."
    )
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


def _agentfox_explainable_summary(experts: list[PixelExpertResult], fused: int) -> str:
    active = [expert for expert in experts if expert.available and expert.score >= 45]
    if not active:
        return "AgentFoX-style explanation: 활성 전문가가 적어 설명할 신호가 거의 없습니다(참고 신호)."
    families = sorted({expert.family for expert in active})
    names = ", ".join(expert.name for expert in sorted(active, key=lambda item: item.score, reverse=True)[:4])
    return f"AgentFoX-style explanation: {len(active)}개 전문가({', '.join(families)})가 원점수 {fused}(참고, 미측정)에 기여했습니다: {names}."


def _implemented_references(mode: str) -> list[str]:
    references = [
        "1. difference_in_difference_reconstruction",
        "2. frequency_forensics_fft_dct_npr",
        "3. low_correlation_fractal_signal",
        "4. alpha_blending_compositing",
        "6. vrag_dfd_local_retrieval",
        "7. reveal_evidence_chain",
        "8. agentfox_explainable_summary",
        "9. fuzzy_decision_tree_fusion",
        "10. ivy_xdetector_adapter",
        "11. spark_il_spectral_retrieval",
    ]
    if mode == "deep":
        references.insert(4, "5. safe_pixel_localization")
    else:
        references.insert(4, "5. safe_pixel_localization (--pixel deep)")
    return references


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




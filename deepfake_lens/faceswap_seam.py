"""Face-swap boundary seam and multi-cue localized manipulation detector.

Designed for single-image and video frame forensics where a subject's face has
been pasted onto an unconsenting victim or body (FaceSwap, FaceFusion, ReActor,
RoOP, SimSwap).

Evaluates 4 orthogonal forensic cues without requiring the unedited original:
1. Boundary seam Laplacian residual: Poisson blending and gaussian feathering
   artifacts along the elliptical face contour.
2. Noise variance discrepancy: mismatch between sensor noise variance inside the
   face mask versus the adjacent neck/shoulder context.
3. Chrominance gradient discontinuity: color temperature/hue transition step
   at the chin-neck transition line in YCrCb/LAB space.
4. Corneal specular asymmetry: reflection highlight angle and coordinate
   discrepancy between left and right pupils under synthetic illumination.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .face import FaceRegion, _detect_faces, _imread_unicode


@dataclass(frozen=True)
class FaceSwapEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class FaceSwapSeamAnalysis:
    score: int  # 0-100
    band: str  # high, medium, low, unknown
    band_label: str  # 높음, 주의, 낮음, 판단 어려움
    verdict: str
    signals: list[FaceSwapEvidenceSignal]
    limitations: list[str]
    face_count: int
    boundary_residual: float | None = None
    noise_discrepancy_ratio: float | None = None
    chrominance_delta: float | None = None
    corneal_asymmetry: float | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def analyze_faceswap_seam(
    path: Path | str,
    *,
    image_matrix: Any = None,
) -> FaceSwapSeamAnalysis:
    """Run multi-cue localized face-swap seam forensic analysis on an image."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return _error_analysis("opencv-python is required for face-swap seam analysis.")

    image = image_matrix
    if image is None:
        image_path = Path(path)
        if not image_path.is_file():
            return _error_analysis(f"파일이 존재하지 않습니다: {image_path}")
        image = _imread_unicode(image_path)
        if image is None:
            return _error_analysis("이미지를 디코딩할 수 없습니다.")

    faces = _detect_faces(image)
    if not faces:
        return FaceSwapSeamAnalysis(
            score=0,
            band="unknown",
            band_label="판단 어려움",
            verdict="얼굴 영역이 감지되지 않아 페이스스왑 경계면 분석을 수행할 수 없습니다.",
            signals=[],
            limitations=["이미지에서 유효한 얼굴을 찾지 못했습니다."],
            face_count=0,
        )

    signals: list[FaceSwapEvidenceSignal] = []
    limitations: list[str] = []

    best_boundary_res: float | None = None
    best_noise_ratio: float | None = None
    best_chroma_delta: float | None = None
    best_corneal_asym: float | None = None

    img_h, img_w = image.shape[:2]
    analyzed_faces = 0

    for face in faces:
        x, y, w, h = face.x, face.y, face.width, face.height
        if w < 32 or h < 32:
            continue
        analyzed_faces += 1

        # 1. Elliptical boundary seam analysis (Poisson blending & feathering)
        seam_res, seam_sig = _analyze_elliptical_seam(image, face, cv2, np)
        if seam_res is not None:
            best_boundary_res = max(best_boundary_res or 0.0, seam_res)
        if seam_sig:
            signals.append(seam_sig)

        # 2. Sensor noise discrepancy (Face crop vs body context)
        noise_ratio, noise_sig = _analyze_noise_mismatch(image, face, cv2, np)
        if noise_ratio is not None and (
            best_noise_ratio is None
            or abs(math.log(max(noise_ratio, 1e-9))) > abs(math.log(max(best_noise_ratio, 1e-9)))
        ):
            best_noise_ratio = noise_ratio
        if noise_sig:
            signals.append(noise_sig)

        # 3. Chrominance gradient discontinuity (Chin vs Neck color temperature)
        chroma_d, chroma_sig = _analyze_chroma_step(image, face, cv2, np)
        if chroma_d is not None:
            best_chroma_delta = max(best_chroma_delta or 0.0, chroma_d)
        if chroma_sig:
            signals.append(chroma_sig)

        # 4. Corneal reflection specular asymmetry
        corneal_asym, corneal_sig = _analyze_corneal_reflections(image, face, cv2, np)
        if corneal_asym is not None:
            best_corneal_asym = max(best_corneal_asym or 0.0, corneal_asym)
        if corneal_sig:
            signals.append(corneal_sig)

    if analyzed_faces == 0:
        return FaceSwapSeamAnalysis(
            score=0,
            band="unknown",
            band_label="판단 어려움",
            verdict="감지된 얼굴이 모두 32px 미만으로 경계면 분석이 불가능합니다.",
            signals=[],
            limitations=["유효 해상도의 얼굴이 없어 분석 지표를 산출하지 못했습니다."],
            face_count=len(faces),
        )

    measured = any(v is not None for v in (best_boundary_res, best_noise_ratio, best_chroma_delta, best_corneal_asym))
    if not measured:
        limitations.append("얼굴 주변 문맥 영역이 부족하여 분석 지표를 산출하지 못했습니다.")
    elif not signals:
        limitations.append("얼굴 경계면 및 노이즈 분포에서 뚜렷한 합성 불연속성이 감지되지 않았습니다.")

    if not measured:
        return FaceSwapSeamAnalysis(
            score=0,
            band="unknown",
            band_label="판단 어려움",
            verdict="안면부 분석 지표를 산출할 수 없어 합성 여부를 판단하지 못했습니다.",
            signals=signals,
            limitations=limitations,
            face_count=len(faces),
        )

    score = min(100, sum(s.weight for s in signals))
    if score >= 65:
        band = "high"
        band_label = "높음"
        verdict = "안면부 경계면 잔차 및 노이즈 불일치로 보아 페이스스왑(FaceSwap) 합성 가능성이 매우 높습니다."
    elif score >= 35:
        band = "medium"
        band_label = "주의"
        verdict = "안면부와 주변 신체 영역 사이에 미세한 이질성이 감지되어 정밀 대조가 필요합니다."
    else:
        band = "low"
        band_label = "낮음"
        verdict = "안면부 경계면 및 피부 노이즈가 주변 환경과 일관성을 유지하고 있습니다."

    return FaceSwapSeamAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        face_count=len(faces),
        boundary_residual=best_boundary_res,
        noise_discrepancy_ratio=best_noise_ratio,
        chrominance_delta=best_chroma_delta,
        corneal_asymmetry=best_corneal_asym,
    )


def _analyze_elliptical_seam(image: Any, face: FaceRegion, cv2: Any, np: Any) -> tuple[float | None, FaceSwapEvidenceSignal | None]:
    """Measure high-frequency Laplacian transition across the elliptical face seam."""
    img_h, img_w = image.shape[:2]
    cx = face.x + face.width // 2
    cy = face.y + face.height // 2
    axes = (int(face.width * 0.48), int(face.height * 0.52))

    if axes[0] <= 4 or axes[1] <= 4:
        return None, None

    # Create inner mask (eroded) and outer mask (dilated)
    inner_mask = np.zeros((img_h, img_w), dtype=np.uint8)
    outer_mask = np.zeros((img_h, img_w), dtype=np.uint8)

    inner_axes = (max(2, int(axes[0] * 0.85)), max(2, int(axes[1] * 0.85)))
    outer_axes = (int(axes[0] * 1.15), int(axes[1] * 1.15))

    cv2.ellipse(inner_mask, (cx, cy), inner_axes, 0, 0, 360, 255, -1)
    cv2.ellipse(outer_mask, (cx, cy), outer_axes, 0, 0, 360, 255, -1)

    seam_ring = cv2.subtract(outer_mask, inner_mask)
    if np.count_nonzero(seam_ring) < 20:
        return None, None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    lap_abs = np.abs(laplacian)

    ring_vals = lap_abs[seam_ring > 0]
    inner_vals = lap_abs[inner_mask > 0]

    ring_var = float(np.var(ring_vals))
    inner_var = float(np.var(inner_vals)) if len(inner_vals) > 0 else 1.0

    # Synthetic blending produces either unnatural feathering (abnormally low seam variance)
    # or hard boundary cut-and-paste edges (abnormally high seam variance).
    ratio = ring_var / max(inner_var, 1e-4)

    if ratio > 3.2:
        return ratio, FaceSwapEvidenceSignal(
            title="안면 윤곽선 경계면 주파수 단절",
            detail=f"얼굴 외곽 경계선에서 라플라시안 주파수 잔차 비율({ratio:.2f})이 비정상적으로 높아 경계 합성이 의심됩니다.",
            weight=30,
        )
    elif ratio < 0.22 and inner_var > 15.0:
        return ratio, FaceSwapEvidenceSignal(
            title="안면부 인위적 스무딩/페더링 흔적",
            detail=f"얼굴 경계면의 고주파 잔차가 과도하게 평활화({ratio:.2f})되어 포아송 블렌딩 합성 흔적을 시사합니다.",
            weight=25,
        )
    return ratio, None


def _analyze_noise_mismatch(image: Any, face: FaceRegion, cv2: Any, np: Any) -> tuple[float | None, FaceSwapEvidenceSignal | None]:
    """Measure high-frequency noise variance mismatch between face and body."""
    img_h, img_w = image.shape[:2]
    fx, fy, fw, fh = face.x, face.y, face.width, face.height

    # Face patch
    face_crop = image[fy : fy + fh, fx : fx + fw]
    if face_crop.size == 0:
        return None, None

    # Context patch (lower neck/chest or surrounding background)
    ctx_y1 = min(img_h - 1, fy + fh)
    ctx_y2 = min(img_h, fy + int(fh * 1.6))
    ctx_x1 = max(0, fx - int(fw * 0.2))
    ctx_x2 = min(img_w, fx + int(fw * 1.2))

    context_crop = image[ctx_y1:ctx_y2, ctx_x1:ctx_x2]
    if context_crop.size < 100:
        # Fallback: context from side regions
        ctx_x1 = max(0, fx - fw)
        ctx_x2 = fx
        context_crop = image[fy : fy + fh, ctx_x1:ctx_x2]

    if context_crop.size < 100:
        return None, None

    # Extract high-frequency noise via median filter subtraction
    face_gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY)
    ctx_gray = cv2.cvtColor(context_crop, cv2.COLOR_BGR2GRAY)

    face_noise = cv2.absdiff(face_gray, cv2.medianBlur(face_gray, 3))
    ctx_noise = cv2.absdiff(ctx_gray, cv2.medianBlur(ctx_gray, 3))

    face_std = float(np.std(face_noise))
    ctx_std = float(np.std(ctx_noise))

    ratio = face_std / max(ctx_std, 1e-4)

    if ratio < 0.42:
        return ratio, FaceSwapEvidenceSignal(
            title="안면부-신체 노이즈 질감 불일치",
            detail=f"얼굴 영역의 센서 노이즈 강도({face_std:.1f})가 주변 신체({ctx_std:.1f}) 대비 {ratio:.2f}배로 이질적입니다 (AI 생성 얼굴 과평활화).",
            weight=28,
        )
    elif ratio > 2.6:
        return ratio, FaceSwapEvidenceSignal(
            title="안면부 인공 고주파 노이즈 과다",
            detail=f"얼굴 영역의 노이즈 강도({face_std:.1f})가 주변({ctx_std:.1f}) 대비 {ratio:.2f}배로 불균형합니다 (생성기 체크포인트 잔차).",
            weight=24,
        )
    return ratio, None


def _analyze_chroma_step(image: Any, face: FaceRegion, cv2: Any, np: Any) -> tuple[float | None, FaceSwapEvidenceSignal | None]:
    """Measure chrominance (color temperature) step across the chin-to-neck transition."""
    img_h, img_w = image.shape[:2]
    fx, fy, fw, fh = face.x, face.y, face.width, face.height

    # Chin area (lower 20% of face box)
    chin_y1 = fy + int(fh * 0.78)
    chin_y2 = min(img_h, fy + fh)
    chin_crop = image[chin_y1:chin_y2, fx + int(fw * 0.2) : fx + int(fw * 0.8)]

    # Neck area (immediately below face box)
    neck_y1 = min(img_h - 1, fy + fh)
    neck_y2 = min(img_h, fy + int(fh * 1.25))
    neck_crop = image[neck_y1:neck_y2, fx + int(fw * 0.2) : fx + int(fw * 0.8)]

    if chin_crop.size < 40 or neck_crop.size < 40:
        return None, None

    chin_ycrcb = cv2.cvtColor(chin_crop, cv2.COLOR_BGR2YCrCb)
    neck_ycrcb = cv2.cvtColor(neck_crop, cv2.COLOR_BGR2YCrCb)

    chin_cr_mean = float(np.mean(chin_ycrcb[:, :, 1]))
    chin_cb_mean = float(np.mean(chin_ycrcb[:, :, 2]))

    neck_cr_mean = float(np.mean(neck_ycrcb[:, :, 1]))
    neck_cb_mean = float(np.mean(neck_ycrcb[:, :, 2]))

    delta_chroma = math.sqrt((chin_cr_mean - neck_cr_mean) ** 2 + (chin_cb_mean - neck_cb_mean) ** 2)

    if delta_chroma > 18.0:
        return delta_chroma, FaceSwapEvidenceSignal(
            title="턱선-목선 색온도 및 색도 단절",
            detail=f"턱선과 목선 사이의 피부 톤 색도차(Δ{delta_chroma:.1f})가 비자연스럽게 커 서로 다른 인물 간 합성 징후입니다.",
            weight=22,
        )
    return delta_chroma, None


def _analyze_corneal_reflections(image: Any, face: FaceRegion, cv2: Any, np: Any) -> tuple[float | None, FaceSwapEvidenceSignal | None]:
    """Examine corneal specular reflection highlight asymmetry across both eyes."""
    if len(face.landmarks) < 2:
        return None, None

    left_anchor, right_anchor = face.landmarks[0], face.landmarks[1]
    eye_radius = max(4, int(face.width * 0.07))

    def _get_specular_centroid(anchor: tuple[int, int]) -> tuple[float, float, float] | None:
        ax, ay = anchor
        y1, y2 = max(0, ay - eye_radius), min(image.shape[0], ay + eye_radius)
        x1, x2 = max(0, ax - eye_radius), min(image.shape[1], ax + eye_radius)
        patch = image[y1:y2, x1:x2]
        if patch.size < 16:
            return None
        gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        max_val = np.max(gray)
        if max_val < 160:  # No strong specular reflection
            return None
        thresh = cv2.threshold(gray, max(180, int(max_val * 0.85)), 255, cv2.THRESH_BINARY)[1]
        moments = cv2.moments(thresh)
        if moments["m00"] <= 0:
            return None
        return (moments["m10"] / moments["m00"], moments["m01"] / moments["m00"], float(moments["m00"]))

    left_spec = _get_specular_centroid(left_anchor)
    right_spec = _get_specular_centroid(right_anchor)

    if left_spec is None or right_spec is None:
        return None, None

    # Compute offset from patch center
    lx_off = left_spec[0] - eye_radius
    ly_off = left_spec[1] - eye_radius
    rx_off = right_spec[0] - eye_radius
    ry_off = right_spec[1] - eye_radius

    # Discrepancy in normalized relative highlight direction
    asym = math.sqrt((lx_off - rx_off) ** 2 + (ly_off - ry_off) ** 2)

    if asym > 4.5:
        return asym, FaceSwapEvidenceSignal(
            title="양안 각막 반사광(하이라이트) 불일치",
            detail=f"양쪽 눈동자에 반사된 광원 중심점 편차({asym:.1f}px)가 물리적 조명 법칙에 위배됩니다 (가상 광원 합성).",
            weight=20,
        )
    return asym, None


def _error_analysis(msg: str) -> FaceSwapSeamAnalysis:
    return FaceSwapSeamAnalysis(
        score=0,
        band="unknown",
        band_label="판단 어려움",
        verdict=msg,
        signals=[],
        limitations=[msg],
        face_count=0,
    )

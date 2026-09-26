"""Face manipulation detection module.

Detects face swap, face reenactment, and lip sync manipulation
using boundary blending, reflection patterns, and color consistency.

Facial landmark anchors are measured with MediaPipe FaceMesh when the
optional ``face_mediapipe`` extra is installed; otherwise they fall back
to box-ratio estimates that are explicitly labelled via
``FaceRegion.landmarks_source`` and must not feed geometry checks.

Two measurement paths exist, tried in order:

1. **Tasks API** (``mp.tasks`` FaceLandmarker, mediapipe >= 0.10.30 / 1.x
   tasks-only builds): needs the ``face_landmarker.task`` model asset —
   point ``DEEPFAKE_LENS_FACE_LANDMARKER`` at it or drop it at
   ``models/face_landmarker.task``. Yields
   ``landmarks_source="mediapipe-facelandmarker"``.
2. **Legacy FaceMesh** (``mp.solutions.face_mesh``, removed in mediapipe
   0.10.30 / 1.x, so the ``face_mediapipe`` extra pins
   ``mediapipe>=0.10,<0.10.30``). Yields
   ``landmarks_source="mediapipe-facemesh"``. Verified end-to-end on
   mediapipe 0.10.21 (macosx arm64, CPython 3.12): a real face photo
   yields measured anchors that differ from the box constants.

With neither available the labelled box-ratio estimate stays active.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FaceEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class FaceRegion:
    x: int
    y: int
    width: int
    height: int
    landmarks: list[tuple[int, int]]
    confidence: float
    # "mediapipe-facelandmarker"/"mediapipe-facemesh" = measured;
    # "box-ratio-estimate" = derived from the detection box and carries no
    # geometric information.
    landmarks_source: str = "box-ratio-estimate"


@dataclass(frozen=True)
class FaceAnalysis:
    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[FaceEvidenceSignal]
    limitations: list[str]
    face_count: int
    manipulation_type: str
    confidence: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _imread_unicode(path: Path | str):
    """cv2.imread that survives non-ASCII paths on Windows.

    cv2.imread silently returns None for paths containing non-ASCII
    characters (Korean/CJK filenames) on Windows — read the bytes and
    decode instead. Returns None on any failure, like cv2.imread.
    """
    import cv2
    import numpy as np

    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def analyze_faces(
    path: Path | str,
) -> FaceAnalysis:
    """Analyze an image for face manipulation signs."""
    image_path = Path(path)
    if not image_path.is_file():
        return _error_analysis(f"파일이 존재하지 않습니다: {image_path}")

    extension = image_path.suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}:
        return _error_analysis(f"지원하지 않는 이미지 형식입니다: {extension}")

    try:
        import cv2
        import numpy as np
    except ImportError:
        return _error_analysis("opencv가 설치되어 있지 않습니다. pip install opencv-python로 설치하세요.")

    try:
        image = _imread_unicode(image_path)
        if image is None:
            return _error_analysis("이미지를 읽을 수 없습니다.")
    except Exception as exc:
        return _error_analysis(f"이미지 읽기 오류: {exc}")

    faces = _detect_faces(image)
    if not faces:
        return FaceAnalysis(
            score=0,
            band="unknown",
            band_label="판단 어려움",
            verdict="얼굴이 감지되지 않았습니다.",
            signals=[],
            limitations=["얼굴이 감지되지 않아 분석할 수 없습니다."],
            face_count=0,
            manipulation_type="none",
            confidence="low",
        )

    signals: list[FaceEvidenceSignal] = []
    limitations: list[str] = []

    for face in faces:
        # Boundary blending
        boundary_signal = _boundary_blending(face, image)
        if boundary_signal:
            signals.append(boundary_signal)

        # Reflection analysis
        reflection_signal = _reflection_analysis(face, image)
        if reflection_signal:
            signals.append(reflection_signal)

        # Color temperature
        color_signal = _color_temperature(face, image)
        if color_signal:
            signals.append(color_signal)

        # Lighting-direction consistency (face vs scene)
        lighting_signal = _lighting_consistency(face, image)
        if lighting_signal:
            signals.append(lighting_signal)

    # Multi-face consistency
    if len(faces) > 1:
        multi_face_signal = _multi_face_consistency(faces, image)
        if multi_face_signal:
            signals.append(multi_face_signal)

    # Limitations
    if len(faces) == 1:
        limitations.append("단일 얼굴만 감지되어 다중 얼굴 비교가 불가합니다.")
    landmark_sources = {face.landmarks_source for face in faces}
    if landmark_sources <= MEASURED_LANDMARK_SOURCES and landmark_sources:
        limitations.append("랜드마크는 MediaPipe 실측값입니다. 기하/대칭 검증은 아직 구현되지 않았습니다.")
    else:
        limitations.append("랜드마크가 감지 박스 비율 추정값(landmarks_source=box-ratio-estimate)이며 실측이 아닙니다.")
        limitations.append("랜드마크 기하/대칭 검증은 실측 랜드마크가 없어 미평가입니다.")
        limitations.append("눈 위치는 감지 박스에서 추정한 값이므로 반사 패턴 비교는 참고 수준입니다.")
    limitations.append("로컬 휴리스틱 기반 선별 결과이며, 확정적 판별이 아닙니다.")

    score = min(100, sum(signal.weight for signal in signals))

    if score >= 67:
        band = "high"
        band_label = "높음"
        verdict = "얼굴 조작 의심 신호가 강합니다."
    elif score >= 35:
        band = "medium"
        band_label = "주의"
        verdict = "얼굴에서 몇 가지 의심 신호가 보여 추가 확인이 필요합니다."
    else:
        band = "low"
        band_label = "낮음"
        verdict = "얼굴에서 뚜렷한 조작 의심 신호는 적습니다."

    manipulation_type = _classify_manipulation_type(signals)
    confidence = _calculate_confidence(score, len(faces), len(signals))

    return FaceAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        face_count=len(faces),
        manipulation_type=manipulation_type,
        confidence=confidence,
    )


def _error_analysis(message: str) -> FaceAnalysis:
    return FaceAnalysis(
        score=0,
        band="unknown",
        band_label="판단 어려움",
        verdict=message,
        signals=[],
        limitations=[message],
        face_count=0,
        manipulation_type="unknown",
        confidence="low",
    )


def _detect_faces(image: Any) -> list[FaceRegion]:
    """Detect faces: OpenCV Haar first, MediaPipe FaceMesh as fallback.

    Haar misses valid frontal faces on generated/atypical imagery (measured
    on local samples); when it returns nothing, a whole-image FaceMesh pass
    recovers detection coverage. Regions recovered by the fallback carry the
    ``mediapipe-facemesh-detection`` landmark source.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    regions = []
    if hasattr(cv2, "CascadeClassifier") and hasattr(getattr(cv2, "data", None), "haarcascades"):
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)
        faces = face_cascade.detectMultiScale(gray, 1.1, 4)
        for x, y, w, h in faces:
            landmarks, source = _face_landmarks(image, x, y, w, h)
            regions.append(
                FaceRegion(
                    x=x, y=y, width=w, height=h,
                    landmarks=landmarks, confidence=0.9,
                    landmarks_source=source,
                )
            )
    if regions:
        return regions
    return _mediapipe_detect_faces(image)


def _mediapipe_detect_faces(image: Any, max_faces: int = 3) -> list[FaceRegion]:
    """Whole-image MediaPipe FaceMesh pass used when Haar finds nothing.

    Each returned mesh's landmark extent becomes the face box (expanded
    ~15%); landmarks are honest box estimates, not measured anchors. Returns
    [] when mediapipe is absent or no mesh is found.
    """
    try:
        import cv2
        import mediapipe as mp
    except ImportError:
        return []
    if not hasattr(mp, "solutions"):
        return []

    try:
        face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=max_faces,
            min_detection_confidence=0.5,
        )
        try:
            result = face_mesh.process(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        finally:
            face_mesh.close()
    except Exception:
        return []

    if not result.multi_face_landmarks:
        return []

    img_h, img_w = image.shape[:2]
    regions: list[FaceRegion] = []
    for face_landmarks in result.multi_face_landmarks:
        xs = [lm.x for lm in face_landmarks.landmark]
        ys = [lm.y for lm in face_landmarks.landmark]
        x0, x1 = min(xs) * img_w, max(xs) * img_w
        y0, y1 = min(ys) * img_h, max(ys) * img_h
        bw, bh = x1 - x0, y1 - y0
        if bw < 8 or bh < 8:
            continue
        mx, my = bw * 0.15, bh * 0.15
        x = int(max(0, x0 - mx))
        y = int(max(0, y0 - my))
        w = int(min(img_w, x1 + mx) - x)
        h = int(min(img_h, y1 + my) - y)
        if w < 8 or h < 8:
            continue
        regions.append(
            FaceRegion(
                x=x, y=y, width=w, height=h,
                landmarks=_estimate_landmarks(x, y, w, h),
                confidence=0.7,
                landmarks_source="mediapipe-facemesh-detection",
            )
        )
    return regions


# Landmark sources that carry measured geometry (vs the box-ratio estimate).
MEASURED_LANDMARK_SOURCES = {"mediapipe-facelandmarker", "mediapipe-facemesh"}

# FaceLandmarker (Tasks API) model asset. Bundled under models/ or pointed at
# with DEEPFAKE_LENS_FACE_LANDMARKER; absent → the legacy FaceMesh path.
_FACE_LANDMARKER_ENV = "DEEPFAKE_LENS_FACE_LANDMARKER"
_FACE_LANDMARKER_ASSET = Path(__file__).resolve().parent.parent / "models" / "face_landmarker.task"


def _facelandmarker_model_path() -> Path | None:
    """Resolve the FaceLandmarker .task asset, if one is provisioned."""
    override = os.environ.get(_FACE_LANDMARKER_ENV, "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    return _FACE_LANDMARKER_ASSET if _FACE_LANDMARKER_ASSET.is_file() else None


def _face_landmarks(image, x: int, y: int, w: int, h: int) -> tuple[list[tuple[int, int]], str]:
    """Return (anchor points, source label) for one detected face box.

    Prefers measured MediaPipe anchors (Tasks-API FaceLandmarker first, then
    legacy FaceMesh); falls back to box-ratio estimates so every FaceRegion
    is honest about where its landmarks came from.
    """
    measured = _facelandmarker_landmarks(image, x, y, w, h)
    if measured is not None:
        return measured, "mediapipe-facelandmarker"
    measured = _mediapipe_landmarks(image, x, y, w, h)
    if measured is not None:
        return measured, "mediapipe-facemesh"
    return _estimate_landmarks(x, y, w, h), "box-ratio-estimate"


# MediaPipe FaceMesh canonical indices for the four anchors, averaged where
# a small neighbourhood is more stable than a single point.
_MEDIAPIPE_ANCHOR_INDICES = (
    (33, 133),    # left eye: inner+outer canthus midpoint
    (263, 362),   # right eye: inner+outer canthus midpoint
    (1,),         # nose tip
    (13, 14),     # mouth center: upper/lower inner lip midpoint
)


def _mediapipe_landmarks(image, x: int, y: int, w: int, h: int) -> list[tuple[int, int]] | None:
    """Measure eye/nose/mouth anchors with MediaPipe FaceMesh.

    Returns None when the optional ``face_mediapipe`` extra is not
    installed, when FaceMesh finds no face in the crop, or when the
    measurement fails — callers then fall back to the labelled box-ratio
    estimate.
    """
    try:
        import cv2
        import mediapipe as mp
    except ImportError:
        return None

    crop = image[max(0, y) : y + h, max(0, x) : x + w]
    if crop.size == 0:
        return None

    try:
        face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=1,
            min_detection_confidence=0.5,
        )
        try:
            result = face_mesh.process(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        finally:
            face_mesh.close()
    except Exception:
        return None

    if not result.multi_face_landmarks:
        return None

    mesh = result.multi_face_landmarks[0].landmark
    crop_h, crop_w = crop.shape[:2]

    def anchor(indices: tuple[int, ...]) -> tuple[int, int]:
        px = sum(mesh[i].x for i in indices) / len(indices) * crop_w
        py = sum(mesh[i].y for i in indices) / len(indices) * crop_h
        return (x + int(px), y + int(py))

    return [anchor(indices) for indices in _MEDIAPIPE_ANCHOR_INDICES]


def _facelandmarker_landmarks(image, x: int, y: int, w: int, h: int) -> list[tuple[int, int]] | None:
    """Measure anchors with the Tasks-API FaceLandmarker.

    This is the supported path on mediapipe >= 0.10.30 / 1.x, where the
    legacy ``mp.solutions`` API no longer exists. It needs the
    ``face_landmarker.task`` model asset (see ``_facelandmarker_model_path``);
    returns None when the asset or the Tasks API is absent, or when no face
    is found in the crop.
    """
    model_path = _facelandmarker_model_path()
    if model_path is None:
        return None
    try:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision
    except (ImportError, AttributeError):
        return None

    try:
        crop = image[max(0, y) : y + h, max(0, x) : x + w]
        if crop.size == 0:
            return None
        options = mp_vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp_vision.RunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=0.5,
        )
        with mp_vision.FaceLandmarker.create_from_options(options) as landmarker:
            rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    except Exception:
        return None

    if not result.face_landmarks:
        return None

    # Same canonical 478-point topology as FaceMesh — the shared anchor
    # indices apply unchanged.
    mesh = result.face_landmarks[0]
    crop_h, crop_w = crop.shape[:2]

    def anchor(indices: tuple[int, ...]) -> tuple[int, int]:
        px = sum(mesh[i].x for i in indices) / len(indices) * crop_w
        py = sum(mesh[i].y for i in indices) / len(indices) * crop_h
        return (x + int(px), y + int(py))

    return [anchor(indices) for indices in _MEDIAPIPE_ANCHOR_INDICES]


def _estimate_landmarks(x: int, y: int, w: int, h: int) -> list[tuple[int, int]]:
    """Assume eye/nose/mouth positions from the detection box proportions.

    These are NOT measured landmarks; they only anchor the eye-region
    sampling used by reflection analysis. Geometry checks must not be built
    on them because every derived relation is a constant of the box shape.
    Callers must label them ``landmarks_source="box-ratio-estimate"``.
    """
    # Left eye, right eye, nose tip, mouth center
    left_eye = (x + int(w * 0.35), y + int(h * 0.35))
    right_eye = (x + int(w * 0.65), y + int(h * 0.35))
    nose_tip = (x + int(w * 0.5), y + int(h * 0.55))
    mouth_center = (x + int(w * 0.5), y + int(h * 0.75))
    return [left_eye, right_eye, nose_tip, mouth_center]


def _boundary_blending(face: FaceRegion, image) -> FaceEvidenceSignal | None:
    """Analyze face boundary blending artifacts."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    # Extract face region
    x, y, w, h = face.x, face.y, face.width, face.height
    face_region = image[y:y+h, x:x+w]
    if face_region.size == 0:
        return None

    # Convert to grayscale
    gray = cv2.cvtColor(face_region, cv2.COLOR_BGR2GRAY)

    # Edge detection at boundary
    edges = cv2.Canny(gray, 50, 150)

    # Check boundary edge density
    boundary_width = max(1, int(min(w, h) * 0.1))
    boundary_mask = np.zeros_like(edges)
    boundary_mask[:boundary_width, :] = 1
    boundary_mask[-boundary_width:, :] = 1
    boundary_mask[:, :boundary_width] = 1
    boundary_mask[:, -boundary_width:] = 1

    boundary_edges = np.sum(edges * boundary_mask)
    total_edges = np.sum(edges)

    if total_edges > 0:
        boundary_ratio = boundary_edges / total_edges
        if boundary_ratio > 0.7:
            return FaceEvidenceSignal(
                "경계 블렌딩 의심",
                f"얼굴 경계에서 에지 비율({boundary_ratio:.2f})이 높아 합성 경계일 수 있습니다.",
                18,
            )

    return None


def _reflection_analysis(face: FaceRegion, image) -> FaceEvidenceSignal | None:
    """Analyze eye reflection patterns."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    if len(face.landmarks) < 2:
        return None

    left_eye, right_eye = face.landmarks[0], face.landmarks[1]

    # Extract eye regions
    eye_size = max(5, int(face.width * 0.08))

    def get_eye_region(center):
        cx, cy = center
        x1 = max(0, cx - eye_size)
        y1 = max(0, cy - eye_size)
        x2 = min(image.shape[1], cx + eye_size)
        y2 = min(image.shape[0], cy + eye_size)
        return image[y1:y2, x1:x2]

    left_region = get_eye_region(left_eye)
    right_region = get_eye_region(right_eye)

    if left_region.size == 0 or right_region.size == 0:
        return None

    # Compare brightness patterns
    left_brightness = np.mean(cv2.cvtColor(left_region, cv2.COLOR_BGR2GRAY))
    right_brightness = np.mean(cv2.cvtColor(right_region, cv2.COLOR_BGR2GRAY))

    brightness_diff = abs(left_brightness - right_brightness)
    if brightness_diff > 40:
        return FaceEvidenceSignal(
            "반사 패턴 불일치",
            f"양쪽 눈 밝기 차이({brightness_diff:.1f})가 커서 인위적 합성일 수 있습니다.",
            15,
        )

    return None


def _color_temperature(face: FaceRegion, image) -> FaceEvidenceSignal | None:
    """Analyze color temperature consistency between face and surrounding."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    x, y, w, h = face.x, face.y, face.width, face.height

    # Face region
    face_region = image[y:y+h, x:x+w]
    if face_region.size == 0:
        return None

    # Surrounding region (neck/hair area)
    surround_y = min(image.shape[0], y + h)
    surround_h = min(int(h * 0.3), image.shape[0] - surround_y)
    if surround_h <= 0:
        return None

    surround_region = image[surround_y:surround_y+surround_h, x:x+w]
    if surround_region.size == 0:
        return None

    # Compare color histograms
    face_hsv = cv2.cvtColor(face_region, cv2.COLOR_BGR2HSV)
    surround_hsv = cv2.cvtColor(surround_region, cv2.COLOR_BGR2HSV)

    face_hue_mean = np.mean(face_hsv[:, :, 0])
    surround_hue_mean = np.mean(surround_hsv[:, :, 0])

    # OpenCV hue is circular in [0, 179]; compare on the circle so hue 5 vs
    # hue 175 (the same red family) does not read as a 170-unit mismatch.
    hue_diff = abs(float(face_hue_mean) - float(surround_hue_mean))
    circular_hue_diff = min(hue_diff, 180.0 - hue_diff)
    if circular_hue_diff > 15:
        return FaceEvidenceSignal(
            "색온도 불일치",
            f"얼굴과 주변 영역 색상 차이({circular_hue_diff:.1f})가 커서 합성일 수 있습니다.",
            12,
        )

    return None


def _multi_face_consistency(faces: list[FaceRegion], image) -> FaceEvidenceSignal | None:
    """Check consistency across multiple faces."""
    if len(faces) < 2:
        return None

    # Check if faces have similar sizes (could indicate pasted faces)
    sizes = [f.width * f.height for f in faces]
    if len(sizes) > 1:
        size_cv = (max(sizes) - min(sizes)) / max(1, max(sizes))
        if size_cv < 0.1 and len(faces) > 2:
            return FaceEvidenceSignal(
                "다중 얼굴 크기 균일",
                f"여러 얼굴의 크기가 비정상적으로 균일합니다 ({len(faces)}개 얼굴).",
                8,
            )

    return None


def _classify_manipulation_type(signals: list[FaceEvidenceSignal]) -> str:
    """Classify the type of manipulation based on signals."""
    signal_titles = {s.title for s in signals}

    if "경계 블렌딩 의심" in signal_titles:
        return "face_swap"
    if "반사 패턴 불일치" in signal_titles:
        return "reenactment"
    if "색온도 불일치" in signal_titles:
        return "face_swap"
    if "다중 얼굴 크기 균일" in signal_titles:
        return "face_paste"
    return "unknown"


def _calculate_confidence(score: int, face_count: int, signal_count: int) -> str:
    """Calculate confidence level."""
    if score >= 67 and face_count >= 1 and signal_count >= 2:
        return "high"
    if score >= 35 and signal_count >= 1:
        return "medium"
    return "low"


def _lighting_consistency(face: FaceRegion, image) -> FaceEvidenceSignal | None:
    """Compare the face's shading direction against the scene's light slope.

    A pasted/AI-composited face often carries illumination from a different
    photo. The face is approximately convex, so its left/right and up/down
    luminance asymmetry approximates the light azimuth; the surrounding
    ring's least-squares luminance ramp estimates the scene's overall
    light direction. A large angular mismatch is a screening signal, not
    proof — mixed lighting and flat studio light both suppress it.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    x, y, w, h = face.x, face.y, face.width, face.height
    if w < 40 or h < 40:
        return None
    face_region = image[y : y + h, x : x + w]
    if face_region.size == 0:
        return None
    gray_face = cv2.cvtColor(face_region, cv2.COLOR_BGR2GRAY).astype(float)

    # Face shading asymmetry — left vs right and top vs bottom halves.
    mid_x, mid_y = w // 2, h // 2
    fx = float(gray_face[:, :mid_x].mean() - gray_face[:, mid_x:].mean())
    fy = float(gray_face[:mid_y, :].mean() - gray_face[mid_y:, :].mean())
    face_strength = float(np.hypot(fx, fy))
    if face_strength < 4.0:
        return None  # evenly lit face — no direction to compare

    # Scene light slope — fit I = a*x + b*y + c over the ring around the
    # face (1.8x box, face excluded).
    img_gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(float)
    x0, y0 = max(0, int(x - 0.4 * w)), max(0, int(y - 0.4 * h))
    x1, y1 = min(image.shape[1], int(x + 1.4 * w)), min(image.shape[0], int(y + 1.4 * h))
    if (x1 - x0) < w or (y1 - y0) < h:
        return None
    coords, values = [], []
    ys, xs = np.mgrid[y0:y1, x0:x1]
    inside = (xs >= x) & (xs < x + w) & (ys >= y) & (ys < y + h)
    ring_x, ring_y = xs[~inside], ys[~inside]
    ring_v = img_gray[y0:y1, x0:x1][~inside]
    if len(ring_v) < 200 or float(ring_v.std()) < 3.0:
        return None  # featureless background — no scene direction
    a_mat = np.stack([ring_x, ring_y, np.ones_like(ring_x)], axis=1)
    coef, *_ = np.linalg.lstsq(a_mat, ring_v, rcond=None)
    gx, gy = float(coef[0]), float(coef[1])
    scene_strength = float(np.hypot(gx, gy))
    if scene_strength < 0.02:
        return None  # uniform background — no direction to compare

    # Sign convention: face asymmetry fx>0 means LEFT darker → light from
    # right, i.e. direction +x. The scene ramp gradient points toward
    # increasing brightness = light direction.
    face_dir = np.arctan2(-fy, -fx)  # darker side → opposite of light
    scene_dir = np.arctan2(gy, gx)
    diff = abs(face_dir - scene_dir)
    diff = min(diff, 2 * np.pi - diff)
    degrees = float(np.degrees(diff))
    if degrees >= 60:
        return FaceEvidenceSignal(
            "조명 방향 불일치",
            f"얼굴 음영 방향과 장면 조명 방향이 약 {degrees:.0f}도 어긋납니다 — 다른 조명 환경의 합성 얼굴 후보.",
            14,
        )
    return None

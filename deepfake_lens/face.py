"""Face manipulation detection module.

Detects face swap, face reenactment, and lip sync manipulation
using geometric analysis, boundary blending, and reflection patterns.
"""

from __future__ import annotations

import math
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
        image = cv2.imread(str(image_path))
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
        # Landmark consistency
        landmark_signal = _landmark_consistency(face)
        if landmark_signal:
            signals.append(landmark_signal)

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

        # Symmetry analysis
        symmetry_signal = _symmetry_analysis(face)
        if symmetry_signal:
            signals.append(symmetry_signal)

    # Multi-face consistency
    if len(faces) > 1:
        multi_face_signal = _multi_face_consistency(faces, image)
        if multi_face_signal:
            signals.append(multi_face_signal)

    # Limitations
    if len(faces) == 1:
        limitations.append("단일 얼굴만 감지되어 다중 얼굴 비교가 불가합니다.")
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
    """Detect faces using OpenCV Haar cascade."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    face_cascade = cv2.CascadeClassifier(cascade_path)
    faces = face_cascade.detectMultiScale(gray, 1.1, 4)

    regions = []
    for x, y, w, h in faces:
        # Simple landmark estimation (eye, nose, mouth positions)
        landmarks = _estimate_landmarks(x, y, w, h)
        regions.append(FaceRegion(x=x, y=y, width=w, height=h, landmarks=landmarks, confidence=0.9))

    return regions


def _estimate_landmarks(x: int, y: int, w: int, h: int) -> list[tuple[int, int]]:
    """Estimate basic facial landmarks from bounding box."""
    # Left eye, right eye, nose tip, mouth center
    left_eye = (x + int(w * 0.35), y + int(h * 0.35))
    right_eye = (x + int(w * 0.65), y + int(h * 0.35))
    nose_tip = (x + int(w * 0.5), y + int(h * 0.55))
    mouth_center = (x + int(w * 0.5), y + int(h * 0.75))
    return [left_eye, right_eye, nose_tip, mouth_center]


def _landmark_consistency(face: FaceRegion) -> FaceEvidenceSignal | None:
    """Check facial landmark geometric consistency."""
    if len(face.landmarks) < 4:
        return None

    left_eye, right_eye, nose_tip, mouth_center = face.landmarks[:4]

    # Eye symmetry
    eye_center_x = (left_eye[0] + right_eye[0]) / 2
    nose_offset = abs(nose_tip[0] - eye_center_x)

    # Nose should be roughly centered between eyes
    if nose_offset > face.width * 0.15:
        return FaceEvidenceSignal(
            "랜드마크 비대칭",
            f"코 위치가 양쪽 눈 중앙에서 벗어나 있습니다 (편차: {nose_offset:.1f}px).",
            20,
        )

    # Eye distance ratio
    eye_distance = math.sqrt((right_eye[0] - left_eye[0]) ** 2 + (right_eye[1] - left_eye[1]) ** 2)
    face_width_ratio = eye_distance / face.width

    if face_width_ratio < 0.2 or face_width_ratio > 0.6:
        return FaceEvidenceSignal(
            "비정상적 눈 간격",
            f"양쪽 눈 사이 거리가 얼굴 너비 대비 비정상적입니다 ({face_width_ratio:.2f}).",
            15,
        )

    # Mouth-nose distance
    mouth_nose_distance = math.sqrt((mouth_center[0] - nose_tip[0]) ** 2 + (mouth_center[1] - nose_tip[1]) ** 2)
    face_height_ratio = mouth_nose_distance / face.height

    if face_height_ratio < 0.1 or face_height_ratio > 0.4:
        return FaceEvidenceSignal(
            "코-입 거리 이상",
            f"코와 입 사이 거리가 비정상적입니다 ({face_height_ratio:.2f}).",
            12,
        )

    return None


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

    hue_diff = abs(face_hue_mean - surround_hue_mean)
    if hue_diff > 15:
        return FaceEvidenceSignal(
            "색온도 불일치",
            f"얼굴과 주변 영역 색상 차이({hue_diff:.1f})가 커서 합성일 수 있습니다.",
            12,
        )

    return None


def _symmetry_analysis(face: FaceRegion) -> FaceEvidenceSignal | None:
    """Analyze facial symmetry."""
    # Simple symmetry check using landmarks
    if len(face.landmarks) < 2:
        return None

    left_eye, right_eye = face.landmarks[0], face.landmarks[1]
    center_x = face.x + face.width // 2

    left_dist = abs(left_eye[0] - center_x)
    right_dist = abs(right_eye[0] - center_x)

    if left_dist > 0 and right_dist > 0:
        symmetry_ratio = min(left_dist, right_dist) / max(left_dist, right_dist)
        if symmetry_ratio < 0.7:
            return FaceEvidenceSignal(
                "대칭성 위반",
                f"양쪽 눈 위치 대칭 비율({symmetry_ratio:.2f})이 낮습니다.",
                10,
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

    if "랜드마크 비대칭" in signal_titles or "대칭성 위반" in signal_titles:
        return "face_swap"
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

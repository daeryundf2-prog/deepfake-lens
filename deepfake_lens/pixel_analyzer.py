"""Pixel analysis module for AI-generated image detection.

Analyzes image pixels to detect AI generation artifacts
without relying on metadata.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class PixelEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class QuickPixelAnalysis:
    """Result of the cv2-based quick pixel screen.

    Deliberately not named ``PixelAnalysis``: that name belongs to the main
    ensemble result in ``pixel.py`` and the two models are incompatible.
    """

    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[PixelEvidenceSignal]
    limitations: list[str]
    features: dict[str, float]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def analyze_pixels(path: Path | str) -> QuickPixelAnalysis:
    """Analyze image pixels for AI generation signs."""
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
        return _error_analysis("opencv/numpy가 설치되어 있지 않습니다.")

    try:
        image = cv2.imread(str(image_path))
        if image is None:
            return _error_analysis("이미지를 읽을 수 없습니다.")
    except Exception as exc:
        return _error_analysis(f"이미지 읽기 오류: {exc}")

    signals: list[PixelEvidenceSignal] = []
    limitations: list[str] = []
    features: dict[str, float] = {}

    # Extract features
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    features = _extract_features(gray, image)

    # Analyze features
    spectral_signal = _spectral_analysis(features)
    if spectral_signal:
        signals.append(spectral_signal)

    noise_signal = _noise_analysis(features)
    if noise_signal:
        signals.append(noise_signal)

    edge_signal = _edge_analysis(features)
    if edge_signal:
        signals.append(edge_signal)

    color_signal = _color_analysis(image)
    if color_signal:
        signals.append(color_signal)

    texture_signal = _texture_analysis(features)
    if texture_signal:
        signals.append(texture_signal)

    # Limitations
    limitations.append("픽셀 분석은 통계적 휴리스틱 기반입니다.")
    limitations.append("실제 AI 생성 여부는 추가 검증이 필요합니다.")

    score = min(100, sum(signal.weight for signal in signals))

    if score >= 35:
        band = "high"
        band_label = "높음"
        verdict = "픽셀 분석에서 AI 생성 의심 신호가 강합니다."
    elif score >= 20:
        band = "medium"
        band_label = "주의"
        verdict = "픽셀 분석에서 몇 가지 의심 신호가 보입니다."
    else:
        band = "low"
        band_label = "낮음"
        verdict = "픽셀 분석에서 뚜렷한 AI 생성 의심 신호는 적습니다."

    return QuickPixelAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        features=features,
    )


def _error_analysis(message: str) -> QuickPixelAnalysis:
    return QuickPixelAnalysis(
        score=0,
        band="unknown",
        band_label="판단 어려움",
        verdict=message,
        signals=[],
        limitations=[message],
        features={},
    )


def _extract_features(gray, image) -> dict[str, float]:
    """Extract pixel-level features from image."""
    import cv2
    import numpy as np

    features = {}

    # Basic statistics
    features["mean"] = float(np.mean(gray))
    features["std"] = float(np.std(gray))
    features["min"] = float(np.min(gray))
    features["max"] = float(np.max(gray))

    # Histogram features
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
    hist = hist.flatten() / hist.sum()
    features["hist_entropy"] = float(-np.sum(hist * np.log2(hist + 1e-10)))
    features["hist_skewness"] = float(np.mean(((gray - features["mean"]) / (features["std"] + 1e-10)) ** 3))
    features["hist_kurtosis"] = float(np.mean(((gray - features["mean"]) / (features["std"] + 1e-10)) ** 4) - 3)

    # Edge features
    edges = cv2.Canny(gray, 50, 150)
    features["edge_density"] = float(np.mean(edges) / 255)
    features["edge_mean"] = float(np.mean(edges))

    # Texture features (Laplacian)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    features["texture_variance"] = float(np.var(laplacian))
    features["texture_mean"] = float(np.mean(np.abs(laplacian)))

    # Frequency domain features (simplified)
    f = np.fft.fft2(gray.astype(float))
    fshift = np.fft.fftshift(f)
    magnitude = np.abs(fshift)
    features["freq_mean"] = float(np.mean(magnitude))
    features["freq_std"] = float(np.std(magnitude))

    # Color features (from BGR image)
    if len(image.shape) == 3:
        b, g, r = cv2.split(image)
        features["color_mean_r"] = float(np.mean(r))
        features["color_mean_g"] = float(np.mean(g))
        features["color_mean_b"] = float(np.mean(b))
        features["color_std_r"] = float(np.std(r))
        features["color_std_g"] = float(np.std(g))
        features["color_std_b"] = float(np.std(b))

    return features


def _spectral_analysis(features: dict[str, float]) -> PixelEvidenceSignal | None:
    """Analyze spectral characteristics."""
    if features.get("freq_std", 0) > 5000 and features.get("edge_density", 0) < 0.2:
        return PixelEvidenceSignal(
            "비자연적 주파수 분포",
            "주파수 분포가 비정상적으로 균일합니다.",
            25,
        )

    if features.get("hist_entropy", 0) < 6.0:
        return PixelEvidenceSignal(
            "낮은 엔트로피",
            f"히스토그램 엔트로피({features['hist_entropy']:.2f})가 낮아 매끄러운 이미지입니다.",
            20,
        )

    return None


def _noise_analysis(features: dict[str, float]) -> PixelEvidenceSignal | None:
    """Analyze noise patterns."""
    if features.get("texture_variance", 0) < 500 and features.get("std", 0) > 20:
        return PixelEvidenceSignal(
            "낮은 노이즈 레벨",
            f"텍스처 분산({features['texture_variance']:.2f})이 낮아 과도하게 매끄럽습니다.",
            18,
        )

    return None


def _edge_analysis(features: dict[str, float]) -> PixelEvidenceSignal | None:
    """Analyze edge patterns."""
    if features.get("edge_density", 0) > 0.15 and features.get("std", 0) < 35:
        return PixelEvidenceSignal(
            "비자연적 엣지 패턴",
            "엣지 밀도가 높은데 대비가 낮습니다.",
            15,
        )

    return None


def _color_analysis(image) -> PixelEvidenceSignal | None:
    """Analyze color distribution."""
    import cv2
    import numpy as np

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)

    saturation_mean = float(np.mean(s))
    saturation_std = float(np.std(s))

    if saturation_mean > 100 and saturation_std < 50:
        return PixelEvidenceSignal(
            "비자연적 채도 패턴",
            f"채도 평균({saturation_mean:.1f})이 높고 표준편차({saturation_std:.1f})가 낮습니다.",
            15,
        )

    return None


def _texture_analysis(features: dict[str, float]) -> PixelEvidenceSignal | None:
    """Analyze texture patterns."""
    if features.get("texture_mean", 0) < 10 and features.get("std", 0) > 30:
        return PixelEvidenceSignal(
            "균일한 텍스처",
            "텍스처가 비정상적으로 균일합니다.",
            12,
        )

    return None

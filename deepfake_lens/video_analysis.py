"""Video temporal analysis module.

Detects temporal inconsistencies, unnatural motion patterns, and
encoding artifacts in video files using optical flow and frame analysis.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path


SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".flv"}
DEFAULT_FRAME_SAMPLE_RATE = 1.0  # frames per second
DEFAULT_MAX_FRAMES = 100


@dataclass(frozen=True)
class VideoEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class FrameAnalysis:
    frame_number: int
    timestamp: float
    brightness: float
    contrast: float
    blur_score: float
    edge_density: float


@dataclass(frozen=True)
class VideoTemporalAnalysis:
    score: int
    band: str
    band_label: str
    verdict: str
    signals: list[VideoEvidenceSignal]
    limitations: list[str]
    frame_count: int
    duration_seconds: float
    fps: float
    resolution: tuple[int, int]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def analyze_video_temporal(
    path: Path | str,
    *,
    frame_sample_rate: float = DEFAULT_FRAME_SAMPLE_RATE,
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> VideoTemporalAnalysis:
    """Analyze a video file for temporal inconsistencies."""
    video_path = Path(path)
    if not video_path.is_file():
        return _error_analysis(f"파일이 존재하지 않습니다: {video_path}")

    extension = video_path.suffix.lower()
    if extension not in SUPPORTED_VIDEO_EXTENSIONS:
        return _error_analysis(f"지원하지 않는 비디오 형식입니다: {extension}")

    try:
        import cv2
    except ImportError:
        return _error_analysis("opencv가 설치되어 있지 않습니다. pip install opencv-python로 설치하세요.")

    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return _error_analysis("비디오를 열 수 없습니다.")
    except Exception as exc:
        return _error_analysis(f"비디오 열기 오류: {exc}")

    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = frame_count / max(1, fps)
    finally:
        cap.release()

    # Sample frames
    frame_analyses = _sample_frames(video_path, frame_sample_rate, max_frames)
    if not frame_analyses:
        return _error_analysis("프레임을 추출할 수 없습니다.")

    signals: list[VideoEvidenceSignal] = []
    limitations: list[str] = []

    # Temporal stability signals are meaningless on effectively frozen
    # footage (screen recordings, fixed tripods): every real video is then
    # "unnaturally stable" too. Report it instead of scoring it.
    if _is_static_footage(frame_analyses):
        limitations.append("정적 영상(화면 녹화/고정 삼각대 추정)이라 안정성 기반 신호는 판별에 사용할 수 없습니다.")
    else:
        # Brightness consistency
        brightness_signal = _brightness_consistency(frame_analyses)
        if brightness_signal:
            signals.append(brightness_signal)

        # Contrast consistency
        contrast_signal = _contrast_consistency(frame_analyses)
        if contrast_signal:
            signals.append(contrast_signal)

        # Blur pattern
        blur_signal = _blur_pattern(frame_analyses)
        if blur_signal:
            signals.append(blur_signal)

        # Edge density changes
        edge_signal = _edge_density_changes(frame_analyses)
        if edge_signal:
            signals.append(edge_signal)

    # Frame rate analysis
    fps_signal = _fps_analysis(fps, duration)
    if fps_signal:
        signals.append(fps_signal)

    # Resolution analysis
    resolution_signal = _resolution_analysis(width, height)
    if resolution_signal:
        signals.append(resolution_signal)

    # Limitations
    if len(frame_analyses) < 10:
        limitations.append("분석된 프레임 수가 적어 결과가 불안정할 수 있습니다.")
    limitations.append("로컬 휴리스틱 기반 선별 결과이며, 확정적 판별이 아닙니다.")

    score = min(100, sum(signal.weight for signal in signals))

    if score >= 67:
        band = "high"
        band_label = "높음"
        verdict = "비디오에서 템포럴 이상 신호가 강합니다."
    elif score >= 35:
        band = "medium"
        band_label = "주의"
        verdict = "비디오에서 몇 가지 이상 신호가 보여 추가 확인이 필요합니다."
    else:
        band = "low"
        band_label = "낮음"
        verdict = "비디오에서 뚜렷한 템포럴 이상 신호는 적습니다."

    return VideoTemporalAnalysis(
        score=score,
        band=band,
        band_label=band_label,
        verdict=verdict,
        signals=signals,
        limitations=limitations,
        frame_count=frame_count,
        duration_seconds=duration,
        fps=fps,
        resolution=(width, height),
    )


def _error_analysis(message: str) -> VideoTemporalAnalysis:
    return VideoTemporalAnalysis(
        score=0,
        band="unknown",
        band_label="판단 어려움",
        verdict=message,
        signals=[],
        limitations=[message],
        frame_count=0,
        duration_seconds=0,
        fps=0,
        resolution=(0, 0),
    )


def _sample_frames(
    path: Path,
    sample_rate: float,
    max_frames: int,
) -> list[FrameAnalysis]:
    """Sample frames from video at specified rate."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []

    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_interval = max(1, int(fps / sample_rate))

        analyses = []
        frame_idx = 0

        while len(analyses) < max_frames and frame_idx < total_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            timestamp = frame_idx / max(1, fps)

            brightness = float(np.mean(gray))
            contrast = float(np.std(gray))
            blur_score = _calculate_blur_score(gray)
            edge_density = _calculate_edge_density(gray)

            analyses.append(FrameAnalysis(
                frame_number=frame_idx,
                timestamp=timestamp,
                brightness=brightness,
                contrast=contrast,
                blur_score=blur_score,
                edge_density=edge_density,
            ))

            frame_idx += frame_interval

        return analyses
    finally:
        cap.release()


def _calculate_blur_score(gray) -> float:
    """Calculate blur score using Laplacian variance."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return 0.0

    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    return float(np.var(laplacian))


def _calculate_edge_density(gray) -> float:
    """Calculate edge density using Canny edge detection."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return 0.0

    edges = cv2.Canny(gray, 50, 150)
    return float(np.mean(edges)) / 255.0


def _is_static_footage(frames: list[FrameAnalysis]) -> bool:
    """Detect effectively frozen footage where stability signals carry no
    information (screen recordings, locked-off tripods)."""
    if len(frames) < 10:
        return False

    def max_step(values: list[float]) -> float:
        return max((abs(values[i] - values[i - 1]) for i in range(1, len(values))), default=0.0)

    def spread(values: list[float]) -> float:
        mean = sum(values) / len(values)
        return max(abs(value - mean) for value in values)

    brightnesses = [f.brightness for f in frames]
    contrasts = [f.contrast for f in frames]
    blurs = [f.blur_score for f in frames]
    edges = [f.edge_density for f in frames]

    frozen = (
        max_step(brightnesses) < 3.0
        and spread(brightnesses) < 3.0
        and max_step(contrasts) < 3.0
        and spread(contrasts) < 3.0
        and max_step(blurs) < 25.0
        and max_step(edges) < 0.01
    )
    return frozen


def _brightness_consistency(frames: list[FrameAnalysis]) -> VideoEvidenceSignal | None:
    """Check for brightness inconsistencies across frames."""
    if len(frames) < 5:
        return None

    brightnesses = [f.brightness for f in frames]
    mean_b = sum(brightnesses) / len(brightnesses)
    std_b = math.sqrt(sum((b - mean_b) ** 2 for b in brightnesses) / len(brightnesses))

    # Unnaturally stable brightness
    if std_b < 2.0 and len(frames) > 20:
        return VideoEvidenceSignal(
            "비자연적 밝기 안정성",
            f"프레임 간 밝기 표준편차({std_b:.2f})가 매우 낮습니다.",
            18,
        )

    # Sudden brightness changes: a single isolated cut inside otherwise
    # stable footage is the suspicious case. Regular editing (multiple cuts)
    # is normal in real footage and must not fire.
    max_change = max(abs(brightnesses[i] - brightnesses[i-1]) for i in range(1, len(brightnesses)))
    large_changes = sum(1 for i in range(1, len(brightnesses)) if abs(brightnesses[i] - brightnesses[i-1]) > 50)
    if max_change > 50 and large_changes == 1:
        return VideoEvidenceSignal(
            "급격한 밝기 변화",
            f"최대 프레임 간 밝기 변화({max_change:.1f})가 큽니다.",
            15,
        )

    return None


def _contrast_consistency(frames: list[FrameAnalysis]) -> VideoEvidenceSignal | None:
    """Check for contrast inconsistencies."""
    if len(frames) < 5:
        return None

    contrasts = [f.contrast for f in frames]
    mean_c = sum(contrasts) / len(contrasts)
    std_c = math.sqrt(sum((c - mean_c) ** 2 for c in contrasts) / len(contrasts))

    # Unnaturally stable contrast
    if std_c < 1.0 and len(frames) > 20:
        return VideoEvidenceSignal(
            "비자연적 대비 안정성",
            f"프레임 간 대비 표준편차({std_c:.2f})가 매우 낮습니다.",
            15,
        )

    return None


def _blur_pattern(frames: list[FrameAnalysis]) -> VideoEvidenceSignal | None:
    """Check for unnatural blur patterns."""
    if len(frames) < 5:
        return None

    blur_scores = [f.blur_score for f in frames]
    mean_blur = sum(blur_scores) / len(blur_scores)
    std_blur = math.sqrt(sum((b - mean_blur) ** 2 for b in blur_scores) / len(blur_scores))

    # Unnaturally consistent blur (possible synthetic)
    if std_blur < 10 and mean_blur > 100 and len(frames) > 20:
        return VideoEvidenceSignal(
            "균일한 블러 패턴",
            f"프레임 간 블러 점수 표준편차({std_blur:.2f})가 낮습니다.",
            12,
        )

    # Sudden blur changes (possible editing)
    max_blur_change = max(abs(blur_scores[i] - blur_scores[i-1]) for i in range(1, len(blur_scores)))
    if max_blur_change > 500:
        return VideoEvidenceSignal(
            "급격한 블러 변화",
            f"최대 프레임 간 블러 변화({max_blur_change:.1f})가 큽니다.",
            10,
        )

    return None


def _edge_density_changes(frames: list[FrameAnalysis]) -> VideoEvidenceSignal | None:
    """Check for edge density changes."""
    if len(frames) < 5:
        return None

    edge_densities = [f.edge_density for f in frames]
    mean_e = sum(edge_densities) / len(edge_densities)
    std_e = math.sqrt(sum((e - mean_e) ** 2 for e in edge_densities) / len(edge_densities))

    # Unnaturally stable edge density
    if std_e < 0.01 and len(frames) > 20:
        return VideoEvidenceSignal(
            "비자연적 에지 밀도 안정성",
            f"프레임 간 에지 밀도 표준편차({std_e:.4f})가 매우 낮습니다.",
            12,
        )

    return None


def _fps_analysis(fps: float, duration: float) -> VideoEvidenceSignal | None:
    """Analyze frame rate for suspicious patterns."""
    # Unusual frame rates
    common_fps = [23.976, 24, 25, 29.97, 30, 50, 59.94, 60]
    if fps > 0:
        min_diff = min(abs(fps - cfps) for cfps in common_fps)
        if min_diff > 1.0:
            return VideoEvidenceSignal(
                "비표준 프레임 레이트",
                f"프레임 레이트({fps:.3f}fps)가 일반적이지 않습니다.",
                8,
            )

    return None


def _resolution_analysis(width: int, height: int) -> VideoEvidenceSignal | None:
    """Analyze resolution for suspicious patterns."""
    if width <= 0 or height <= 0:
        return None

    # Unusual resolutions
    common_widths = [320, 480, 640, 720, 1280, 1920, 2560, 3840]
    common_heights = [240, 360, 480, 720, 1080, 1440, 2160]

    width_match = any(abs(width - cw) < 10 for cw in common_widths)
    height_match = any(abs(height - ch) < 10 for ch in common_heights)

    if not width_match and not height_match:
        return VideoEvidenceSignal(
            "비표준 해상도",
            f"해상도({width}x{height})가 일반적이지 않습니다.",
            5,
        )

    return None

"""Heuristic audio-visual sync (lip-sync) probe.

A full lip-sync verifier uses a learned SyncNet-style embedding; this module
implements the zero-asset coarse version — correlate the audio RMS envelope
with a mouth-region openness proxy (darkness of the lower-center face ROI)
across candidate lag offsets. Real talking-head video shows a stable, small
positive correlation at a near-zero lag; face-swapped or poorly dubbed video
tends to decorrelate or sit at a large offset.

Both halves are optional-dependency guarded: missing ffmpeg, cv2, librosa
inputs, a face, or an audio track all degrade to ``available=False`` rather
than a fabricated score.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

# Correlation below this with clear speech activity = mismatch candidate.
_WEAK_CORRELATION = 0.12
# Offsets beyond ~0.5 s are implausible for a genuine in-camera recording.
_LARGE_OFFSET_SECONDS = 0.5
_FFMPEG_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class LipsyncAnalysis:
    """Coarse audio-visual sync measurement."""

    available: bool
    score: int  # 0-100 suspicion of sync mismatch
    verdict: str
    best_correlation: float | None
    best_lag_seconds: float | None
    mouth_samples: int
    speech_activity: float | None  # envelope std — gate for the verdict
    limitations: list[str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def analyze_lipsync(path: Path | str, *, max_seconds: float = 20.0) -> LipsyncAnalysis:
    """Measure audio-envelope vs mouth-motion correlation for a video file."""
    video_path = Path(path)
    limitations: list[str] = [
        "학습된 SyncNet 모델이 아닌 저해상 상관 휴리스틱입니다.",
        "얼굴이 작거나 측면이면 입 영역 프록시가 불안정합니다.",
    ]
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        return _unavailable(limitations, "cv2/numpy가 없어 립싱크 분석을 건너뜁니다.")
    if shutil.which("ffmpeg") is None:
        return _unavailable(limitations, "ffmpeg가 없어 오디오 트랙을 추출할 수 없습니다.")

    envelope, env_dt = _audio_envelope(video_path, max_seconds=max_seconds)
    if not envelope:
        return _unavailable(limitations, "오디오 트랙이 없거나 추출에 실패했습니다.")
    mouth, mouth_dt = _mouth_openness_series(video_path, max_seconds=max_seconds)
    if len(mouth) < 20:
        return _unavailable(limitations, "얼굴/입 영역을 충분히 샘플링하지 못했습니다.")

    import numpy as np

    env = np.asarray(envelope, dtype=float)
    mou = np.asarray(mouth, dtype=float)
    # Resample the finer series onto the coarser time base (~same grid).
    if env_dt < mouth_dt:
        t = np.arange(len(mou)) * mouth_dt
        env = np.interp(t, np.arange(len(env)) * env_dt, env)
    else:
        t = np.arange(len(env)) * env_dt
        mou = np.interp(t, np.arange(len(mou)) * mouth_dt, mou)
    dt = mouth_dt if env_dt < mouth_dt else env_dt

    speech_activity = float(env.std())
    if speech_activity < 1e-4 or mou.std() < 1e-4:
        limitations.append("음성 활동 또는 입 움직임 변화가 거의 없어 동기 판별이 불가합니다.")
        return LipsyncAnalysis(True, 0, "동기 판별 불가 — 활동량 부족", None, None, len(mouth), speech_activity, limitations)

    env_z = (env - env.mean()) / env.std()
    mou_z = (mou - mou.mean()) / mou.std()
    max_lag = int(2.0 / dt)
    best_corr, best_lag = -1.0, 0.0
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            a, b = env_z[-lag:], mou_z[: lag]
        elif lag > 0:
            a, b = env_z[:-lag], mou_z[lag:]
        else:
            a, b = env_z, mou_z
        if len(a) < 20:
            continue
        corr = float(np.dot(a, b) / len(a))
        if corr > best_corr:
            best_corr, best_lag = corr, lag * dt

    if best_corr < _WEAK_CORRELATION:
        score = 30
        verdict = f"음성-입 움직임 상관이 거의 없습니다(r={best_corr:.2f}) — 더빙/얼굴 합성 의심."
    elif abs(best_lag) > _LARGE_OFFSET_SECONDS:
        score = 25
        verdict = f"동기 오프셋이 {best_lag:+.2f}초로 큽니다 — 오디오 교체/싱크 조작 의심."
    else:
        score = 0
        verdict = f"음성-입 움직임이 동기화되어 있습니다(r={best_corr:.2f}, lag={best_lag:+.2f}s)."
    return LipsyncAnalysis(True, score, verdict, round(best_corr, 4), round(best_lag, 3), len(mouth), round(speech_activity, 5), limitations)


def _audio_envelope(video_path: Path, *, max_seconds: float) -> tuple[list[float], float]:
    """Extract mono 8 kHz audio via ffmpeg and return (RMS envelope, dt).

    Uses the stdlib ``wave`` reader on PCM output so librosa is not required.
    Envelope window is 40 ms — fine enough to track syllable-rate motion.
    """
    import numpy as np
    import wave

    window_seconds = 0.04
    rate = 8000
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(video_path),
                "-t", f"{max_seconds:.1f}",
                "-vn", "-ac", "1", "-ar", str(rate), "-f", "wav", str(tmp_path),
            ],
            capture_output=True,
            timeout=_FFMPEG_TIMEOUT_SECONDS,
            check=True,
        )
        with wave.open(str(tmp_path), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
            width = handle.getsampwidth()
        samples = np.frombuffer(frames, dtype=np.int16 if width == 2 else np.uint8).astype(float)
        if width == 1:
            samples -= 128.0
        hop = max(1, int(rate * window_seconds))
        envelope = [float(np.sqrt(np.mean(samples[i : i + hop] ** 2))) for i in range(0, len(samples) - hop, hop)]
        return envelope, window_seconds
    except (subprocess.SubprocessError, wave.Error, OSError):
        return [], window_seconds
    finally:
        tmp_path.unlink(missing_ok=True)


def _mouth_openness_series(video_path: Path, *, max_seconds: float) -> tuple[list[float], float]:
    """Per-sample mouth-openness proxy: darkness of the lower-center face ROI.

    An open mouth exposes a darker interior (and teeth edges), so mouth ROI
    darkness co-varies with speech. Sampled at ~10 fps via frame stride.
    """
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return [], 0.1
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        max_frames = min(total, int(max_seconds * fps)) if total > 0 else int(max_seconds * fps)
        stride = max(1, int(fps * 0.1))
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        series: list[float] = []
        last_roi: tuple[int, int, int, int] | None = None
        index = 0
        while index < max_frames:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, 1.1, 4)
            if len(faces):
                x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                last_roi = (x, y, w, h)
            elif last_roi is None:
                index += stride
                continue
            x, y, w, h = last_roi
            mx1, mx2 = int(x + w * 0.3), int(x + w * 0.7)
            my1, my2 = int(y + h * 0.65), int(y + h * 0.9)
            roi = gray[max(0, my1) : my2, max(0, mx1) : mx2]
            if roi.size:
                series.append(255.0 - float(np.mean(roi)))
            index += stride
        return series, stride / fps if fps > 0 else 0.1
    finally:
        capture.release()


def _unavailable(limitations: list[str], reason: str) -> LipsyncAnalysis:
    return LipsyncAnalysis(False, 0, f"립싱크 분석 불가 — {reason}", None, None, 0, None, limitations + [reason])

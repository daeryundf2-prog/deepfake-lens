"""Audio-visual sync (lip-sync) probe.

Two paths, tried in order:

1. Pretrained SyncNet (joonson/syncnet_python; ``models/syncnet_v2.model``
   + ``sfd_face.pth``) — learned AV offset/confidence. Verified
   2026-09-23: recovers an injected +400 ms shift exactly (-10 frames
   @25fps). Skipped when the package or weights are absent, and falls
   through to the heuristic when S3FD finds no usable face track.
2. Heuristic fallback — correlates the audio RMS envelope with a
   mouth-region openness proxy across candidate lag offsets. Coarse, but
   measured on real Commons clips (aligned r~0.19 score 0 vs +400 ms
   shift flagged score 25).

Both halves degrade to ``available=False`` rather than a fabricated
score when ffmpeg, cv2, a face, or an audio track is missing.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .native_path import CascadeLoadError, native_safe_path, scratch_dir
from .layer_diagnostic import REFERENCE_BAND, UNAVAILABLE_BAND
from .vendor_weights import default_models_dir
from .native_stderr import FFMPEG_QUIET_ARGS, quiet_native_stderr

# Correlation below this with clear speech activity = mismatch candidate.
_WEAK_CORRELATION = 0.12
# Offsets beyond ~0.5 s are implausible for a genuine in-camera recording.
_LARGE_OFFSET_SECONDS = 0.5
_FFMPEG_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class LipsyncAnalysis:
    """Coarse audio-visual sync measurement."""

    available: bool
    score: int  # 0-100 raw heuristic points for sync mismatch (unmeasured)
    # D1: a descriptive measurement note (correlation/offset/confidence), not
    # a conclusion; reference_band follows layer_diagnostic (reference when
    # measured, unavailable when the layer could not run).
    reference_note: str
    best_correlation: float | None
    best_lag_seconds: float | None
    mouth_samples: int
    speech_activity: float | None  # envelope std — gate for the verdict
    limitations: list[str]
    # SyncNet-path fields (None on the heuristic path). Kept separate so
    # heuristic fields never carry pretrained-model semantics.
    syncnet_confidence: float | None = None
    syncnet_min_dist: float | None = None
    method: str = "heuristic"
    reference_band: str = REFERENCE_BAND

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def analyze_lipsync(path: Path | str, *, max_seconds: float = 20.0) -> LipsyncAnalysis:
    """Measure audio-visual sync for a video file.

    Prefers the pretrained SyncNet model (joonson/syncnet_python weights
    in ``models/``) when available; falls back to the zero-asset
    envelope/mouth-motion correlation heuristic otherwise.
    """
    syncnet = _syncnet_analysis(Path(path))
    if syncnet is not None:
        return syncnet
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
        return LipsyncAnalysis(
            True, 0, f"음성 활동량 {speech_activity:.5f}, 입 움직임 변화량 {float(mou.std()):.5f} — 변화가 거의 없어 동기를 측정하지 못했습니다.",
            None, None, len(mouth), speech_activity, limitations,
        )

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
    elif abs(best_lag) > _LARGE_OFFSET_SECONDS:
        score = 25
    else:
        score = 0
    note = (
        f"음성-입 움직임 상관 r={best_corr:.2f}, 최적 지연 {best_lag:+.2f}초 "
        f"(가산 기준: r<{_WEAK_CORRELATION} 또는 |지연|>{_LARGE_OFFSET_SECONDS}초, 미측정 휴리스틱)."
    )
    return LipsyncAnalysis(True, score, note, round(best_corr, 4), round(best_lag, 3), len(mouth), round(speech_activity, 5), limitations)


def _audio_envelope(video_path: Path, *, max_seconds: float) -> tuple[list[float], float]:
    """Extract mono 8 kHz audio via ffmpeg and return (RMS envelope, dt).

    Uses the stdlib ``wave`` reader on PCM output so librosa is not required.
    Envelope window is 40 ms — fine enough to track syllable-rate motion.
    """
    import numpy as np
    import wave

    window_seconds = 0.04
    rate = 8000
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=scratch_dir()) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with native_safe_path(video_path) as native_video:  # R12-1
            subprocess.run(
                [
                    "ffmpeg", *FFMPEG_QUIET_ARGS, "-y", "-i", native_video,
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


@quiet_native_stderr  # G14: decoder chatter (fd 2) goes to the log, not the console
def _mouth_openness_series(video_path: Path, *, max_seconds: float) -> tuple[list[float], float]:
    """Per-sample mouth-openness proxy: darkness of the lower-center face ROI.

    An open mouth exposes a darker interior (and teeth edges), so mouth ROI
    darkness co-varies with speech. Sampled at ~10 fps via frame stride.
    """
    import cv2
    import numpy as np

    with native_safe_path(video_path) as native_video:  # R12-1: never a non-ASCII name to cv2
        capture = cv2.VideoCapture(native_video)
        if not capture.isOpened():
            return [], 0.1
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 25.0)
            total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            max_frames = min(total, int(max_seconds * fps)) if total > 0 else int(max_seconds * fps)
            stride = max(1, int(fps * 0.1))
            cascade = _face_cascade()  # R14-5: native_safe_path; a cascade that does not load raises
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
    return LipsyncAnalysis(False, 0, f"립싱크 분석 불가 — {reason}", None, None, 0, None, limitations + [reason], reference_band=UNAVAILABLE_BAND)


_SYNCNET_PIPELINE = None
_SYNCNET_FAILED = False


def _syncnet_analysis(video_path: Path) -> LipsyncAnalysis | None:
    """Pretrained SyncNet offset/confidence, or None when unavailable.

    Requires the optional ``syncnet-python`` package plus both weight
    files (``models/syncnet_v2.model`` ~2.6 MB, ``models/sfd_face.pth``
    ~90 MB). Returns None on any failure so the heuristic path runs.
    """
    global _SYNCNET_PIPELINE, _SYNCNET_FAILED
    if _SYNCNET_FAILED:
        return None
    try:
        if _SYNCNET_PIPELINE is None:
            from syncnet_python.syncnet_pipeline import SyncNetPipeline

            models_dir = default_models_dir()
            s3fd = models_dir / "sfd_face.pth"
            syncnet = models_dir / "syncnet_v2.model"
            if not (s3fd.is_file() and syncnet.is_file()):
                _SYNCNET_FAILED = True
                return None
            _SYNCNET_PIPELINE = SyncNetPipeline(
                {
                    "s3fd_weights": str(s3fd),
                    "syncnet_weights": str(syncnet),
                },
                device="cpu",
            )
        import contextlib
        import sys

        # syncnet-python prints ffmpeg progress and framewise confidence to
        # stdout — redirect to stderr so JSON consumers get a clean channel.
        with contextlib.redirect_stdout(sys.stderr), native_safe_path(video_path) as native_video:  # R12-1
            offsets, confs, dists, max_conf, min_dist, _json, has_face = (
                _SYNCNET_PIPELINE.inference(native_video)
            )
    except Exception:
        # Latch only when the pipeline never constructed — a per-file
        # inference failure (corrupt video, no decodable stream) must not
        # permanently disable SyncNet for the rest of the process.
        if _SYNCNET_PIPELINE is None:
            _SYNCNET_FAILED = True
        return None

    limitations = [
        "SyncNet 사전학습 모델(LRS2) 기반 오프셋/신뢰도 측정 — 스크리닝 신호이며 포렌식 감정이 아닙니다.",
        "얼굴 트랙이 짧거나 화질이 낮으면 오프셋 추정이 불안정합니다.",
    ]
    if not has_face or not offsets:
        # S3FD found no usable track — fall through to the heuristic path,
        # whose mouth-region proxy can still measure faces S3FD misses.
        return None
    # All face tracks get checked — report the worst offset across tracks
    # rather than only the first detected face.
    offset_frames = max((float(o) for o in offsets), key=abs)
    lag_seconds = abs(offset_frames) / 25.0
    confidence = float(max_conf)
    # SyncNet convention: |offset| <= 3 frames and confidence >= 3 means
    # in-sync; large offset or low confidence is the dubbing/forgery side.
    if lag_seconds > 0.5 or confidence < 1.0:
        score = 70
    elif lag_seconds > 0.2 or confidence < 3.0:
        score = 40
    else:
        score = 0
    note = f"SyncNet 오프셋 {offset_frames:+.0f}프레임({lag_seconds:.2f}초), 신뢰도 {confidence:.1f}."
    return LipsyncAnalysis(
        True, score, note, None, round(lag_seconds, 3), len(offsets), None,
        limitations, syncnet_confidence=round(confidence, 4),
        syncnet_min_dist=round(min_dist, 4) if min_dist else None,
        method="syncnet",
    )


def _face_cascade() -> Any:
    """R14-5: the Haar face cascade (face.load_face_cascade); none at all is a load failure here."""
    from .face import load_face_cascade

    cascade = load_face_cascade()
    if cascade is None:
        raise CascadeLoadError("Haar 얼굴 검출기 파일이 없습니다")
    return cascade

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
from .native_stderr import FFMPEG_QUIET_ARGS, FfmpegError, ffmpeg_error, ffmpeg_found_no_stream, quiet_native_stderr
from .checkpoint_integrity import force_weights_only
from .model_assets import S3FD_WEIGHTS, SYNCNET_WEIGHTS, AssetPinError, expected_sha256, manifest_state, verified_copy
from .shutdown import run_child

# Correlation below this with clear speech activity = mismatch candidate.
_WEAK_CORRELATION = 0.12
# Offsets beyond ~0.5 s are implausible for a genuine in-camera recording.
_LARGE_OFFSET_SECONDS = 0.5
_FFMPEG_TIMEOUT_SECONDS = 60
# R17-4 (round 17): an empty envelope means only "no audio track" now — an
# ffmpeg that cannot run or fails raises FfmpegError (a failed check).
LIPSYNC_NO_AUDIO_NOTE = "오디오 트랙이 없습니다."


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
    if not video_path.is_file():
        # R17-4: checked here — ffmpeg's "No such file" is no longer read as "no audio track".
        return _unavailable(limitations, "영상 파일이 없습니다.")

    envelope, env_dt = _audio_envelope(video_path, max_seconds=max_seconds)
    if not envelope:
        return _unavailable(limitations, LIPSYNC_NO_AUDIO_NOTE)
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
            proc = run_child(  # R15-1: a tracked child, stopped by the shutdown cleanup
                [
                    "ffmpeg", *FFMPEG_QUIET_ARGS, "-y", "-i", native_video,
                    "-t", f"{max_seconds:.1f}",
                    "-vn", "-ac", "1", "-ar", str(rate), "-f", "wav", str(tmp_path),
                ],
                capture_output=True,
                timeout=_FFMPEG_TIMEOUT_SECONDS,
            )
        if proc.returncode != 0:
            if ffmpeg_found_no_stream(proc.stderr):
                return [], window_seconds  # no audio track
            # R17-4 (round 17): not "no audio track" — ffmpeg could not run or failed.
            raise ffmpeg_error(proc.returncode, proc.stderr)
        with wave.open(str(tmp_path), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
            width = handle.getsampwidth()
        samples = np.frombuffer(frames, dtype=np.int16 if width == 2 else np.uint8).astype(float)
        if width == 1:
            samples -= 128.0
        hop = max(1, int(rate * window_seconds))
        envelope = [float(np.sqrt(np.mean(samples[i : i + hop] ** 2))) for i in range(0, len(samples) - hop, hop)]
        return envelope, window_seconds
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(f"ffmpeg가 {_FFMPEG_TIMEOUT_SECONDS}초 안에 끝나지 않음") from exc
    except wave.Error as exc:
        # R17-4: ffmpeg ended 0 but its WAV cannot be read — a failure, not "no track".
        raise FfmpegError(f"ffmpeg 출력 WAV를 읽지 못함: {exc}") from exc
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
# R16-3 (round 16): what the cached pipeline was built from — the manifest
# state, both pins and both weight files' (path, size, mtime, inode). The
# pipeline used to be built once per process and reused after a re-pin or a
# removed pin; it is now rebuilt (re-verified) whenever this key changes.
_SYNCNET_KEY: tuple[object, ...] | None = None
# syncnet-python is not importable (latched: the package does not appear mid-run).
_SYNCNET_FAILED = False
# R16-3: the key whose pipeline construction raised (not retried for the same files and pins).
_SYNCNET_BROKEN_KEY: tuple[object, ...] | None = None


def _syncnet_pipeline(models_dir: Path, expected: tuple[str, str] | None = None) -> Any:
    """R15-3: the SyncNet pipeline built from the *verified* weights, or None when they are not provisioned.

    Both weight files must be registered in the asset manifest with a
    sha256 their bytes match (``model_assets``): an unpinned or different
    file raises :class:`AssetPinError` "미고정 모델: …" — the lip-sync
    check is ``failed`` — instead of being handed to the third-party
    ``torch.load``. The pipeline gets private copies of the verified bytes
    and loads them with ``weights_only=True`` forced; the copies are removed
    once it has loaded. ``expected`` (R16-3): the (s3fd, syncnet) pins the
    caller keyed its cache on.
    """
    from syncnet_python.syncnet_pipeline import SyncNetPipeline

    s3fd = models_dir / S3FD_WEIGHTS
    syncnet = models_dir / SYNCNET_WEIGHTS
    if not (s3fd.is_file() and syncnet.is_file()):
        return None
    copies: list[str] = []
    try:
        copies.append(verified_copy(S3FD_WEIGHTS, s3fd, expected=expected[0] if expected else None))
        copies.append(verified_copy(SYNCNET_WEIGHTS, syncnet, expected=expected[1] if expected else None))
        with force_weights_only():
            return SyncNetPipeline({"s3fd_weights": copies[0], "syncnet_weights": copies[1]}, device="cpu")
    finally:
        for copy in copies:
            Path(copy).unlink(missing_ok=True)


def _syncnet_key(models_dir: Path) -> tuple[object, ...] | None:
    """R16-3: the cache key of the pipeline for ``models_dir`` — None when the weights are not provisioned.

    Reads the current pins (the manifest is re-read when it changed); a
    weight file that is present but not pinned raises :class:`AssetPinError`.
    """
    files = (models_dir / S3FD_WEIGHTS, models_dir / SYNCNET_WEIGHTS)
    if not all(path.is_file() for path in files):
        return None
    pins = []
    for asset in (S3FD_WEIGHTS, SYNCNET_WEIGHTS):
        expected = expected_sha256(asset)
        if expected is None:
            raise AssetPinError(asset)
        pins.append(expected)
    stats = []
    for path in files:
        info = path.stat()
        stats.append((str(path), info.st_size, info.st_mtime_ns, info.st_ino))
    return (manifest_state(), tuple(pins), tuple(stats))


def _syncnet_analysis(video_path: Path) -> LipsyncAnalysis | None:
    """Pretrained SyncNet offset/confidence, or None when unavailable.

    Requires the optional ``syncnet-python`` package plus both weight
    files (``models/syncnet_v2.model`` ~2.6 MB, ``models/sfd_face.pth``
    ~90 MB). Returns None on any failure so the heuristic path runs —
    except R15-3: weights that are present but not pinned (or not the
    pinned bytes) raise :class:`AssetPinError`. R16-3: the pins and the
    weight files are checked on every call and the pipeline rebuilt from
    freshly verified bytes when either changed.
    """
    global _SYNCNET_PIPELINE, _SYNCNET_KEY, _SYNCNET_FAILED, _SYNCNET_BROKEN_KEY
    if _SYNCNET_FAILED:
        return None
    try:
        import syncnet_python.syncnet_pipeline  # noqa: F401  (availability probe)
    except ImportError:
        _SYNCNET_FAILED = True
        return None
    models_dir = default_models_dir()
    key = _syncnet_key(models_dir)  # R15-3: AssetPinError is never latched — every video records the refusal
    if key is None or key == _SYNCNET_BROKEN_KEY:
        return None
    if _SYNCNET_PIPELINE is None or key != _SYNCNET_KEY:
        _SYNCNET_PIPELINE, _SYNCNET_KEY = None, None
        pins = key[1]
        assert isinstance(pins, tuple)
        try:
            pipeline = _syncnet_pipeline(models_dir, expected=(str(pins[0]), str(pins[1])))
        except AssetPinError:
            raise
        except Exception:
            _SYNCNET_BROKEN_KEY = key
            return None
        if pipeline is None:
            return None
        _SYNCNET_PIPELINE, _SYNCNET_KEY = pipeline, key
    try:
        import contextlib
        import sys

        # syncnet-python prints ffmpeg progress and framewise confidence to
        # stdout — redirect to stderr so JSON consumers get a clean channel.
        with contextlib.redirect_stdout(sys.stderr), native_safe_path(video_path) as native_video:  # R12-1
            offsets, confs, dists, max_conf, min_dist, _json, has_face = (
                _SYNCNET_PIPELINE.inference(native_video)
            )
    except Exception:
        # A per-file inference failure (corrupt video, no decodable stream)
        # must not permanently disable SyncNet for the rest of the process.
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

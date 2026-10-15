"""Meta Video Seal (images, video) and AudioSeal decoders — optional torch extra (techniques-2026 item 5).

- **Video Seal 1.0** (facebookresearch/videoseal, MIT, arXiv 2412.09492): the
  TorchScript export ``y_256b_img.jit`` (``dl.fbaipublicfiles.com``), asset
  ``videoseal-y_256b_img.jit`` pinned by sha256 in ``models/assets.json``.
  ``detect`` returns 1 + 256 logits per frame; the message is the sign of the
  256 bit logits (averaged over frames for a video — the "avg" aggregation of
  ``detect_video_and_aggregate``). Video Seal has no calibrated presence
  output (its first channel is not a detection score), so a decoded message
  is **no information by itself**: any image yields 256 bits. Presence is
  claimed only on agreement with a registered message
  (``models/watermark-registry.json``, rule ``min_agreement_bits``) — the
  registry holds no Video Seal message (no public, verified generator
  message exists, 2026-10), so the check records the decoded message as a
  fact in the marking context and draws no conclusion.
- **AudioSeal 0.2** (facebookresearch/audioseal, MIT): the 16-bit detector
  ``detector_base.pth`` (Hugging Face ``facebook/audioseal`` revision
  ``3c19eba5…``), asset ``audioseal-detector_base.pth``, built with the
  ``audioseal`` package's own model card ``audioseal_detector_16bits``. It
  has a detection head: the share of 16 kHz frames whose watermark
  probability exceeds 0.5. A share >= :data:`AUDIOSEAL_PRESENCE_THRESHOLD`
  is recorded as a **fact** (presence + 16-bit message) with the measured
  false-positive rate; a generator is named only on an exact match with a
  registered 16-bit message (none registered).

Without torch (and, for AudioSeal, the ``audioseal`` package) the checks are
``skipped`` "의존성 부재: torch"; without the weight file, ``skipped``
"가중치 없음: …"; a weight file whose bytes differ from the pin, ``failed``
"미고정 모델: …". Absence of a mark never means "original".

Long inputs are streamed: a video is read frame by frame and at most
:data:`VIDEO_MAX_FRAMES` frames, evenly spread over the first
:data:`VIDEO_MAX_SECONDS` seconds, are decoded; audio is decoded by ffmpeg to
16 kHz mono (first :data:`AUDIO_MAX_SECONDS` seconds) and run in
:data:`AUDIO_CHUNK_SECONDS` chunks. Torch runs with one intra-op thread during
these calls (restored afterwards) so the logits do not depend on the
worker count.
"""

from __future__ import annotations

import io
import threading
import wave
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .check_skipped import CheckSkipped
from .native_stderr import quiet_native_stderr

VIDEOSEAL_ASSET = "videoseal-y_256b_img.jit"
AUDIOSEAL_ASSET = "audioseal-detector_base.pth"
AUDIOSEAL_CARD = "audioseal_detector_16bits"
VIDEOSEAL_BITS = 256
AUDIOSEAL_BITS = 16
# Registry rule for Video Seal (neural, lossy): agreement of at least this many of the 256 bits with a
# registered message. Chance agreement of a random 256-bit string: P(X >= 243) < 1e-50 (binomial, p=1/2);
# an embed + 8-bit round trip measured 254/256 (docs/WATERMARK-MEASUREMENTS.md).
VIDEOSEAL_MATCH_BITS = 243
# audioseal.models.AudioSealDetector.detect_watermark default detection threshold (share of frames).
AUDIOSEAL_PRESENCE_THRESHOLD = 0.5
AUDIOSEAL_SAMPLE_RATE = 16000
AUDIO_MAX_SECONDS = 600
AUDIO_CHUNK_SECONDS = 60
VIDEO_MAX_SECONDS = 600
VIDEO_MAX_FRAMES = 32
# VideoSeal processes at 256 px internally; inputs are reduced to 1024 px first (an embed + 8-bit round trip
# read 254/256 bits at 1280, 1024 and 512 px alike; 0.26 s → 0.22 s per image, one thread).
IMAGE_MAX_SIDE = 1024
MIN_IMAGE_SIDE = 128

_LOCK = threading.Lock()
_MODELS: dict[tuple[str, str], Any] = {}


@dataclass(frozen=True)
class VideoSealReading:
    bits: str
    mean_abs_logit: float
    frames: int


@dataclass(frozen=True)
class AudioSealReading:
    probability: float
    bits: str
    seconds: float


def _models_dir() -> Path:
    from .vendor_weights import default_models_dir

    return default_models_dir()


def _weight(asset: str) -> Path:
    path = _models_dir() / asset
    if not path.is_file():
        raise CheckSkipped(f"가중치 없음: {asset} — scripts/fetch_watermark_models.py로 내려받아 models 폴더에 두면 실행됩니다")
    return path


@contextmanager
def _one_thread() -> Iterator[None]:
    import torch

    with _LOCK:
        previous = torch.get_num_threads()
        torch.set_num_threads(1)
        try:
            with torch.inference_mode():
                yield
        finally:
            torch.set_num_threads(previous)


def _videoseal() -> Any:
    import torch

    from .model_assets import expected_sha256, verified_bytes

    path = _weight(VIDEOSEAL_ASSET)
    expected = expected_sha256(VIDEOSEAL_ASSET)
    key = (VIDEOSEAL_ASSET, expected or "")
    with _LOCK:
        cached = _MODELS.get(key)
    if cached is not None:
        return cached
    data = verified_bytes(VIDEOSEAL_ASSET, path, expected=expected)  # AssetPinError -> failed
    import warnings

    with warnings.catch_warnings():  # torch.jit deprecation notices are not the examiner's concern
        warnings.simplefilter("ignore")
        model = torch.jit.load(io.BytesIO(data), map_location="cpu")
    model.eval()
    with _LOCK:
        _MODELS[key] = model
    return model


def _audioseal() -> Any:
    import audioseal  # noqa: F401 — dependency probe (the model code)
    import torch  # noqa: F401
    from audioseal.builder import AudioSealDetectorConfig, create_detector
    from audioseal.loader import (
        AudioSeal,
        _safe_load_checkpoint,
        convert_state_dict_for_scriptable_model,
        load_local_model_config,
        load_state_dict,
    )
    from omegaconf import OmegaConf

    from .model_assets import expected_sha256, verified_copy

    path = _weight(AUDIOSEAL_ASSET)
    expected = expected_sha256(AUDIOSEAL_ASSET)
    key = (AUDIOSEAL_ASSET, expected or "")
    with _LOCK:
        cached = _MODELS.get(key)
    if cached is not None:
        return cached
    copy = verified_copy(AUDIOSEAL_ASSET, path, expected=expected)  # the verified bytes, never the original path
    import warnings

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            checkpoint = _safe_load_checkpoint(copy, device="cpu")
    finally:
        Path(copy).unlink(missing_ok=True)
    card = load_local_model_config(AUDIOSEAL_CARD)
    if card is None:
        raise RuntimeError(f"audioseal 모델 카드 없음: {AUDIOSEAL_CARD}")
    config = OmegaConf.to_container(card)
    assert isinstance(config, dict)
    config.pop("checkpoint", None)
    if "xp.cfg" in checkpoint:
        config = {**checkpoint["xp.cfg"], **config}
    model_config = AudioSeal.parse_config(config, AudioSealDetectorConfig, nbits=AUDIOSEAL_BITS)
    if "best_state" in checkpoint:
        checkpoint = checkpoint["best_state"]["model"]
    elif "model" in checkpoint:
        checkpoint = checkpoint["model"]
    model = create_detector(model_config)
    load_state_dict(model, convert_state_dict_for_scriptable_model(checkpoint))
    model.eval()
    with _LOCK:
        _MODELS[key] = model
    return model


def _image_tensor(rgb: Any) -> Any:
    import numpy as np
    import torch

    return torch.from_numpy(np.ascontiguousarray(rgb, dtype=np.float32) / 255.0).permute(2, 0, 1)[None]


def _bits(logits: Any) -> str:
    return "".join("1" if float(value) > 0 else "0" for value in logits)


def videoseal_read_image(path: Path) -> VideoSealReading:
    import numpy  # noqa: F401 — dependency probes
    import torch  # noqa: F401
    from PIL import Image

    model = _videoseal()
    with Image.open(path) as opened:
        opened.load()
        image = opened.convert("RGB")
    if min(image.size) < MIN_IMAGE_SIDE:
        raise CheckSkipped(f"측정 범위 밖: 해상도 {image.size[0]}x{image.size[1]}(짧은 변 {MIN_IMAGE_SIDE}px 미만)")
    if max(image.size) > IMAGE_MAX_SIDE:
        scale = IMAGE_MAX_SIDE / max(image.size)
        image = image.resize((max(1, round(image.size[0] * scale)), max(1, round(image.size[1] * scale))), Image.Resampling.BILINEAR)
    import numpy as np

    with _one_thread():
        preds = model.detect(_image_tensor(np.asarray(image)))
    logits = preds[0, 1:1 + VIDEOSEAL_BITS]
    return VideoSealReading(_bits(logits), float(logits.abs().mean()), 1)


@quiet_native_stderr  # FFmpeg decoder chatter ("moov atom not found") goes to the log, not the console
def videoseal_read_video(path: Path) -> VideoSealReading:
    """Stream the frames (cv2), decode up to VIDEO_MAX_FRAMES evenly spread over the first VIDEO_MAX_SECONDS."""
    import cv2
    import torch

    from .native_path import native_safe_path

    model = _videoseal()
    # R24-2: an in-process FFmpeg reader gets a name it opens from any working folder (for_child).
    with native_safe_path(path, for_child=True) as native_video:
        capture = cv2.VideoCapture(native_video)
        try:
            if not capture.isOpened():
                raise RuntimeError("영상을 열 수 없습니다")
            fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
            count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            limit = int(min(count, fps * VIDEO_MAX_SECONDS)) if fps > 0 and count > 0 else int(VIDEO_MAX_FRAMES)
            step = max(1, limit // VIDEO_MAX_FRAMES) if limit > 0 else 1
            total = None
            used = 0
            index = 0
            while used < VIDEO_MAX_FRAMES and (limit <= 0 or index < max(limit, 1)):
                ok, frame = capture.read()
                if not ok:
                    break
                if index % step == 0:
                    if min(frame.shape[:2]) < MIN_IMAGE_SIDE:
                        raise CheckSkipped(f"측정 범위 밖: 해상도 {frame.shape[1]}x{frame.shape[0]}(짧은 변 {MIN_IMAGE_SIDE}px 미만)")
                    rgb = frame[:, :, ::-1]
                    if max(rgb.shape[:2]) > IMAGE_MAX_SIDE:
                        scale = IMAGE_MAX_SIDE / max(rgb.shape[:2])
                        rgb = cv2.resize(rgb, (round(rgb.shape[1] * scale), round(rgb.shape[0] * scale)), interpolation=cv2.INTER_AREA)
                    with _one_thread():
                        logits = model.detect(_image_tensor(rgb))[0, 1:1 + VIDEOSEAL_BITS]
                    total = logits.clone() if total is None else total + logits
                    used += 1
                index += 1
        finally:
            capture.release()
    if total is None or used == 0:
        raise RuntimeError("디코딩된 영상 프레임이 없습니다")
    mean = total / used
    return VideoSealReading(_bits(mean), float(torch.as_tensor(mean).abs().mean()), used)


def _pcm16_mono(path: Path) -> tuple[Any, float]:
    import numpy as np

    with wave.open(str(path), "rb") as handle:
        frames = handle.readframes(handle.getnframes())
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    return samples, len(samples) / AUDIOSEAL_SAMPLE_RATE


def audioseal_read(path: Path) -> AudioSealReading:
    """Decode the first AUDIO_MAX_SECONDS to 16 kHz mono with ffmpeg and run the detector in chunks."""
    import numpy as np
    import torch

    from .audio import AudioDecoderMissing, NoAudioStream, ffmpeg_audio_wav

    model = _audioseal()
    try:
        with ffmpeg_audio_wav(path, seconds=AUDIO_MAX_SECONDS, mono_rate=AUDIOSEAL_SAMPLE_RATE) as wav:
            samples, seconds = _pcm16_mono(wav)
    except AudioDecoderMissing as exc:
        raise CheckSkipped(str(exc)) from exc
    except NoAudioStream as exc:
        raise CheckSkipped("오디오 트랙 없음") from exc
    if seconds < 1.0:
        raise CheckSkipped(f"측정 범위 밖: 길이 {seconds:.2f}초(1초 미만)")
    chunk = AUDIO_CHUNK_SECONDS * AUDIOSEAL_SAMPLE_RATE
    detected = 0.0
    weights = 0
    message: Any = np.zeros(AUDIOSEAL_BITS, dtype=np.float64)
    for start in range(0, len(samples), chunk):
        piece = samples[start:start + chunk]
        if len(piece) < AUDIOSEAL_SAMPLE_RATE // 2:
            continue
        with _one_thread():
            result, probabilities = model(torch.from_numpy(np.ascontiguousarray(piece))[None, None], sample_rate=AUDIOSEAL_SAMPLE_RATE)
        frames = result.shape[-1]
        detected += float(torch.count_nonzero(torch.gt(result[:, 1, :], 0.5)))
        weights += frames
        message += probabilities[0].double().numpy() * frames
    if weights == 0:
        raise CheckSkipped("측정 범위 밖: 디코딩된 오디오 샘플이 너무 짧음")
    bits = "".join("1" if value / weights > 0.5 else "0" for value in message)
    return AudioSealReading(detected / weights, bits, seconds)


def bits_hex(bits: str) -> str:
    return f"{int(bits, 2):0{(len(bits) + 3) // 4}x}" if bits else ""


__all__ = [
    "AUDIOSEAL_ASSET",
    "AUDIOSEAL_PRESENCE_THRESHOLD",
    "AudioSealReading",
    "VIDEOSEAL_ASSET",
    "VIDEOSEAL_MATCH_BITS",
    "VideoSealReading",
    "audioseal_read",
    "bits_hex",
    "videoseal_read_image",
    "videoseal_read_video",
]

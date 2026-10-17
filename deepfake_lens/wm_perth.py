"""Resemble AI PerTh ("Perceptual Threshold") implicit audio watermark — presence decoder (TB-2).

Threat catalogue O-CHATTERBOX / M-AUD-01. Chatterbox (resemble-ai/chatterbox,
MIT) calls ``perth.PerthImplicitWatermarker().apply_watermark`` on every
generated waveform (``src/chatterbox/tts.py`` L126/L271 and ``vc.py`` L29/L103
at commit 5de7a54aa4e5; README "Built-in PerTh Watermarking"). The watermarker
is the MIT package ``resemble-perth`` 1.0.1 (PyPI, wheel sha256
``65e9c375…``); its implicit model carries **no message** — the decoder
returns one confidence in [0, 1] that the README reads as "0.0 (no watermark)
or 1.0 (watermarked)" after rounding.

This module re-implements only the decoder path of
``PerthImplicitWatermarker.get_watermark`` with torch (no torchaudio — no
torchaudio build exists for the gate's torch 2.14):

1. the first :data:`MAX_SECONDS` of the first audio stream, decoded by ffmpeg
   to 32-bit float, averaged to mono (``librosa.to_mono``), resampled to
   32 kHz with ``librosa.resample(res_type="polyphase")`` (the package's call);
2. the magnitude STFT of torchaudio's ``Spectrogram(n_fft=2048, win_length=2048,
   hop_length=320, window_fn=hann, power=None, normalized=False)`` — that is
   ``torch.stft(center=True, pad_mode="reflect", onesided=True)`` with a periodic
   Hann window — in dB, clipped at 1e-9 and normalised ``(dB + 180) / 195``
   (``perth_net_implicit/utils.py``);
3. the PerthNet decoder (three Conv1d stacks over the lowest 2 kHz, masked means,
   softmax attention — ``model/decoder.py``) with the decoder weights of
   ``perth_net_250000.pth.tar`` (the checkpoint inside the wheel, asset
   ``perth-implicit-250000.pth.tar`` pinned in ``models/assets.json``, loaded
   with ``torch.load(weights_only=True)`` from the verified bytes), clipped to
   [0, 1].

The confidence is a **fact** with its measured false-positive rate; presence
(>= :data:`PRESENCE_THRESHOLD`, the package's rounding) never names a
generator by itself — only the registry entry ``perth-implicit`` does, and
since the implicit mark has one bit it is a moderate synthetic item (never a
conclusion; see media_marks). Absence is no information: re-recording,
speech enhancement or never using Chatterbox leave no mark.
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .check_skipped import CheckSkipped

PERTH_ASSET = "perth-implicit-250000.pth.tar"
SAMPLE_RATE = 32000
N_FFT = 2048
HOP = 320
HIDDEN = 256
MAX_WMARK_FREQ = 2000
MIN_LEVEL_DB = -180.0  # 20 * log10(stft_magnitude_min = 1e-9)
HEADROOM_DB = 15.0
MAX_SECONDS = 300
MIN_SECONDS = 1.0
PRESENCE_THRESHOLD = 0.5  # get_watermark(round=True) rounds the clipped confidence
SUBBAND = int(round((N_FFT // 2 + 1) * MAX_WMARK_FREQ / (SAMPLE_RATE / 2)))

_LOCK = threading.Lock()
_MODELS: dict[str, Any] = {}


@dataclass(frozen=True)
class PerthReading:
    confidence: float
    seconds: float
    source_rate: int

    @property
    def present(self) -> bool:
        return self.confidence >= PRESENCE_THRESHOLD


def _decoder_class() -> Any:
    import torch
    from torch import nn
    import torch.nn.functional as F

    class Conv(nn.Module):
        def __init__(self, i: int, o: int, k: int, act: bool = True) -> None:
            super().__init__()
            self.conv = nn.Conv1d(i, o, k, padding=(k - 1) // 2)
            self.act = nn.LeakyReLU() if act else None

        def forward(self, x: Any) -> Any:
            x = self.conv(x)
            return self.act(x) if self.act is not None else x

    def layers(subband: int, hidden: int) -> Any:
        return nn.Sequential(Conv(subband, hidden, 1), *[Conv(hidden, hidden, 7) for _ in range(5)], Conv(hidden, 2, 1, act=False))

    def masked_mean(x: Any, m: Any) -> Any:
        return (x * m).sum(dim=2) / m.sum(dim=2)

    class Decoder(nn.Module):
        def __init__(self, hidden: int, subband: int) -> None:
            super().__init__()
            self.subband = subband
            self.slow_layers = layers(subband, hidden)
            self.normal_layers = layers(subband, hidden)
            self.fast_layers = layers(subband, hidden)

        def _branch(self, net: Any, subband: Any, mask: Any, size: int | None) -> tuple[Any, Any]:
            x = subband if size is None else F.interpolate(subband, size=size, mode="linear", align_corners=True)
            out = net(x)
            m = F.interpolate(mask, size=out.size(2), mode="nearest")
            return masked_mean(out[:, 1:], m), masked_mean(out[:, :1], m)

        def forward(self, magspec: Any) -> Any:
            s = magspec.detach().sum(dim=1)
            mask = (s > s.max(dim=1).values[:, None] * 0.05).float()[:, None]
            subband = magspec[:, : self.subband]
            t = subband.shape[2]
            slow_w, slow_a = self._branch(self.slow_layers, subband, mask, int(t * 1.25))
            normal_w, normal_a = self._branch(self.normal_layers, subband, mask, None)
            fast_w, fast_a = self._branch(self.fast_layers, subband, mask, int(t * 0.75))
            attn = F.softmax(torch.cat([slow_a, normal_a, fast_a], dim=1), dim=1)
            return (torch.cat([slow_w, normal_w, fast_w], dim=1) * attn).sum(dim=1)

    return Decoder


def _weight() -> Path:
    from .vendor_weights import default_models_dir

    path = default_models_dir() / PERTH_ASSET
    if not path.is_file():
        raise CheckSkipped(f"가중치 없음: {PERTH_ASSET} — scripts/fetch_watermark_models.py --only perth로 내려받아 models 폴더에 두면 실행됩니다")
    return path


def load_decoder() -> Any:
    import torch

    from .model_assets import expected_sha256, verified_bytes

    path = _weight()
    expected = expected_sha256(PERTH_ASSET) or ""
    with _LOCK:
        cached = _MODELS.get(expected)
    if cached is not None:
        return cached
    data = verified_bytes(PERTH_ASSET, path, expected=expected or None)  # AssetPinError -> failed "미고정 모델: …"
    checkpoint = torch.load(io.BytesIO(data), map_location="cpu", weights_only=True)
    state = {key[len("decoder."):]: value for key, value in checkpoint["model"].items() if key.startswith("decoder.")}
    decoder = _decoder_class()(HIDDEN, SUBBAND)
    decoder.load_state_dict(state, strict=True)
    decoder.eval()
    with _LOCK:
        _MODELS[expected] = decoder
    return decoder


def magnitude(signal: Any) -> Any:
    """The normalised dB magnitude spectrogram (torchaudio Spectrogram + perth cx_to_magphase)."""
    import torch

    window = torch.hann_window(N_FFT)
    spec = torch.stft(signal, N_FFT, hop_length=HOP, win_length=N_FFT, window=window, center=True,
                      pad_mode="reflect", normalized=False, onesided=True, return_complex=True)
    mag = 20 * torch.log10(spec.abs().clip(1e-9))
    return (mag - MIN_LEVEL_DB) / (-MIN_LEVEL_DB + HEADROOM_DB)


def confidence(decoder: Any, samples_32k: Any) -> float:
    """``get_watermark(round=False)`` for a mono float waveform at 32 kHz."""
    import torch

    with _LOCK:
        previous = torch.get_num_threads()
        torch.set_num_threads(1)
        try:
            with torch.inference_mode():
                signal = torch.from_numpy(samples_32k.copy()).to(dtype=torch.float)
                value = decoder(magnitude(signal)[None])[0].clip(0.0, 1.0)
        finally:
            torch.set_num_threads(previous)
    return float(value)


# TB-2 measurement domain: the decoder was measured on speech; ffmpeg's 440 Hz sine scored 0.99 (lossless and after
# mp3/aac/opus) while LibriSpeech never exceeded 0.78. A signal whose three strongest spectral peaks (+-2 bins) hold
# this share of the energy is outside that domain and is skipped. Measured 2026-10-17: LibriSpeech dev-clean (240)
# median 0.23, max 0.622; the synthetic voiced fixtures 0.55 / 0.67; pure sines (220/440/1000 Hz, also after mp3) 1.0.
MAX_TONAL_RATIO = 0.9
TONAL_SECONDS = 30


def tonal_ratio(samples: Any, rate: int) -> float:
    """Share of the averaged power spectrum (first TONAL_SECONDS, 4096-point Hann frames) in the 3 strongest peaks (+-2 bins)."""
    import numpy as np

    x = np.asarray(samples[: int(rate * TONAL_SECONDS)], dtype=np.float64)
    n = 4096
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    window = np.hanning(n)
    power = np.zeros(n // 2 + 1)
    frames = (len(x) - n) // (n // 2) + 1
    for index in range(frames):
        power += np.abs(np.fft.rfft(x[index * (n // 2): index * (n // 2) + n] * window)) ** 2
    total = float(power.sum())
    if total <= 0:
        return 0.0
    remaining = power.copy()
    peak_energy = 0.0
    for _ in range(3):
        k = int(np.argmax(remaining))
        lo, hi = max(0, k - 2), min(len(remaining), k + 3)
        peak_energy += float(remaining[lo:hi].sum())
        remaining[lo:hi] = 0
    return peak_energy / total


def perth_read(path: Path) -> PerthReading:
    import librosa  # noqa: F401 — dependency probe: the package's resampler
    import numpy as np

    from .audio import AudioDecoderMissing, NoAudioStream, ffmpeg_audio_wav
    from .float_wav import read_float_wav

    decoder = load_decoder()
    try:
        with ffmpeg_audio_wav(path, seconds=MAX_SECONDS) as wav:
            frames, rate = read_float_wav(wav)
    except AudioDecoderMissing as exc:
        raise CheckSkipped(str(exc)) from exc
    except NoAudioStream as exc:
        raise CheckSkipped("오디오 트랙 없음") from exc
    seconds = len(frames) / rate if rate else 0.0
    if seconds < MIN_SECONDS:
        raise CheckSkipped(f"측정 범위 밖: 길이 {seconds:.2f}초(1초 미만)")
    mono = np.ascontiguousarray(frames.mean(axis=1), dtype=np.float32)
    tonal = tonal_ratio(mono, rate)
    if tonal >= MAX_TONAL_RATIO:
        raise CheckSkipped(
            f"측정 범위 밖: 가장 강한 주파수 3개가 에너지의 {tonal:.0%}인 순음 위주 신호 — PerTh 디코더는 음성에서만 측정되었고 "
            "순음에서 오탐이 관찰됨(440 Hz 사인파 신뢰도 0.99)"
        )
    if rate != SAMPLE_RATE:
        import librosa

        mono = librosa.resample(mono, orig_sr=rate, target_sr=SAMPLE_RATE, res_type="polyphase")
    return PerthReading(confidence(decoder, np.asarray(mono, dtype=np.float32)), seconds, rate)


__all__ = ["PERTH_ASSET", "PRESENCE_THRESHOLD", "PerthReading", "confidence", "load_decoder", "magnitude", "perth_read"]

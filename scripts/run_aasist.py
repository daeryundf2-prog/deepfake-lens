#!/usr/bin/env python3
"""Run the AASIST (Interspeech 2022) pretrained audio anti-spoofing model.

Reconstructs the AASIST architecture from its published code
(models/AASIST.py in github.com/clovaai/aasist — a sinc-conv front-end,
six residual blocks, then spectral/temporal graph attention with
heterogeneous master-node layers), loads the official checkpoint
(models/weights/AASIST.pth in that repo; fetch with
scripts/fetch_aasist.py into models/aasist.pth), decodes an audio file to
a 16 kHz mono waveform, and scores it.

Input handling mirrors upstream: each window is loop-padded / trimmed to
nb_samp=64600 samples (~4.04 s) exactly like data_utils.pad(). Upstream
evaluates a single window per file; here logits are averaged over up to
``max_seconds`` worth of evenly spaced windows so longer files get more
coverage — set --max-seconds 4.05 to reproduce single-window scoring.

Label convention (from clovaai/aasist data_utils.py): bonafide -> 1,
spoof -> 0, so logits are (spoof=0, bonafide=1) and softmax index 0 is
the fake/spoof probability.

Optional dependencies: torch + numpy. Without them every entry point
degrades gracefully — load_model raises ImportError and the CLI prints a
structured {"available": false, ...} payload instead of a traceback.

Usage:
    python scripts/run_aasist.py --checkpoint models/aasist.pth --audio x.wav
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:  # numpy powers WAV decode + windowing; the model also needs torch
    import numpy as np  # noqa: E402

    _NUMPY_ERROR: ImportError | None = None
except ImportError as exc:  # pragma: no cover - exercised without numpy
    np = None  # type: ignore[assignment]
    _NUMPY_ERROR = exc

try:  # optional research stack — the adapter surfaces ImportError as unavailable
    import torch  # noqa: E402
    import torch.nn as nn  # noqa: E402
    import torch.nn.functional as F  # noqa: E402

    _IMPORT_ERROR: ImportError | None = None
except ImportError as exc:  # pragma: no cover - exercised in environments without torch
    torch = nn = F = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc

# model_config from clovaai/aasist config/AASIST.conf (verbatim).
AASIST_CONFIG = {
    "architecture": "AASIST",
    "nb_samp": 64600,
    "first_conv": 128,
    "filts": [70, [1, 32], [32, 32], [32, 64], [64, 64]],
    "gat_dims": [64, 32],
    "pool_ratios": [0.5, 0.7, 0.5, 0.5],
    "temperatures": [2.0, 2.0, 100.0, 100.0],
}

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_MAX_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Model (structure ported from clovaai/aasist models/AASIST.py, MIT license,
# (c) 2021-present NAVER Corp.; weights come from the checkpoint)
# ---------------------------------------------------------------------------

if torch is not None:

    class GraphAttentionLayer(nn.Module):
        def __init__(self, in_dim, out_dim, **kwargs):
            super().__init__()

            # attention map
            self.att_proj = nn.Linear(in_dim, out_dim)
            self.att_weight = self._init_new_params(out_dim, 1)

            # project
            self.proj_with_att = nn.Linear(in_dim, out_dim)
            self.proj_without_att = nn.Linear(in_dim, out_dim)

            # batch norm
            self.bn = nn.BatchNorm1d(out_dim)

            # dropout for inputs
            self.input_drop = nn.Dropout(p=0.2)

            # activate
            self.act = nn.SELU(inplace=True)

            # temperature
            self.temp = 1.0
            if "temperature" in kwargs:
                self.temp = kwargs["temperature"]

        def forward(self, x):
            # apply input dropout
            x = self.input_drop(x)
            # derive attention map
            att_map = self._derive_att_map(x)
            # projection
            x = self._project(x, att_map)
            # apply batch norm
            x = self._apply_BN(x)
            x = self.act(x)
            return x

        def _pairwise_mul_nodes(self, x):
            nb_nodes = x.size(1)
            x = x.unsqueeze(2).expand(-1, -1, nb_nodes, -1)
            x_mirror = x.transpose(1, 2)
            return x * x_mirror

        def _derive_att_map(self, x):
            att_map = self._pairwise_mul_nodes(x)
            att_map = torch.tanh(self.att_proj(att_map))
            att_map = torch.matmul(att_map, self.att_weight)
            att_map = att_map / self.temp
            att_map = F.softmax(att_map, dim=-2)
            return att_map

        def _project(self, x, att_map):
            x1 = self.proj_with_att(torch.matmul(att_map.squeeze(-1), x))
            x2 = self.proj_without_att(x)
            return x1 + x2

        def _apply_BN(self, x):
            org_size = x.size()
            x = x.view(-1, org_size[-1])
            x = self.bn(x)
            x = x.view(org_size)
            return x

        def _init_new_params(self, *size):
            out = nn.Parameter(torch.FloatTensor(*size))
            nn.init.xavier_normal_(out)
            return out

    class HtrgGraphAttentionLayer(nn.Module):
        def __init__(self, in_dim, out_dim, **kwargs):
            super().__init__()

            self.proj_type1 = nn.Linear(in_dim, in_dim)
            self.proj_type2 = nn.Linear(in_dim, in_dim)

            # attention map
            self.att_proj = nn.Linear(in_dim, out_dim)
            self.att_projM = nn.Linear(in_dim, out_dim)

            self.att_weight11 = self._init_new_params(out_dim, 1)
            self.att_weight22 = self._init_new_params(out_dim, 1)
            self.att_weight12 = self._init_new_params(out_dim, 1)
            self.att_weightM = self._init_new_params(out_dim, 1)

            # project
            self.proj_with_att = nn.Linear(in_dim, out_dim)
            self.proj_without_att = nn.Linear(in_dim, out_dim)

            self.proj_with_attM = nn.Linear(in_dim, out_dim)
            self.proj_without_attM = nn.Linear(in_dim, out_dim)

            # batch norm
            self.bn = nn.BatchNorm1d(out_dim)

            # dropout for inputs
            self.input_drop = nn.Dropout(p=0.2)

            # activate
            self.act = nn.SELU(inplace=True)

            # temperature
            self.temp = 1.0
            if "temperature" in kwargs:
                self.temp = kwargs["temperature"]

        def forward(self, x1, x2, master=None):
            num_type1 = x1.size(1)
            num_type2 = x2.size(1)

            x1 = self.proj_type1(x1)
            x2 = self.proj_type2(x2)

            x = torch.cat([x1, x2], dim=1)

            if master is None:
                master = torch.mean(x, dim=1, keepdim=True)

            # apply input dropout
            x = self.input_drop(x)

            # derive attention map
            att_map = self._derive_att_map(x, num_type1, num_type2)

            # directional edge for master node
            master = self._update_master(x, master)

            # projection
            x = self._project(x, att_map)

            # apply batch norm
            x = self._apply_BN(x)
            x = self.act(x)

            x1 = x.narrow(1, 0, num_type1)
            x2 = x.narrow(1, num_type1, num_type2)

            return x1, x2, master

        def _update_master(self, x, master):
            att_map = self._derive_att_map_master(x, master)
            master = self._project_master(x, master, att_map)
            return master

        def _pairwise_mul_nodes(self, x):
            nb_nodes = x.size(1)
            x = x.unsqueeze(2).expand(-1, -1, nb_nodes, -1)
            x_mirror = x.transpose(1, 2)
            return x * x_mirror

        def _derive_att_map_master(self, x, master):
            att_map = x * master
            att_map = torch.tanh(self.att_projM(att_map))
            att_map = torch.matmul(att_map, self.att_weightM)
            att_map = att_map / self.temp
            att_map = F.softmax(att_map, dim=-2)
            return att_map

        def _derive_att_map(self, x, num_type1, num_type2):
            att_map = self._pairwise_mul_nodes(x)
            att_map = torch.tanh(self.att_proj(att_map))

            att_board = torch.zeros_like(att_map[:, :, :, 0]).unsqueeze(-1)
            att_board[:, :num_type1, :num_type1, :] = torch.matmul(att_map[:, :num_type1, :num_type1, :], self.att_weight11)
            att_board[:, num_type1:, num_type1:, :] = torch.matmul(att_map[:, num_type1:, num_type1:, :], self.att_weight22)
            att_board[:, :num_type1, num_type1:, :] = torch.matmul(att_map[:, :num_type1, num_type1:, :], self.att_weight12)
            att_board[:, num_type1:, :num_type1, :] = torch.matmul(att_map[:, num_type1:, :num_type1, :], self.att_weight12)
            att_map = att_board

            att_map = att_map / self.temp
            att_map = F.softmax(att_map, dim=-2)
            return att_map

        def _project(self, x, att_map):
            x1 = self.proj_with_att(torch.matmul(att_map.squeeze(-1), x))
            x2 = self.proj_without_att(x)
            return x1 + x2

        def _project_master(self, x, master, att_map):
            x1 = self.proj_with_attM(torch.matmul(att_map.squeeze(-1).unsqueeze(1), x))
            x2 = self.proj_without_attM(master)
            return x1 + x2

        def _apply_BN(self, x):
            org_size = x.size()
            x = x.view(-1, org_size[-1])
            x = self.bn(x)
            x = x.view(org_size)
            return x

        def _init_new_params(self, *size):
            out = nn.Parameter(torch.FloatTensor(*size))
            nn.init.xavier_normal_(out)
            return out

    class GraphPool(nn.Module):
        def __init__(self, k: float, in_dim: int, p):
            super().__init__()
            self.k = k
            self.sigmoid = nn.Sigmoid()
            self.proj = nn.Linear(in_dim, 1)
            self.drop = nn.Dropout(p=p) if p > 0 else nn.Identity()
            self.in_dim = in_dim

        def forward(self, h):
            Z = self.drop(h)
            weights = self.proj(Z)
            scores = self.sigmoid(weights)
            new_h = self.top_k_graph(scores, h, self.k)
            return new_h

        def top_k_graph(self, scores, h, k):
            _, n_nodes, n_feat = h.size()
            n_nodes = max(int(n_nodes * k), 1)
            _, idx = torch.topk(scores, n_nodes, dim=1)
            idx = idx.expand(-1, -1, n_feat)
            h = h * scores
            h = torch.gather(h, 1, idx)
            return h

    class CONV(nn.Module):
        """Sinc-convolution front-end (RawNet2-style fixed band-pass filters)."""

        @staticmethod
        def to_mel(hz):
            return 2595 * np.log10(1 + hz / 700)

        @staticmethod
        def to_hz(mel):
            return 700 * (10 ** (mel / 2595) - 1)

        def __init__(self, out_channels, kernel_size, sample_rate=16000, in_channels=1, stride=1, padding=0, dilation=1, bias=False, groups=1, mask=False):
            super().__init__()
            if in_channels != 1:
                raise ValueError(f"SincConv only supports one input channel (here, in_channels = {in_channels})")
            self.out_channels = out_channels
            self.kernel_size = kernel_size
            self.sample_rate = sample_rate

            # Forcing the filters to be odd (i.e, perfectly symmetric)
            if kernel_size % 2 == 0:
                self.kernel_size = self.kernel_size + 1
            self.stride = stride
            self.padding = padding
            self.dilation = dilation
            self.mask = mask
            if bias:
                raise ValueError("SincConv does not support bias.")
            if groups > 1:
                raise ValueError("SincConv does not support groups.")

            nfft = 512
            f = int(self.sample_rate / 2) * np.linspace(0, 1, int(nfft / 2) + 1)
            fmel = self.to_mel(f)
            fmelmax = np.max(fmel)
            fmelmin = np.min(fmel)
            filbandwidthsmel = np.linspace(fmelmin, fmelmax, self.out_channels + 1)
            filbandwidthsf = self.to_hz(filbandwidthsmel)

            self.mel = filbandwidthsf
            self.hsupp = torch.arange(-(self.kernel_size - 1) / 2, (self.kernel_size - 1) / 2 + 1)
            self.band_pass = torch.zeros(self.out_channels, self.kernel_size)
            for i in range(len(self.mel) - 1):
                fmin = self.mel[i]
                fmax = self.mel[i + 1]
                hhigh = (2 * fmax / self.sample_rate) * np.sinc(2 * fmax * self.hsupp / self.sample_rate)
                hlow = (2 * fmin / self.sample_rate) * np.sinc(2 * fmin * self.hsupp / self.sample_rate)
                hideal = hhigh - hlow
                self.band_pass[i, :] = torch.tensor(np.hamming(self.kernel_size), dtype=torch.float32) * torch.tensor(np.asarray(hideal), dtype=torch.float32)

        def forward(self, x, mask=False):
            band_pass_filter = self.band_pass.clone().to(x.device)
            # mask=True is upstream's Freq_aug (random band drop); unused at eval
            filters = band_pass_filter.view(self.out_channels, 1, self.kernel_size)
            return F.conv1d(x, filters, stride=self.stride, padding=self.padding, dilation=self.dilation, bias=None, groups=1)

    class Residual_block(nn.Module):
        def __init__(self, nb_filts, first=False):
            super().__init__()
            self.first = first

            if not self.first:
                self.bn1 = nn.BatchNorm2d(num_features=nb_filts[0])
            self.conv1 = nn.Conv2d(in_channels=nb_filts[0], out_channels=nb_filts[1], kernel_size=(2, 3), padding=(1, 1), stride=1)
            self.selu = nn.SELU(inplace=True)

            self.bn2 = nn.BatchNorm2d(num_features=nb_filts[1])
            self.conv2 = nn.Conv2d(in_channels=nb_filts[1], out_channels=nb_filts[1], kernel_size=(2, 3), padding=(0, 1), stride=1)

            if nb_filts[0] != nb_filts[1]:
                self.downsample = True
                self.conv_downsample = nn.Conv2d(in_channels=nb_filts[0], out_channels=nb_filts[1], padding=(0, 1), kernel_size=(1, 3), stride=1)
            else:
                self.downsample = False
            self.mp = nn.MaxPool2d((1, 3))

        def forward(self, x):
            identity = x
            if not self.first:
                out = self.bn1(x)
                out = self.selu(out)
            else:
                out = x
            # Upstream applies conv1 to x (the bn1/selu branch above is
            # dead code kept verbatim for checkpoint compatibility).
            out = self.conv1(x)
            out = self.bn2(out)
            out = self.selu(out)
            out = self.conv2(out)
            if self.downsample:
                identity = self.conv_downsample(identity)
            out += identity
            out = self.mp(out)
            return out

    class AasistModel(nn.Module):
        """AASIST `Model` rebuilt with the released model_config."""

        def __init__(self, d_args):
            super().__init__()

            self.d_args = d_args
            filts = d_args["filts"]
            gat_dims = d_args["gat_dims"]
            pool_ratios = d_args["pool_ratios"]
            temperatures = d_args["temperatures"]

            self.conv_time = CONV(out_channels=filts[0], kernel_size=d_args["first_conv"], in_channels=1)
            self.first_bn = nn.BatchNorm2d(num_features=1)

            self.drop = nn.Dropout(0.5, inplace=True)
            self.drop_way = nn.Dropout(0.2, inplace=True)
            self.selu = nn.SELU(inplace=True)

            self.encoder = nn.Sequential(
                nn.Sequential(Residual_block(nb_filts=filts[1], first=True)),
                nn.Sequential(Residual_block(nb_filts=filts[2])),
                nn.Sequential(Residual_block(nb_filts=filts[3])),
                nn.Sequential(Residual_block(nb_filts=filts[4])),
                nn.Sequential(Residual_block(nb_filts=filts[4])),
                nn.Sequential(Residual_block(nb_filts=filts[4])),
            )

            self.pos_S = nn.Parameter(torch.randn(1, 23, filts[-1][-1]))
            self.master1 = nn.Parameter(torch.randn(1, 1, gat_dims[0]))
            self.master2 = nn.Parameter(torch.randn(1, 1, gat_dims[0]))

            self.GAT_layer_S = GraphAttentionLayer(filts[-1][-1], gat_dims[0], temperature=temperatures[0])
            self.GAT_layer_T = GraphAttentionLayer(filts[-1][-1], gat_dims[0], temperature=temperatures[1])

            self.HtrgGAT_layer_ST11 = HtrgGraphAttentionLayer(gat_dims[0], gat_dims[1], temperature=temperatures[2])
            self.HtrgGAT_layer_ST12 = HtrgGraphAttentionLayer(gat_dims[1], gat_dims[1], temperature=temperatures[2])
            self.HtrgGAT_layer_ST21 = HtrgGraphAttentionLayer(gat_dims[0], gat_dims[1], temperature=temperatures[2])
            self.HtrgGAT_layer_ST22 = HtrgGraphAttentionLayer(gat_dims[1], gat_dims[1], temperature=temperatures[2])

            self.pool_S = GraphPool(pool_ratios[0], gat_dims[0], 0.3)
            self.pool_T = GraphPool(pool_ratios[1], gat_dims[0], 0.3)
            self.pool_hS1 = GraphPool(pool_ratios[2], gat_dims[1], 0.3)
            self.pool_hT1 = GraphPool(pool_ratios[2], gat_dims[1], 0.3)
            self.pool_hS2 = GraphPool(pool_ratios[2], gat_dims[1], 0.3)
            self.pool_hT2 = GraphPool(pool_ratios[2], gat_dims[1], 0.3)

            self.out_layer = nn.Linear(5 * gat_dims[1], 2)

        def forward(self, x, Freq_aug=False):
            x = x.unsqueeze(1)
            x = self.conv_time(x, mask=Freq_aug)
            x = x.unsqueeze(dim=1)
            x = F.max_pool2d(torch.abs(x), (3, 3))
            x = self.first_bn(x)
            x = self.selu(x)

            # get embeddings using encoder: (#bs, #filt, #spec, #seq)
            e = self.encoder(x)

            # spectral GAT (GAT-S)
            e_S, _ = torch.max(torch.abs(e), dim=3)  # max along time
            e_S = e_S.transpose(1, 2) + self.pos_S
            gat_S = self.GAT_layer_S(e_S)
            out_S = self.pool_S(gat_S)  # (#bs, #node, #dim)

            # temporal GAT (GAT-T)
            e_T, _ = torch.max(torch.abs(e), dim=2)  # max along freq
            e_T = e_T.transpose(1, 2)
            gat_T = self.GAT_layer_T(e_T)
            out_T = self.pool_T(gat_T)

            # learnable master nodes
            master1 = self.master1.expand(x.size(0), -1, -1)
            master2 = self.master2.expand(x.size(0), -1, -1)

            # inference 1
            out_T1, out_S1, master1 = self.HtrgGAT_layer_ST11(out_T, out_S, master=self.master1)
            out_S1 = self.pool_hS1(out_S1)
            out_T1 = self.pool_hT1(out_T1)

            out_T_aug, out_S_aug, master_aug = self.HtrgGAT_layer_ST12(out_T1, out_S1, master=master1)
            out_T1 = out_T1 + out_T_aug
            out_S1 = out_S1 + out_S_aug
            master1 = master1 + master_aug

            # inference 2
            out_T2, out_S2, master2 = self.HtrgGAT_layer_ST21(out_T, out_S, master=self.master2)
            out_S2 = self.pool_hS2(out_S2)
            out_T2 = self.pool_hT2(out_T2)

            out_T_aug, out_S_aug, master_aug = self.HtrgGAT_layer_ST22(out_T2, out_S2, master=master2)
            out_T2 = out_T2 + out_T_aug
            out_S2 = out_S2 + out_S_aug
            master2 = master2 + master_aug

            out_T1 = self.drop_way(out_T1)
            out_T2 = self.drop_way(out_T2)
            out_S1 = self.drop_way(out_S1)
            out_S2 = self.drop_way(out_S2)
            master1 = self.drop_way(master1)
            master2 = self.drop_way(master2)

            out_T = torch.max(out_T1, out_T2)
            out_S = torch.max(out_S1, out_S2)
            master = torch.max(master1, master2)

            T_max, _ = torch.max(torch.abs(out_T), dim=1)
            T_avg = torch.mean(out_T, dim=1)
            S_max, _ = torch.max(torch.abs(out_S), dim=1)
            S_avg = torch.mean(out_S, dim=1)

            last_hidden = torch.cat([T_max, T_avg, S_max, S_avg, master.squeeze(1)], dim=1)
            last_hidden = self.drop(last_hidden)
            output = self.out_layer(last_hidden)
            return last_hidden, output


# ---------------------------------------------------------------------------
# Audio decode + windowing (stdlib wave first; optional loaders as fallback)
# ---------------------------------------------------------------------------


def _pcm_bytes_to_float(data: bytes, sampwidth: int) -> "np.ndarray":
    """Decode little-endian PCM payload to float32 in [-1, 1]."""
    if sampwidth == 1:
        return (np.frombuffer(data, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    if sampwidth == 2:
        return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    if sampwidth == 3:
        raw = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = raw[:, 0] | (raw[:, 1] << 8) | (raw[:, 2] << 16)
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        return ints.astype(np.float32) / 8388608.0
    if sampwidth == 4:
        return np.frombuffer(data, dtype="<i4").astype(np.float32) / 2147483648.0
    raise RuntimeError(f"unsupported WAV sample width: {sampwidth} bytes")


def _resample_linear(samples: "np.ndarray", source_rate: int, target_rate: int) -> "np.ndarray":
    """Linear-interpolation resample (approximation of soundfile/librosa)."""
    if source_rate == target_rate or len(samples) == 0:
        return samples
    out_len = int(round(len(samples) * target_rate / source_rate))
    if out_len <= 0:
        return np.zeros(0, dtype=np.float32)
    return np.interp(np.linspace(0.0, len(samples) - 1, out_len), np.arange(len(samples)), samples).astype(np.float32)


def _load_wav(path: Path, *, sample_rate: int, max_seconds: float) -> "np.ndarray":
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sampwidth = handle.getsampwidth()
        source_rate = handle.getframerate()
        nframes = min(handle.getnframes(), int(max_seconds * source_rate))
        data = handle.readframes(nframes)
    samples = _pcm_bytes_to_float(data, sampwidth)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return _resample_linear(samples, source_rate, sample_rate)


def _load_optional(path: Path, *, sample_rate: int, max_seconds: float) -> "np.ndarray":
    """Fallback decoders for non-PCM-WAV containers (flac/mp3/ogg/m4a)."""
    try:
        import soundfile as sf

        samples, source_rate = sf.read(str(path), dtype="float32", always_2d=False)
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        samples = samples[: int(max_seconds * source_rate)]
        return _resample_linear(samples, int(source_rate), sample_rate)
    except ImportError:
        pass
    try:
        import librosa

        samples, _ = librosa.load(str(path), sr=sample_rate, mono=True, duration=max_seconds)
        return np.asarray(samples, dtype=np.float32)
    except ImportError:
        pass
    raise RuntimeError("cannot decode audio: install soundfile or librosa for non-PCM-WAV input, or provide a PCM .wav file")


def load_waveform(path: Path | str, *, sample_rate: int = DEFAULT_SAMPLE_RATE, max_seconds: float = DEFAULT_MAX_SECONDS) -> "np.ndarray":
    """Decode ``path`` to a mono float32 waveform at ``sample_rate``."""
    if np is None:
        raise ImportError(f"numpy is required for audio decode: {_NUMPY_ERROR}")
    audio_path = Path(path)
    if audio_path.suffix.lower() in {".wav", ".wave"}:
        try:
            return _load_wav(audio_path, sample_rate=sample_rate, max_seconds=max_seconds)
        except (wave.Error, EOFError):
            pass  # compressed .wav variants fall through to optional decoders
    return _load_optional(audio_path, sample_rate=sample_rate, max_seconds=max_seconds)


def pad(samples: "np.ndarray", max_len: int) -> "np.ndarray":
    """Upstream data_utils.pad(): trim to max_len or loop-repeat to reach it."""
    if samples.shape[0] >= max_len:
        return samples[:max_len]
    num_repeats = int(max_len / samples.shape[0]) + 1
    return np.tile(samples, num_repeats)[:max_len]


def window_starts(total: int, window: int, max_windows: int) -> list[int]:
    """Evenly spaced window offsets covering ``total`` samples."""
    if total <= window:
        return [0]
    count = max(1, min(max_windows, total // window))
    if count == 1:
        return [0]
    return [int(round(v)) for v in np.linspace(0, total - window, count)]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def load_model(checkpoint_path: Path | str) -> "AasistModel":
    if _IMPORT_ERROR is not None:
        raise ImportError(f"AASIST runtime is optional and not installed: {_IMPORT_ERROR}")
    checkpoint_path = Path(checkpoint_path)
    state = torch.load(str(checkpoint_path), map_location="cpu", weights_only=True)
    if isinstance(state, dict):
        for key in ("state_dict", "model"):
            nested = state.get(key)
            if isinstance(nested, dict) and any(str(k).startswith("encoder.") or str(k).startswith("conv_time.") for k in nested):
                state = nested
                break
    model = AasistModel(AASIST_CONFIG)
    model.load_state_dict(state, strict=True)
    model.eval()
    return model


def score_waveform(model: "AasistModel", samples: "np.ndarray", *, nb_samp: int = 64600, max_windows: int = 7) -> "torch.Tensor":
    """Return mean logits [spoof, bonafide] over up to ``max_windows`` windows."""
    if _IMPORT_ERROR is not None:
        raise ImportError(f"AASIST runtime is optional and not installed: {_IMPORT_ERROR}")
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if samples.shape[0] == 0:
        raise RuntimeError("audio decoded to zero samples")
    logits = []
    with torch.no_grad():
        for start in window_starts(samples.shape[0], nb_samp, max_windows):
            window = pad(samples[start : start + nb_samp], nb_samp)
            batch = torch.from_numpy(np.ascontiguousarray(window, dtype=np.float32)).unsqueeze(0)
            _, out = model(batch)
            logits.append(out)
    return torch.stack(logits).mean(dim=0)


def score_audio_file(model: "AasistModel", path: Path | str, *, sample_rate: int = DEFAULT_SAMPLE_RATE, nb_samp: int = 64600, max_seconds: float = DEFAULT_MAX_SECONDS) -> "torch.Tensor":
    samples = load_waveform(path, sample_rate=sample_rate, max_seconds=max_seconds)
    max_windows = max(1, int(max_seconds * sample_rate / nb_samp))
    return score_waveform(model, samples, nb_samp=nb_samp, max_windows=max_windows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AASIST pretrained audio anti-spoofing model.")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True, help="audio file to classify (PCM .wav needs no extra deps)")
    parser.add_argument("--sample-rate", type=int, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS)
    args = parser.parse_args(argv)

    missing = [str(err) for err in (_IMPORT_ERROR, _NUMPY_ERROR) if err is not None]
    if missing:
        print(json.dumps({"available": False, "detail": f"optional research stack not installed: {'; '.join(missing)}"}))
        return 3
    if not args.checkpoint.is_file():
        print(json.dumps({"available": False, "detail": f"checkpoint was not found: {args.checkpoint} (fetch via scripts/fetch_aasist.py)"}))
        return 2
    try:
        model = load_model(args.checkpoint)
        logits = score_audio_file(model, args.audio, sample_rate=args.sample_rate, max_seconds=args.max_seconds)
    except Exception as exc:  # noqa: BLE001 - surface any decode/inference failure as structured JSON
        print(json.dumps({"available": False, "detail": f"aasist inference failed: {exc}"}))
        return 1

    probs = torch.softmax(logits, dim=1)[0]
    print(json.dumps({"available": True, "spoof": round(float(probs[0]), 4), "bonafide": round(float(probs[1]), 4)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the AIDE (ICLR 2025) pretrained detector end to end.

Reconstructs the AIDE architecture from its published code (models/AIDE.py,
data/dct.py in github.com/shilinyan99/AIDE), loads an official training
checkpoint (Google Drive links in the AIDE README), preprocesses images with
the paper's DCT band-selection pipeline, and produces logits(real=0,
fake=1). Optional --onnx exports a TorchScript graph for the runtime-profile
adapter used by `deepfake-lens --model-path`.

Checkpoint keys used: 'model' (full AIDE_Model state dict, including the
frozen open_clip convnext_xxlarge trunk, so no open_clip download is needed
at load time; the package is only required to instantiate the class).

Usage:
    python scripts/run_aide.py --checkpoint aide-genimage.pth --image x.png
    python scripts/run_aide.py --checkpoint aide-genimage.pth --export-onnx aide.onnx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import torch  # noqa: E402
import torch.nn as nn  # noqa: E402
import torch.nn.functional as F  # noqa: E402
import timm  # noqa: E402

# ---------------------------------------------------------------------------
# DCT band-selection preprocessing (ported from AIDE data/dct.py)
# ---------------------------------------------------------------------------


def _dct_mat(size: int) -> torch.Tensor:
    m = [
        [
            (np.sqrt(1.0 / size) if i == 0 else np.sqrt(2.0 / size)) * np.cos((j + 0.5) * np.pi * i / size)
            for j in range(size)
        ]
        for i in range(size)
    ]
    return torch.tensor(m, dtype=torch.float32)


def _band_mask(size: int, start: float, end: float) -> torch.Tensor:
    return torch.tensor(
        [[0.0 if (i + j > end or i + j < start) else 1.0 for j in range(size)] for i in range(size)],
        dtype=torch.float32,
    )


class DctPreprocessor:
    """AIDE's DCT_base_Rec_Module (window 32, stride 16, single full band).

    An image is unfolded into 32x32 windows; each window is DCT-transformed,
    filtered to the selected band, inverse-transformed, and the windows with
    the smallest / largest high-frequency grades are re-folded into the two
    "min" and two "max" reconstruction views the ResNets consume.
    """

    def __init__(self, window_size: int = 32, stride: int = 16, grade_n: int = 6):
        self.window = window_size
        self.stride = stride
        self.grade_n = grade_n
        self._dct = _dct_mat(window_size)
        self._dct_t = self._dct.transpose(0, 1)
        self._band = _band_mask(window_size, 0, window_size * 2)
        self._grade_masks = nn.ParameterList()
        for i in range(grade_n):
            start = window_size * 2.0 / grade_n * i
            end = window_size * 2.0 / grade_n * (i + 1)
            mask = _band_mask(window_size, start, end)
            count = mask.sum()
            self._grade_masks.append((mask, count))

    def __call__(self, image: torch.Tensor):
        """image: [C, H, W] unnormalized [0, 1] tensor -> 4 views [C, 32, 32] x4."""
        c, h, w = image.shape
        ws, st = self.window, self.stride
        patches = F.unfold(image.unsqueeze(0), kernel_size=ws, stride=st).squeeze(0)  # [C*ws*ws, L]
        _, length = patches.shape
        patches = patches.transpose(0, 1).reshape(length, c, ws, ws)
        dct = self._dct @ patches @ self._dct_t

        grade = torch.zeros(length)
        weight = 1.0
        for mask, count in self._grade_masks:
            energy = torch.log(torch.abs(dct) + 1) * mask / count
            grade += weight * energy.sum(dim=(1, 2, 3))
            weight *= 2
        order = torch.argsort(grade)
        n = (h // ws) * (w // ws)
        n = max(1, min(n, length))

        band = dct * self._band
        restored = self._dct_t @ band @ self._dct  # [L, C, ws, ws]

        def fold(index: int) -> torch.Tensor:
            patch = restored[index].reshape(1, c * ws * ws).transpose(0, 1)
            return F.fold(patch, output_size=(ws, ws), kernel_size=ws, stride=ws).squeeze(0)

        min_idx = order[:n]
        max_idx = torch.flip(order, dims=[0])[:n]
        return fold(int(min_idx[0])), fold(int(max_idx[0])), fold(int(min_idx[1 % n])), fold(int(max_idx[1 % n]))


# ---------------------------------------------------------------------------
# Model (structure from AIDE models/AIDE.py, weights from the checkpoint)
# ---------------------------------------------------------------------------


class Hpf(nn.Module):
    def __init__(self):
        super().__init__()
        # 30 SRM 3x3 filters zero-padded to 5x5 (AIDE models/srm_filter_kernel.py)
        from experiments.aide_srm_kernels import all_normalized_hpf_list

        padded = [np.pad(k, ((1, 1), (1, 1)), mode="constant") if k.shape[0] == 3 else k for k in all_normalized_hpf_list]
        weight = torch.tensor(np.stack(padded), dtype=torch.float32).view(30, 1, 5, 5).repeat(1, 3, 1, 1)
        self.hpf = nn.Conv2d(3, 30, kernel_size=5, padding=2, bias=False)
        self.hpf.weight = nn.Parameter(weight, requires_grad=False)

    def forward(self, x):
        return self.hpf(x)


def _conv3x3(a, b, stride=1):
    return nn.Conv2d(a, b, kernel_size=3, stride=stride, padding=1, bias=False)


def _conv1x1(a, b, stride=1):
    return nn.Conv2d(a, b, kernel_size=1, stride=stride, bias=False)


class Bottleneck(nn.Module):
    expansion = 4

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super().__init__()
        self.conv1 = _conv1x1(inplanes, planes)
        self.bn1 = nn.BatchNorm2d(planes)
        self.conv2 = _conv3x3(planes, planes, stride)
        self.bn2 = nn.BatchNorm2d(planes)
        self.conv3 = _conv1x1(planes, planes * self.expansion)
        self.bn3 = nn.BatchNorm2d(planes * self.expansion)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample

    def forward(self, x):
        identity = x
        out = self.relu(self.bn1(self.conv1(x)))
        out = self.relu(self.bn2(self.conv2(out)))
        out = self.bn3(self.conv3(out))
        if self.downsample is not None:
            identity = self.downsample(x)
        return self.relu(out + identity)


class ResBackbone(nn.Module):
    def __init__(self, layers=(3, 4, 6, 3)):
        super().__init__()
        self.inplanes = 64
        self.conv1 = nn.Conv2d(30, 64, kernel_size=7, stride=2, padding=3, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(64, layers[0])
        self.layer2 = self._make_layer(128, layers[1], stride=2)
        self.layer3 = self._make_layer(256, layers[2], stride=2)
        self.layer4 = self._make_layer(512, layers[3], stride=2)
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))

    def _make_layer(self, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * Bottleneck.expansion:
            downsample = nn.Sequential(_conv1x1(self.inplanes, planes * Bottleneck.expansion, stride), nn.BatchNorm2d(planes * Bottleneck.expansion))
        layers = [Bottleneck(self.inplanes, planes, stride, downsample)]
        self.inplanes = planes * Bottleneck.expansion
        layers += [Bottleneck(self.inplanes, planes) for _ in range(1, blocks)]
        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.maxpool(self.relu(self.bn1(self.conv1(x))))
        x = self.layer4(self.layer3(self.layer2(self.layer1(x))))
        return torch.flatten(self.avgpool(x), 1)  # [b, 2048]


class _FusionMlp(nn.Module):
    """AIDE's Mlp (ViT-style); attribute names fc1/fc2 match the checkpoint."""

    def __init__(self, in_features: int, hidden_features: int, out_features: int):
        super().__init__()
        self.fc1 = nn.Linear(in_features, hidden_features)
        self.act = nn.GELU()
        self.fc2 = nn.Linear(hidden_features, out_features)

    def forward(self, x):
        return self.fc2(self.act(self.fc1(x)))


class AideModel(nn.Module):
    """AIDE_Model rebuilt on timm's ConvNeXt (identical state-dict layout).

    The official AIDE builds its trunk via
    open_clip.create_model_and_transforms('convnext_xxlarge').visual.trunk,
    which IS timm's ConvNeXt — a hand-rolled substitute diverges (verified
    empirically: maxdiff 14 on trunk output). num_classes=0 drops the
    classifier head exactly like AIDE's head surgery.
    """

    def __init__(self):
        super().__init__()
        self.hpf = Hpf()
        self.model_min = ResBackbone()
        self.model_max = ResBackbone()
        # attribute names fc.fc1/fc.fc2 match the official checkpoint
        self.fc = _FusionMlp(2048 + 256, 1024, 2)
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.convnext_proj = nn.Sequential(nn.Linear(3072, 256))
        self.openclip_convnext_xxl = timm.create_model("convnext_xxlarge", pretrained=False, num_classes=0)
        # AIDE's head surgery: keep the pre-pool norm map [b, 3072, 8, 8]
        self.openclip_convnext_xxl.head.global_pool = nn.Identity()
        self.openclip_convnext_xxl.head.flatten = nn.Identity()

    def forward(self, x):
        views = x[:, :4]  # [b, 4, 3, 256, 256] DCT reconstructions
        tokens = x[:, 4]  # [b, 3, 256, 256] normalized RGB

        feats = [self.model_min(self.hpf(views[:, 0])), self.model_max(self.hpf(views[:, 1])),
                 self.model_min(self.hpf(views[:, 2])), self.model_max(self.hpf(views[:, 3]))]
        res_feat = torch.stack(feats, dim=0).mean(dim=0)

        clip_mean = torch.tensor([0.48145466, 0.4578275, 0.40821073]).view(1, 3, 1, 1)
        clip_std = torch.tensor([0.26862954, 0.26130258, 0.27577711]).view(1, 3, 1, 1)
        dinov2_mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
        dinov2_std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
        shifted = tokens * (dinov2_std / clip_std) + (dinov2_mean - clip_mean) / clip_std

        with torch.no_grad():
            trunk_out = self.openclip_convnext_xxl(shifted)  # [b, 3072, 8, 8]
            pooled = self.avgpool(trunk_out).view(x.size(0), -1)
            convnext_feat = self.convnext_proj(pooled)

        return self.fc(torch.cat([convnext_feat, res_feat], dim=1))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def load_model(checkpoint_path: Path) -> AideModel:
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    state = ckpt["model"] if isinstance(ckpt, dict) and "model" in ckpt else ckpt
    model = AideModel()
    missing, unexpected = model.load_state_dict(state, strict=False)
    real_missing = [k for k in missing if not k.startswith("openclip_convnext_xxl") or True]
    if unexpected:
        print(f"warning: unexpected checkpoint keys (ignored): {unexpected[:5]}", file=sys.stderr)
    if [k for k in missing if k not in ("hpf.hpf.weight",)]:
        print(f"warning: missing keys: {missing[:8]}", file=sys.stderr)
    model.eval()
    return model


def preprocess(pil_image, dct: DctPreprocessor) -> torch.Tensor:
    """Replicate AIDE's TestDataset preprocessing exactly.

    ToTensor() -> DCT band-selection -> (Resize 256 + Normalize) on the four
    32x32 views and the token. Resize uses torchvision bilinear to match
    AIDE's transforms.Compose behavior bit-for-bit (PIL BILINEAR differs).
    """
    from torchvision import transforms

    raw = transforms.functional.to_tensor(pil_image.convert("RGB"))
    if raw.shape[-2:] != (256, 256):
        raw = transforms.functional.resize(raw, [256, 256])
    views_raw = [v.squeeze(0) if v.dim() == 4 else v for v in dct(raw)]
    mean, std = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
    views = [transforms.functional.normalize(transforms.functional.resize(v, [256, 256]), mean, std) for v in views_raw]
    token = transforms.functional.normalize(raw, mean, std)
    return torch.stack([*views, token], dim=0)  # [5, 3, 256, 256]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AIDE pretrained detector.")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--image", type=Path, help="image to classify")
    parser.add_argument("--export-onnx", type=Path, help="export the fused model to ONNX")
    args = parser.parse_args(argv)

    if args.image is None and args.export_onnx is None:
        parser.error("provide --image and/or --export-onnx")

    model = load_model(args.checkpoint)
    dct = DctPreprocessor()

    if args.image is not None:
        from PIL import Image

        batch = preprocess(Image.open(args.image), dct).unsqueeze(0)
        with torch.no_grad():
            logits = model(batch)
        probs = torch.softmax(logits, dim=1)[0]
        print(f"real={probs[0].item():.4f} fake={probs[1].item():.4f}")

    if args.export_onnx is not None:
        sample = torch.zeros(1, 5, 3, 256, 256)
        args.export_onnx.parent.mkdir(parents=True, exist_ok=True)
        torch.onnx.export(
            model,
            sample,
            str(args.export_onnx),
            input_names=["input"],
            output_names=["logits"],
            opset_version=17,
            dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
            dynamo=False,
        )
        print(f"exported {args.export_onnx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

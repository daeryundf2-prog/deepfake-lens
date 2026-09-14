#!/usr/bin/env python3
"""Export a torchvision-runtime detector to the Android ONNX contract.

Rebuilds the model exactly like deepfake_lens.model_adapter's "torchvision"
runtime (arch + num_classes head + state_dict_prefix stripping), wraps the
output to the [1,2] logit layout OnnxClassifier.kt expects — for the
single-logit sigmoid detectors (CNNDetection et al.) the wrapper emits
[-z/2, z/2], whose softmax index 1 equals sigmoid(z) exactly — and exports
"input" [1,3,224,224] -> "logits" [1,2] ONNX, optionally INT8-dynamic-
quantized for the APK size budget.

The resulting file belongs at deepfakeclassifier/src/main/assets/
deepfake-lens.onnx (gitignored — checkpoint licenses do not permit
redistribution; see models/NOTICE.md).

Usage:
    python experiments/export_mobile_onnx.py --profile models/cnndetection-runtime.json \
        --checkpoint models/blur_jpg_prob0.5.pth --out deepfake-lens.onnx --quantize-int8
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def build_model(profile: dict[str, object], checkpoint: Path):
    """Mirror model_adapter._run_torchvision's rebuild: arch + head + weights."""
    import torch
    import torchvision.models as tvm

    arch = str(profile.get("arch") or "resnet50")
    num_classes = int(profile.get("num_classes", 1) or 1)
    model_fn = getattr(tvm, arch, None)
    if model_fn is None:
        raise SystemExit(f"error: torchvision.models has no architecture named '{arch}'")
    model = model_fn(weights=None)
    model.fc = torch.nn.Linear(model.fc.in_features, num_classes)
    state = torch.load(str(checkpoint), map_location="cpu")
    if isinstance(state, dict):
        for wrapper in ("state_dict", "model", "net"):
            nested = state.get(wrapper)
            if isinstance(nested, dict) and any(hasattr(v, "ndim") for v in nested.values()):
                state = nested
                break
    prefix = str(profile.get("state_dict_prefix") or "")
    if prefix:
        state = {name[len(prefix):] if str(name).startswith(prefix) else name: value for name, value in state.items()}
    model.load_state_dict(state, strict=True)
    model.eval()
    return model, num_classes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a torchvision-runtime checkpoint to the Android ONNX contract.")
    parser.add_argument("--profile", type=Path, required=True, help="torchvision-runtime profile JSON (arch, num_classes, state_dict_prefix, input_size)")
    parser.add_argument("--checkpoint", type=Path, help="override the profile's checkpoint path")
    parser.add_argument("--out", type=Path, required=True, help="output .onnx path (e.g. deepfakeclassifier/src/main/assets/deepfake-lens.onnx)")
    parser.add_argument("--quantize-int8", action="store_true", help="apply onnxruntime dynamic INT8 quantization (~4x smaller)")
    args = parser.parse_args(argv)

    try:
        import numpy as np
        import torch
    except ImportError as exc:
        print(f"error: optional export dependency missing: {exc}", file=sys.stderr)
        return 2
    try:
        import onnxruntime  # noqa: F401 - used for parity verification
    except ImportError:
        print("error: onnxruntime is required for post-export parity verification", file=sys.stderr)
        return 2

    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    if str(profile.get("runtime") or "") != "torchvision":
        print(f"error: profile runtime must be 'torchvision', got {profile.get('runtime')!r}", file=sys.stderr)
        return 2
    checkpoint = args.checkpoint or args.profile.parent / str(profile.get("checkpoint") or "")
    if not checkpoint.is_file():
        print(f"error: checkpoint not found: {checkpoint}", file=sys.stderr)
        return 2

    input_size = int(profile.get("input_size", 224) or 224)
    model, num_classes = build_model(profile, checkpoint)

    if num_classes == 1:
        # Single-logit sigmoid detectors: emit [-z/2, z/2] so softmax[1]
        # equals sigmoid(z) exactly, matching OnnxClassifier's [1,2] layout.
        wrapped = _TwoLogit(model)
        detail = "wrapped 1-logit sigmoid as [-z/2, z/2] two-logit output"
    elif num_classes == 2:
        wrapped = model
        detail = "native two-class output"
    else:
        print(f"error: num_classes={num_classes} does not fit the Android [1,2] contract", file=sys.stderr)
        return 2

    sample = torch.zeros(1, 3, input_size, input_size)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        torch.onnx.export(
            wrapped, sample, str(args.out),
            input_names=["input"], output_names=["logits"], opset_version=17,
            dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
            dynamo=False,
        )
    except TypeError:
        torch.onnx.export(
            wrapped, sample, str(args.out),
            input_names=["input"], output_names=["logits"], opset_version=17,
            dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
        )
    print(f"exported {args.out} ({args.out.stat().st_size / 1024**2:.1f} MiB, {detail})")

    # Parity: the ONNX must reproduce the torch logits on a fixed input.
    import onnxruntime as ort

    rng = np.random.default_rng(0)
    probe = rng.standard_normal((1, 3, input_size, input_size)).astype(np.float32)
    with torch.no_grad():
        expected = wrapped(torch.from_numpy(probe)).detach().cpu().numpy()
    actual = ort.InferenceSession(str(args.out), providers=["CPUExecutionProvider"]).run(None, {"input": probe})[0]
    diff = float(np.max(np.abs(expected - actual)))
    if expected.shape != actual.shape or diff > 1e-3:
        print(f"error: ONNX diverges from torch (shape {actual.shape} vs {expected.shape}, max abs diff {diff:.4g})", file=sys.stderr)
        return 1
    print(f"verified: ONNX matches torch (max abs diff {diff:.3g})")

    if args.quantize_int8:
        from onnxruntime.quantization import QuantType, quantize_dynamic

        quantized = args.out.with_name(args.out.stem + ".int8.onnx")
        quantize_dynamic(str(args.out), str(quantized), weight_type=QuantType.QInt8)
        print(f"quantized {quantized} ({quantized.stat().st_size / 1024**2:.1f} MiB)")
        q_actual = ort.InferenceSession(str(quantized), providers=["CPUExecutionProvider"]).run(None, {"input": probe})[0]
        q_diff = float(np.max(np.abs(expected - q_actual)))
        print(f"int8 parity vs torch: max abs diff {q_diff:.4g} (logit units; expect < ~0.5)")
    return 0


def _TwoLogit(model):
    import torch

    class TwoLogit(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner

        def forward(self, x):
            z = self.inner(x).reshape(-1, 1)
            return torch.cat([-z / 2.0, z / 2.0], dim=1)

    wrapped = TwoLogit(model)
    wrapped.eval()
    return wrapped


if __name__ == "__main__":
    raise SystemExit(main())

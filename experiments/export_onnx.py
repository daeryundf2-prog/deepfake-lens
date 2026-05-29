from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Optional Deepfake Lens Torch checkpoint to ONNX exporter.")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--image-size", type=int, default=224)
    parser.add_argument("--input-name", default="input")
    parser.add_argument("--output-name", default="logits")
    args = parser.parse_args(argv)

    try:
        import torch
    except ImportError as exc:
        print(f"error: optional export dependency missing: {exc}", file=sys.stderr)
        print("Install torch in an experiment environment.", file=sys.stderr)
        return 2
    if not args.checkpoint.exists():
        print(f"error: checkpoint not found: {args.checkpoint}", file=sys.stderr)
        return 2

    try:
        model = torch.jit.load(str(args.checkpoint), map_location="cpu")
    except Exception as exc:  # noqa: BLE001 - torch raises version-specific exceptions.
        print(f"error: checkpoint must be a TorchScript module for this exporter: {exc}", file=sys.stderr)
        return 2
    model.eval()
    dummy = torch.zeros(1, 3, args.image_size, args.image_size)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        dummy,
        str(args.out),
        input_names=[args.input_name],
        output_names=[args.output_name],
        opset_version=17,
        dynamic_axes={args.input_name: {0: "batch"}, args.output_name: {0: "batch"}},
    )
    print(f"exported {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

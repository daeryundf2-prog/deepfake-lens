from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Optional Deepfake Lens Torch checkpoint to ONNX exporter.")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--image-size",
        type=int,
        default=None,
        help="model input size (default: taken from training-metadata.json beside the checkpoint)",
    )
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

    image_size = _resolve_image_size(parser, args.checkpoint, args.image_size)

    try:
        model = torch.jit.load(str(args.checkpoint), map_location="cpu")
    except Exception as exc:  # noqa: BLE001 - torch raises version-specific exceptions.
        print(f"error: checkpoint must be a TorchScript module for this exporter: {exc}", file=sys.stderr)
        return 2
    model.eval()
    dummy = torch.zeros(1, 3, image_size, image_size)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    _export_onnx(model, dummy, args.out, args.input_name, args.output_name)
    print(f"exported {args.out}")
    if not _verify_export(args.checkpoint, args.out, image_size, args.input_name, args.output_name):
        return 1
    return 0


def _export_onnx(model, dummy, out_path: Path, input_name: str, output_name: str) -> None:
    """Export a TorchScript checkpoint to ONNX.

    TorchScript modules cannot traverse the modern torch.export-based
    exporter (torch >= 2.9 default), so the legacy TorchScript exporter is
    the correct route here; on older torch builds that lack the ``dynamo``
    kwarg the export is simply called without it.
    """
    import torch

    try:
        torch.onnx.export(
            model,
            dummy,
            str(out_path),
            input_names=[input_name],
            output_names=[output_name],
            opset_version=17,
            dynamic_axes={input_name: {0: "batch"}, output_name: {0: "batch"}},
            dynamo=False,
        )
    except TypeError:
        torch.onnx.export(
            model,
            dummy,
            str(out_path),
            input_names=[input_name],
            output_names=[output_name],
            opset_version=17,
            dynamic_axes={input_name: {0: "batch"}, output_name: {0: "batch"}},
        )


def _resolve_image_size(parser: argparse.ArgumentParser, checkpoint: Path, value: int | None) -> int:
    """Prefer an explicit size, then the trainer's metadata.

    Silently defaulting to 224 could export a model whose input size does
    not match the checkpoint, so an unknown size is a hard error.
    """
    if value is not None:
        return value
    metadata_path = checkpoint.parent / "training-metadata.json"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            size = int(metadata.get("image_size", 0) or 0)
            if size > 0:
                return size
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    parser.error(
        "--image-size is required when no training-metadata.json sits beside the checkpoint "
        "(a silent default could mismatch the checkpoint's real input size)"
    )


def _verify_export(checkpoint: Path, out: Path, image_size: int, input_name: str, output_name: str) -> bool:
    """Compare ONNXRuntime output against the TorchScript model when possible."""
    try:
        import numpy as np
        import onnxruntime
    except ImportError:
        print("note: onnxruntime not installed; skipped post-export verification", file=sys.stderr)
        return True
    try:
        import torch

        model = torch.jit.load(str(checkpoint), map_location="cpu")
        model.eval()
        sample = np.random.default_rng(0).standard_normal((1, 3, image_size, image_size)).astype(np.float32)
        with torch.no_grad():
            expected = model(torch.from_numpy(sample)).detach().cpu().numpy()
        session = onnxruntime.InferenceSession(str(out), providers=["CPUExecutionProvider"])
        actual = session.run(None, {input_name: sample})[0]
    except Exception as exc:  # noqa: BLE001 - report and continue: verification is best-effort
        print(f"warning: post-export verification could not run: {exc}", file=sys.stderr)
        return True

    if expected.shape != actual.shape:
        print(
            f"error: ONNX output shape {actual.shape} does not match TorchScript {expected.shape}",
            file=sys.stderr,
        )
        return False
    diff = float(np.max(np.abs(expected - actual)))
    if diff > 1e-2:
        print(f"error: ONNX output diverges from TorchScript (max abs diff {diff:.4g})", file=sys.stderr)
        return False
    print(f"verified: ONNX matches TorchScript (max abs diff {diff:.3g})")
    return True


if __name__ == "__main__":
    raise SystemExit(main())

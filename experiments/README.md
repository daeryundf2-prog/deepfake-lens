# Deepfake Lens Experiments

These scripts are optional research utilities. They are not required by the
local CLI install.

Expected optional dependencies for real training:

- `torch`
- `torchvision`
- `Pillow`

Recommended flow:

```sh
python -m deepfake_lens dataset data/raw --manifest-out artifacts/manifest.json --audit-out artifacts/audit.json --split-out artifacts/split.json
python experiments/train_detector.py --manifest artifacts/manifest.json --arch convnext_tiny --epochs 10 --out experiments/run-001
python experiments/export_onnx.py --checkpoint experiments/run-001/convnext_tiny.torchscript --out experiments/run-001/convnext_tiny.onnx
python -m deepfake_lens models --candidate aide-iclr-2025 --checkpoint experiments/run-001/convnext_tiny.onnx --profile-out experiments/run-001/onnx-runtime-profile.json
python -m deepfake_lens benchmark data/raw --pixel-modes deep --model-path experiments/run-001/runtime-profile.json --json-out experiments/run-001/benchmark.json
```

`train_detector.py` trains a local binary image detector, writes a TorchScript
checkpoint, state dict, runtime profile, and history metadata. Keep the command
outside the package dependency set so the CLI remains lightweight.

Do not publish checkpoints without dataset provenance, license notes, and a
calibration/benchmark report.

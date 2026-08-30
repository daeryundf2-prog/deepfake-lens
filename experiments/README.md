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

`--sbi` (Self-Blended Images) trains from real-labeled images only: each
record yields an original plus a blended fake synthesized from distortion
pairs (see `experiments/sbi.py`). This avoids needing a fake dataset and
learns blending/resampling artifacts instead of one generator's signature.

`export_onnx.py` takes the input size from `training-metadata.json` beside
the checkpoint and verifies the exported ONNX against TorchScript with
onnxruntime when it is installed.

## Android handoff

The mobile app runs the exported model through ONNX Runtime when the file
is present:

```sh
python experiments/export_onnx.py --checkpoint experiments/run-001/convnext_tiny.torchscript \
  --out deepfakeclassifier/src/main/assets/deepfake-lens.onnx
```

Contract (see `deepfakeclassifier/src/main/assets/README.md` and
`OnnxClassifier.kt`): input `input` [1,3,224,224] NCHW float32,
ImageNet-normalized RGB; output `logits` [1,2], softmax over
`real=0, synthetic-fake=1`. Without the asset the app degrades to
heuristics only. Until a checkpoint is validated on a real benchmark
(cross-dataset AUC/EER report), the neural score is surfaced in the app as
a weight-0 informational signal and never moves the heuristic score.

Do not publish checkpoints without dataset provenance, license notes, and a
calibration/benchmark report.

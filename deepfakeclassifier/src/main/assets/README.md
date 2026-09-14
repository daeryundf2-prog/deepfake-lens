# ONNX model asset directory

Drop the exported detector here as `deepfake-lens.onnx`:

```
python experiments/train_detector.py --manifest artifacts/manifest.json --sbi --out experiments/run-001
python experiments/export_onnx.py --checkpoint experiments/run-001/convnext_tiny.torchscript --out deepfakeclassifier/src/main/assets/deepfake-lens.onnx
```

A published checkpoint can also be adapted directly — `export_mobile_onnx.py`
rebuilds a torchvision-runtime profile (e.g. CNNDetection's
`blur_jpg_prob0.5.pth`), wraps the single-logit sigmoid as the `[1,2]`
contract, and verifies torch/ONNX parity:

```
python experiments/export_mobile_onnx.py --profile models/cnndetection-runtime.json \
    --checkpoint models/blur_jpg_prob0.5.pth \
    --out deepfakeclassifier/src/main/assets/deepfake-lens.onnx
```

**Do NOT ship the `--quantize-int8` output as `deepfake-lens.onnx`.**
INT8 dynamic quantization emits `ConvInteger`/`MatMulInteger`/
`DynamicQuantizeLinear` ops that `onnxruntime-android` cannot create a
session for (verified: instrumented test `classify` returned null on an
Android-14 x86_64 emulator, 2026-09-14). Ship the fp32 export (~94 MB);
the bundled `deepfake-lens.onnx` is that verified artifact.

Contract (must match `OnnxClassifier.kt` / `export_onnx.py` defaults):

- input name `input`, shape `[1, 3, 224, 224]`, float32 NCHW,
  ImageNet-normalized RGB (mean 0.485/0.456/0.406, std 0.229/0.224/0.225)
- output name `logits`, shape `[1, 2]`, softmax over classes
  `real=0, synthetic-fake=1`
- dynamic batch axis is allowed

Without the file the app degrades to heuristics only, and the analysis
says so. A model trained with `--sbi` learns blending artifacts from real
images only; see `docs/deepfake-lens-cli.md` and `experiments/README.md`.

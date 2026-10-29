#!/usr/bin/env python3
"""Fetch the Community Forensics ViT-S/384 detector and export it to ONNX.

Source (primary, MIT code + weights): Hugging Face ``OwensLab/commfor-model-384``
at a pinned commit, ``model.safetensors`` (ViTClassifier from
github.com/JeongsooP/Community-Forensics ``models.py``: timm
``vit_small_patch16_384`` with ``head = Linear(384, 1)``; state-dict keys are
prefixed ``vit.``). Park & Owens, "Community Forensics", CVPR 2025,
arXiv:2411.04125.

Steps (each verified):
1. download ``model.safetensors`` from the pinned revision and check its sha256;
2. build the timm model (``pretrained=False`` — no other download) and load
   the weights strictly;
3. export to ONNX (legacy TorchScript exporter, opset 17, input ``input``
   (1,3,384,384) float32, output ``logit`` (1,1)), check ONNX Runtime against
   PyTorch on a fixed input, and compare the file's sha256 with the pinned
   export digest.

The ONNX digest is reproducible with the recorded toolchain
(``EXPORT_TOOLCHAIN``). Another torch/onnx version may serialize a
numerically identical graph with different bytes: the script then stops
unless ``--accept-new-export`` is given, and the new file has to be pinned
with ``deepfake-lens vendor-weights pin community-forensics-vit-runtime.json``
before a scan will load it.

Needs: torch, timm, safetensors, onnx, onnxruntime (export-time only; a scan
needs only onnxruntime). Output: ``<models dir>/checkpoints/community-forensics/
commfor-vit-s384.onnx`` — the path the runtime profiles name.

Usage:
    python scripts/fetch_community_forensics.py [--dest <models dir>] [--force]
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deepfake_lens.cli import default_models_dir  # noqa: E402

HF_REPO = "OwensLab/commfor-model-384"
HF_REVISION = "6076002bf0d9dd37537f965ee2f06f826c333b61"
SAFETENSORS_URL = f"https://huggingface.co/{HF_REPO}/resolve/{HF_REVISION}/model.safetensors"
SAFETENSORS_SHA256 = "b89f36275f3bf5e2b040eee36597a8f19db051bff9a473a9cf7b2466284fb387"
SAFETENSORS_BYTES = 87_262_324
CODE_URL = "https://github.com/JeongsooP/Community-Forensics/blob/ee5b71d43db0f3779e1edd64ee927b13f2dd6ad4/models.py"

ONNX_RELPATH = Path("checkpoints/community-forensics/commfor-vit-s384.onnx")
# sha256 of the ONNX file this script produced with EXPORT_TOOLCHAIN; the same
# value is the pin in community-forensics-vit-runtime.json.
ONNX_SHA256 = "32d3f2ed0ec00fc66df6e987210fb55a862fcf00b2d359b2290acc4446626369"
EXPORT_TOOLCHAIN = "torch 2.14.1, timm 1.0.30, onnx 1.23.2, onnxruntime 1.31.1 (macOS arm64, CPython 3.12)"
OPSET = 17
INPUT_SIZE = 384

LICENSE_NOTICE = f"""\
Community Forensics checkpoint notice
- Weights: {HF_REPO}@{HF_REVISION} model.safetensors (MIT, Jeongsoo Park / Owens Lab).
- Code: {CODE_URL} (MIT).
- Output is an uncalibrated detector score; deepfake-lens reports it as an
  unmeasured reference signal only (never a conclusion).
"""

_CHUNK = 1 << 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path, *, max_bytes: int) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "deepfake-lens-fetch-community-forensics/1.0"})
    written = 0
    with urllib.request.urlopen(request) as response, dest.open("wb") as handle:
        for chunk in iter(lambda: response.read(_CHUNK), b""):
            written += len(chunk)
            if written > max_bytes:
                raise RuntimeError(f"download exceeds {max_bytes} bytes")
            handle.write(chunk)


def build_model(weights: Path):
    import timm
    import torch
    from safetensors.torch import load_file

    model = timm.create_model("vit_small_patch16_384", pretrained=False, num_classes=0)
    model.head = torch.nn.Linear(384, 1, bias=True)
    state = {key.removeprefix("vit."): value for key, value in load_file(str(weights)).items()}
    model.load_state_dict(state, strict=True)
    model.eval()
    return model


def export_onnx(model, out: Path) -> None:
    import torch

    dummy = torch.zeros(1, 3, INPUT_SIZE, INPUT_SIZE, dtype=torch.float32)
    with torch.no_grad():
        torch.onnx.export(
            model, (dummy,), str(out), input_names=["input"], output_names=["logit"],
            opset_version=OPSET, do_constant_folding=True, dynamo=False,
        )


def check_parity(model, onnx_path: Path) -> float:
    """Max |torch - onnxruntime| logit difference on a fixed pseudo-random input."""
    import numpy as np
    import onnxruntime as ort
    import torch

    x = np.random.default_rng(0).standard_normal((1, 3, INPUT_SIZE, INPUT_SIZE)).astype("float32")
    with torch.no_grad():
        expected = model(torch.from_numpy(x)).numpy()
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    actual = session.run(None, {"input": x})[0]
    return float(np.abs(expected - actual).max())


def main(argv: list[str] | None = None) -> int:
    from deepfake_lens.cli_parser import KoreanArgumentParser

    parser = KoreanArgumentParser(description="Community Forensics ViT-S/384 가중치를 내려받아 ONNX로 변환하고 sha256을 검증합니다.")
    parser.add_argument("--dest", type=Path, default=None, help="모델 폴더(기본: $DEEPFAKE_LENS_MODELS_DIR 또는 패키지 models 폴더)")
    parser.add_argument("--weights", type=Path, default=None, help="이미 내려받은 model.safetensors(sha256을 확인한 뒤 사용)")
    parser.add_argument("--force", action="store_true", help="이미 있는 ONNX 파일을 덮어씀")
    parser.add_argument("--accept-new-export", action="store_true", help="고정된 ONNX sha256과 다른 바이트(다른 도구 버전)를 받아들임 — 이후 vendor-weights pin 필요")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    print(LICENSE_NOTICE)

    dest = (args.dest or default_models_dir()) / ONNX_RELPATH
    if dest.exists() and not args.force:
        print(f"error: {dest} already exists; pass --force to overwrite", file=sys.stderr)
        return 2
    dest.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="commfor-") as tmp:
        weights = args.weights
        if weights is None:
            weights = Path(tmp) / "model.safetensors"
            print(f"downloading {SAFETENSORS_URL}")
            try:
                download(SAFETENSORS_URL, weights, max_bytes=SAFETENSORS_BYTES * 2)
            except Exception as exc:  # noqa: BLE001 - report any fetch failure uniformly
                print(f"error: download failed: {exc}", file=sys.stderr)
                return 1
        digest = sha256_file(weights)
        if digest != SAFETENSORS_SHA256:
            print(f"error: safetensors sha256 mismatch: expected {SAFETENSORS_SHA256}, got {digest}", file=sys.stderr)
            return 1
        print(f"safetensors sha256 verified: {digest}")
        try:
            model = build_model(weights)
        except ImportError as exc:
            print(f"error: export needs torch, timm, safetensors, onnx, onnxruntime ({exc.name or exc})", file=sys.stderr)
            return 1
        part = Path(tmp) / "model.onnx"
        export_onnx(model, part)
        diff = check_parity(model, part)
        print(f"onnxruntime vs torch max |logit diff| = {diff:.2e}")
        if diff > 1e-3:
            print("error: ONNX output does not match PyTorch", file=sys.stderr)
            return 1
        onnx_digest = sha256_file(part)
        if onnx_digest != ONNX_SHA256:
            if not args.accept_new_export:
                print(
                    f"error: ONNX sha256 {onnx_digest} differs from the pinned export {ONNX_SHA256}\n"
                    f"  (pinned toolchain: {EXPORT_TOOLCHAIN}). Re-run with --accept-new-export, then\n"
                    "  pin it: deepfake-lens vendor-weights pin community-forensics-vit-runtime.json",
                    file=sys.stderr,
                )
                return 1
            print(f"warning: new ONNX bytes {onnx_digest}; pin them before scanning", file=sys.stderr)
        part.replace(dest)
    print(f"onnx sha256: {onnx_digest}" + (" (verified)" if onnx_digest == ONNX_SHA256 else ""))
    print(f"saved {dest} ({dest.stat().st_size / (1024 ** 2):.2f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

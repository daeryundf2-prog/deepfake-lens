#!/usr/bin/env python3
"""Fetch the FaceForensics++ EfficientNet-B0 face-manipulation checkpoint.

Downloads `efficientnet_b0_ffpp_c23.pth` (~17 MB) into `models/` so the
committed `models/faceswap-ffpp-runtime.json` profile (and the
`faceswap-ffpp-frames-runtime.json` per-frame video variant) can drive
scans. The checkpoint is NOT committed to git (see .gitignore).

Source: Xicor9/efficientnet-b0-ffpp-c23 on Hugging Face — an
EfficientNet-B0 fine-tuned on FaceForensics++ C23 frames covering all
five manipulation families (Deepfakes, Face2Face, FaceSwap, FaceShifter,
NeuralTextures). This complements the generator-detector members (AIDE,
UnivFD): faceswap reuses real pixels composited over a real frame, which
generator detectors structurally do not see.

The publisher ships no sha256; the script prints the downloaded digest —
record it in models/NOTICE.md on first fetch.

Usage:
    python scripts/fetch_faceswap.py [--sha256 <hex>]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "models"

CHECKPOINT_URL = (
    "https://huggingface.co/Xicor9/efficientnet-b0-ffpp-c23/resolve/main/"
    "efficientnet_b0_ffpp_c23.pth"
)
SOURCE_URL = "https://huggingface.co/Xicor9/efficientnet-b0-ffpp-c23"
NAME = "efficientnet_b0_ffpp_c23.pth"

_CHUNK = 1024 * 1024


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sha256", default=None, help="expected sha256 hex digest to pin")
    args = parser.parse_args()

    MODELS_DIR.mkdir(exist_ok=True)
    target = MODELS_DIR / NAME
    if target.is_file() and target.stat().st_size > 1_000_000:
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        print(f"{NAME}: already present ({target.stat().st_size} bytes, sha256 {digest})")
        return 0

    print(f"{NAME}: downloading from {CHECKPOINT_URL}")
    try:
        request = urllib.request.Request(CHECKPOINT_URL, headers={"User-Agent": "deepfake-lens-fetch-faceswap/1.0"})
        digest = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=600) as response, target.open("wb") as out:
            while True:
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
    except Exception as exc:
        target.unlink(missing_ok=True)
        print(f"  download failed: {exc}")
        return 1

    hex_digest = digest.hexdigest()
    print(f"  saved {target.stat().st_size} bytes -> {target}")
    print(f"  sha256: {hex_digest}")
    if args.sha256 and hex_digest.lower() != args.sha256.lower():
        print("  ERROR: sha256 mismatch — deleting downloaded file")
        target.unlink(missing_ok=True)
        return 1
    print(f"Checkpoint license/terms: see {SOURCE_URL} - do not redistribute without checking upstream terms.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

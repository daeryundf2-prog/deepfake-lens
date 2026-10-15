#!/usr/bin/env python3
"""Fetch the watermark decoder weights into the models folder (techniques-2026 items 4–5).

Every file is checked against the sha256 pinned in ``models/assets.json``
before it is moved into place; a mismatch is refused and nothing is written.
The analysis never downloads — without these files the checks are
``skipped`` "가중치 없음: …".

| asset (models folder name)       | source                                                                  | license |
| --- | --- | --- |
| trustmark-decoder_Q.onnx          | https://cai-watermark.adobe.net/watermarking/trustmark-models/decoder_Q.onnx | MIT (adobe/trustmark) |
| trustmark-decoder_P.onnx          | …/decoder_P.onnx                                                          | MIT |
| trustmark-decoder_C.onnx          | …/decoder_C.onnx                                                          | MIT |
| videoseal-y_256b_img.jit          | https://dl.fbaipublicfiles.com/videoseal/y_256b_img.jit                   | MIT (facebookresearch/videoseal) |
| audioseal-detector_base.pth       | https://huggingface.co/facebook/audioseal/resolve/3c19eba5…/detector_base.pth | MIT (facebook/audioseal) |

Usage:
    python scripts/fetch_watermark_models.py [--dest <models dir>] [--only trustmark|meta] [--verify-only]
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

DOWNLOADS = {
    "trustmark-decoder_Q.onnx": "https://cai-watermark.adobe.net/watermarking/trustmark-models/decoder_Q.onnx",
    "trustmark-decoder_P.onnx": "https://cai-watermark.adobe.net/watermarking/trustmark-models/decoder_P.onnx",
    "trustmark-decoder_C.onnx": "https://cai-watermark.adobe.net/watermarking/trustmark-models/decoder_C.onnx",
    "videoseal-y_256b_img.jit": "https://dl.fbaipublicfiles.com/videoseal/y_256b_img.jit",
    "audioseal-detector_base.pth": "https://huggingface.co/facebook/audioseal/resolve/3c19eba53390776cf2cc9ed5f6c9ac67ce72ecba/detector_base.pth",
}
GROUPS = {"trustmark": [name for name in DOWNLOADS if name.startswith("trustmark-")],
          "meta": [name for name in DOWNLOADS if not name.startswith("trustmark-")]}
USER_AGENT = "deepfake-lens-fetch-watermark-models/1.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[1]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help
    from deepfake_lens.model_assets import expected_sha256
    from deepfake_lens.vendor_weights import default_models_dir

    parser = KoreanArgumentParser(description="워터마크 디코더 가중치(TrustMark·VideoSeal·AudioSeal)를 sha256 확인 후 models 폴더에 받습니다.")
    parser.add_argument("--dest", type=Path, default=None, help="models 폴더(기본: 유효 models 폴더)")
    parser.add_argument("--only", choices=sorted(GROUPS), default=None, help="trustmark 또는 meta만")
    parser.add_argument("--verify-only", action="store_true", help="내려받지 않고 있는 파일의 sha256만 확인")
    args = parser.parse_args(argv)
    dest = args.dest or default_models_dir()
    names = GROUPS[args.only] if args.only else list(DOWNLOADS)
    status = 0
    for name in names:
        expected = expected_sha256(name)
        if expected is None:
            print(f"{name}: assets.json에 고정값 없음 — 거부", file=sys.stderr)
            status = 1
            continue
        target = dest / name
        if target.is_file() and _sha256(target) == expected:
            print(f"{name}: 이미 있음(sha256 일치)")
            continue
        if args.verify_only:
            print(f"{name}: {'sha256 불일치' if target.is_file() else '없음'}")
            status = 1
            continue
        fd, tmp = tempfile.mkstemp(prefix=".fetch-", dir=dest)
        os.close(fd)
        try:
            request = urllib.request.Request(DOWNLOADS[name], headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=600) as response, open(tmp, "wb") as handle:
                for chunk in iter(lambda: response.read(1 << 20), b""):
                    handle.write(chunk)
            actual = _sha256(Path(tmp))
            if actual != expected:
                print(f"{name}: sha256 불일치(기대 {expected[:12]}…, 실제 {actual[:12]}…) — 저장하지 않음", file=sys.stderr)
                status = 1
                continue
            os.replace(tmp, target)
            print(f"{name}: 받음({target.stat().st_size} bytes, sha256 일치)")
        finally:
            Path(tmp).unlink(missing_ok=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())

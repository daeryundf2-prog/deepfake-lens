#!/usr/bin/env python3
"""Fetch SyncNet lip-sync weights into the models dir (not committed).

Downloads two files used by ``deepfake_lens.lipsync``'s SyncNet path:

- ``syncnet_v2.model`` (~2.6 MB) — the SyncNet audio-visual embedding net
- ``sfd_face.pth`` (~90 MB) — the S3FD face detector the pipeline crops with

Both are hosted by the Oxford VGG lipsync page (joonson/syncnet_python's
canonical source). Weights are research-licensed — check upstream terms
before redistributing. Requires ``pip install syncnet-python`` plus
``scenedetect<0.6`` (0.7 removed VideoManager which the package imports).

The default destination is ``deepfake_lens.cli.default_models_dir()``
($DEEPFAKE_LENS_MODELS_DIR or the packaged models dir) — the directory
``deepfake_lens.lipsync`` reads them from (G27).

Usage:
    python scripts/fetch_syncnet.py [--dest DIR]
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deepfake_lens.cli import default_models_dir  # noqa: E402

FILES = {
    "syncnet_v2.model": "https://www.robots.ox.ac.uk/~vgg/software/lipsync/data/syncnet_v2.model",
    "sfd_face.pth": "https://www.robots.ox.ac.uk/~vgg/software/lipsync/data/sfd_face.pth",
}


def main(argv: list[str] | None = None) -> int:
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help
    parser = KoreanArgumentParser(description="SyncNet 립싱크 가중치를 모델 폴더로 내려받습니다.")
    parser.add_argument("--dest", type=Path, default=None, help="내려받을 폴더(기본: deepfake_lens.cli.default_models_dir())")
    args = parser.parse_args(argv)
    models_dir = Path(args.dest) if args.dest else default_models_dir()
    models_dir.mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        target = models_dir / name
        if target.is_file() and target.stat().st_size > 1_000_000:
            print(f"{name}: already present ({target.stat().st_size} bytes)")
            continue
        print(f"{name}: downloading from {url}")
        try:
            with urllib.request.urlopen(url, timeout=600) as response:
                target.write_bytes(response.read())
        except Exception as exc:
            print(f"  download failed: {exc}")
            return 1
        print(f"  saved {target.stat().st_size} bytes -> {target}")
    print("Done. SyncNet path in deepfake_lens.lipsync will pick these up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

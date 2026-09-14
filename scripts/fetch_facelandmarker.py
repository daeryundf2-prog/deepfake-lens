#!/usr/bin/env python3
"""Fetch the MediaPipe FaceLandmarker ``.task`` asset with sha256 verification.

Downloads ``face_landmarker.task`` (~4 MB) into ``models/`` so the Tasks-API
path in ``deepfake_lens/face.py`` (``landmarks_source="mediapipe-facelandmarker"``)
can run on mediapipe >= 0.10.30 / 1.x, where the legacy ``mp.solutions``
FaceMesh API no longer exists. The asset is NOT committed to git (see
.gitignore); it can also be placed manually or pointed at with the
``DEEPFAKE_LENS_FACE_LANDMARKER`` environment variable.

Official distribution point: Google's MediaPipe model bucket, the same URL
the MediaPipe Tasks documentation links for the FaceLandmarker vision task.

No official sha256 is published. Pass --sha256 <hex> to pin a digest you
trust; the script always prints the downloaded digest so it can be recorded.

Usage:
    python scripts/fetch_facelandmarker.py [--sha256 <hex>]
    python scripts/fetch_facelandmarker.py --url <direct-url> [--sha256 <hex>]
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST_DIR = REPO_ROOT / "models"
DEFAULT_NAME = "face_landmarker.task"

FACELANDMARKER_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float32/1/face_landmarker.task"
)
FACELANDMARKER_DOC_URL = "https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker"

EXPECTED_SHA256: str | None = None

LICENSE_NOTICE = """\
FaceLandmarker model notice
- Asset: face_landmarker.task (~4 MB), Google's MediaPipe Tasks face
  landmark model (478-point canonical face mesh, float32).
- Terms: distributed under the MediaPipe model terms — verify the current
  terms at {doc} before relying on outputs in a product.
- The asset is NOT covered by this repository's license and must not be
  committed — you are downloading it for local use only.
- Model: {model}
"""

_CHUNK = 1024 * 1024
_USER_AGENT = "deepfake-lens-fetch-facelandmarker/1.0"


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request) as response:
        first = response.read(256)
        if b"<html" in first.lower():
            raise RuntimeError(
                "URL returned an HTML page instead of the model asset; "
                "pass a direct-download link via --url"
            )
        with dest.open("wb") as handle:
            handle.write(first)
            for chunk in iter(lambda: response.read(_CHUNK), b""):
                handle.write(chunk)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch the FaceLandmarker .task asset into models/.")
    parser.add_argument("--url", default=FACELANDMARKER_MODEL_URL, help="direct model download URL")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST_DIR, help=f"destination directory (default: {DEFAULT_DEST_DIR})")
    parser.add_argument("--name", default=DEFAULT_NAME, help=f"destination filename (default: {DEFAULT_NAME})")
    parser.add_argument("--sha256", help="expected lowercase sha256 hex digest; the download fails if it does not match")
    parser.add_argument("--force", action="store_true", help="overwrite an existing destination file")
    args = parser.parse_args(argv)

    # Windows consoles default to a legacy code page (e.g. cp949) that cannot
    # encode the em-dashes in LICENSE_NOTICE; reconfigure so print() can't crash.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    dest_dir = Path(args.dest)
    dest = dest_dir / args.name
    part = dest.with_name(dest.name + ".part")

    print(LICENSE_NOTICE.format(source=FACELANDMARKER_DOC_URL, doc=FACELANDMARKER_DOC_URL, model=FACELANDMARKER_MODEL_URL))

    if dest.exists() and not args.force:
        print(f"error: {dest} already exists; pass --force to overwrite", file=sys.stderr)
        return 2

    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        download(args.url, part)
    except Exception as exc:
        part.unlink(missing_ok=True)
        print(f"error: download failed: {exc}", file=sys.stderr)
        return 1

    digest = sha256_file(part)
    expected = (args.sha256 or EXPECTED_SHA256 or "").lower()
    if expected and digest != expected:
        part.unlink(missing_ok=True)
        print(f"error: sha256 mismatch: expected {expected}, got {digest}", file=sys.stderr)
        return 1

    part.replace(dest)
    size_mib = dest.stat().st_size / (1024 * 1024)
    print(f"downloaded {args.url}\n  -> {dest}")
    print(f"sha256: {digest} (no --sha256 supplied; record this digest)" if not args.sha256 else f"sha256: {digest} (verified)")
    print(f"saved {dest} ({size_mib:.2f} MiB)")
    print("face.py auto-discovers models/face_landmarker.task; DEEPFAKE_LENS_FACE_LANDMARKER overrides the path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

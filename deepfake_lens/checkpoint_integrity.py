"""Checkpoint loading with integrity checks.

All ``torch.load`` sites route through :func:`load_torch_state` so the
policy lives in one place:

- ``weights_only=True`` is mandatory — a ``.pth`` is a pickle, and an
  operator-supplied checkpoint is code execution if loaded unrestricted.
- If a ``<checkpoint>.sha256`` sidecar exists, its hex digest must match
  the file. The sidecar format is ``<64-hex>`` optionally followed by
  whitespace and a filename, like ``sha256sum`` output.
- ``DEEPFAKE_LENS_CHECKPOINT_SHA256`` pins the expected digest for
  environments that cannot ship sidecars; when set, verification is
  required (not optional) and applies to every checkpoint load.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def _expected_sha256(checkpoint: Path) -> str | None:
    sidecar = checkpoint.with_name(checkpoint.name + ".sha256")
    if sidecar.is_file():
        text = sidecar.read_text(encoding="utf-8").strip()
        digest = text.split()[0] if text else ""
        if len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest):
            raise RuntimeError(f"malformed sha256 sidecar: {sidecar}")
        return digest.lower()
    pinned = os.environ.get("DEEPFAKE_LENS_CHECKPOINT_SHA256", "").strip()
    return pinned.lower() or None


def verify_checkpoint_sha256(checkpoint: Path, expected: str | None = None) -> None:
    """Verify the checkpoint digest against a sidecar, env pin, or declared pin.

    ``expected`` is a caller-supplied digest — when present it is the
    strongest pin and wins. (Runtime profiles declare theirs as
    ``pin.sha256``; ``model_adapter`` verifies that through
    ``model_pins.verify_pin`` on every load.)
    No pin configured means "unverified" — operators that need provenance
    must declare a hash. A configured pin that mismatches is a hard failure.
    """
    if expected is None:
        expected = _expected_sha256(checkpoint)
    if expected is None:
        return
    expected = expected.strip().lower()
    digest = _stream_sha256(checkpoint)
    if digest != expected:
        raise RuntimeError(
            f"checkpoint sha256 mismatch: {checkpoint}\n"
            f"  expected {expected}\n  got      {digest}"
        )


def _stream_sha256(checkpoint: Path) -> str:
    digest = hashlib.sha256()
    with checkpoint.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_torch_state(checkpoint: Path | str, *, expected_sha256: str | None = None):
    """Load a torch checkpoint with weights_only=True and hash verification."""
    checkpoint = Path(checkpoint)
    verify_checkpoint_sha256(checkpoint, expected_sha256)
    import torch

    return torch.load(str(checkpoint), map_location="cpu", weights_only=True)

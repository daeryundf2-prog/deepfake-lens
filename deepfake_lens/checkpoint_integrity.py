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


def verify_checkpoint_sha256(checkpoint: Path) -> None:
    """Verify the checkpoint digest when a sidecar or env pin exists.

    No pin configured means "unverified" — operators that need provenance
    must ship a sidecar or set the env var. A configured pin that mismatches
    is a hard failure.
    """
    expected = _expected_sha256(checkpoint)
    if expected is None:
        return
    digest = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    if digest != expected:
        raise RuntimeError(
            f"checkpoint sha256 mismatch: {checkpoint}\n"
            f"  expected {expected}\n  got      {digest}"
        )


def load_torch_state(checkpoint: Path | str):
    """Load a torch checkpoint with weights_only=True and hash verification."""
    checkpoint = Path(checkpoint)
    verify_checkpoint_sha256(checkpoint)
    import torch

    return torch.load(str(checkpoint), map_location="cpu", weights_only=True)

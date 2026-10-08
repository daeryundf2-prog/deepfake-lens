"""Model weight pins (G9, G10).

Every runtime profile that loads weights carries a ``pin`` object, and a
weight is loaded only when the pin is present and matches:

- local checkpoints: ``{"sha256": "<64 hex>"}`` — the file's SHA-256 is
  recomputed on every load (no "verified once" cache: a checkpoint replaced
  between two scans must be caught on the second scan);
- Hugging Face hub models: ``{"revision": "<40 hex commit sha>"}`` — passed
  as ``revision=`` to every ``from_pretrained`` call so the hub serves that
  exact commit. A branch or tag name ("main", "v1") is a moving target and
  does not count as a pin.

Runtimes that use both (``clip-linear``: local head + hub backbone) need
both keys; ``binoculars`` additionally pins its observer LM under
``observer_revision``. A ``video-frames`` profile's pin applies to its
``inner`` image profile.

``deepfake-lens vendor-weights pin <profile>`` fills the values.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

PIN_FIELD = "pin"

# Coverage reasons (Korean, user-facing). A failed coverage entry makes the
# verdict undetermined (decision rule 3).
UNPINNED_REASON = "미고정 프로필"
MISMATCH_REASON = "무결성 불일치"

# SHA-256 hex digest length; git/HF commit ids are SHA-1 (40 hex).
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
# Streaming chunk for hashing multi-GB checkpoints without loading them.
_HASH_CHUNK_BYTES = 1 << 20

# Runtimes whose weights come from the hub (revision pin) versus a local
# checkpoint file (sha256 pin). Kept here, not imported from model_adapter,
# so vendor_weights/CLI can use the rules without importing the adapter.
HUB_ONLY_RUNTIMES = frozenset({"hf-text-classifier", "hf-image-classifier", "hf-audio-classifier", "causal-lm-ppl", "binoculars"})
LOCAL_RUNTIMES = frozenset({"onnx", "torchscript", "aide", "torchvision", "aasist", "onnx-audio"})
LOCAL_AND_HUB_RUNTIMES = frozenset({"clip-linear"})


class PinError(RuntimeError):
    """A weight load refused by the pin policy; ``reason`` is the coverage text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def profile_pin(profile: Mapping[str, Any]) -> dict[str, str]:
    """The profile's ``pin`` object with string values (empty when absent)."""
    raw = profile.get(PIN_FIELD)
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): str(value or "").strip().lower() for key, value in raw.items()}


def declared_sha256(profile: Mapping[str, Any]) -> str:
    return profile_pin(profile).get("sha256", "")


def declared_revision(profile: Mapping[str, Any], key: str = "revision") -> str:
    return profile_pin(profile).get(key, "")


def is_sha256(value: str) -> bool:
    return bool(_SHA256_RE.match(value))


def is_commit_sha(value: str) -> bool:
    return bool(_COMMIT_RE.match(value))


def pin_target(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    """The profile whose weights the pin describes (``inner`` for video-frames)."""
    runtime = str(profile.get("runtime") or "").lower()
    inner = profile.get("inner") or profile.get("frame_profile")
    if runtime == "video-frames" and isinstance(inner, Mapping):
        return inner
    return profile


def required_pin_keys(profile: Mapping[str, Any]) -> tuple[str, ...]:
    """Pin keys a profile needs before its weights may load."""
    target = pin_target(profile)
    runtime = str(target.get("runtime") or "").lower()
    if runtime == "binoculars":
        return ("revision", "observer_revision")
    if runtime in HUB_ONLY_RUNTIMES:
        return ("revision",)
    if runtime in LOCAL_AND_HUB_RUNTIMES:
        return ("sha256", "revision")
    if runtime in LOCAL_RUNTIMES:
        return ("sha256",)
    return ()


def empty_pin_for(profile: Mapping[str, Any]) -> dict[str, str]:
    """The placeholder ``pin`` object a new profile ships with."""
    return {key: "" for key in required_pin_keys(profile)}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_revision(profile: Mapping[str, Any], key: str = "revision") -> str:
    """The pinned hub commit for ``key``; raises :class:`PinError` when unpinned."""
    value = declared_revision(profile, key)
    if not value:
        raise PinError(f"{UNPINNED_REASON}: pin.{key} 비어 있음")
    if not is_commit_sha(value):
        raise PinError(f"{UNPINNED_REASON}: pin.{key} 값이 40자리 커밋 SHA가 아님 (브랜치·태그는 고정이 아님)")
    return value


def verify_pin(profile: Mapping[str, Any], checkpoint: Path | None) -> None:
    """Refuse a weight load whose pin is missing, malformed or mismatched.

    ``profile`` is the profile whose runtime loads weights (for video-frames
    callers pass the ``inner`` profile with the outer pin merged in).
    ``checkpoint`` is the resolved local file for sha256-pinned runtimes.
    Raises :class:`PinError` with the coverage reason; returns on success.
    The checkpoint is re-hashed on every call.
    """
    keys = required_pin_keys(profile)
    if not keys:
        return
    if not isinstance(profile.get(PIN_FIELD), Mapping):
        raise PinError(f"{UNPINNED_REASON}: pin 객체 없음")
    for key in keys:
        if key == "sha256":
            continue
        require_revision(profile, key)
    if "sha256" not in keys:
        return
    expected = declared_sha256(profile)
    if not expected:
        raise PinError(f"{UNPINNED_REASON}: pin.sha256 비어 있음")
    if not is_sha256(expected):
        raise PinError(f"{UNPINNED_REASON}: pin.sha256 값이 64자리 16진수가 아님")
    if checkpoint is None or not checkpoint.is_file():
        raise PinError(f"{MISMATCH_REASON}: 체크포인트 파일 없음 ({checkpoint})")
    actual = file_sha256(checkpoint)
    if actual != expected:
        raise PinError(
            f"{MISMATCH_REASON}: {checkpoint.name} sha256 기대 {expected[:12]}… 실제 {actual[:12]}…"
        )

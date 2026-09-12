from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


REPORT_KEY_ENV = "DEEPFAKE_LENS_REPORT_KEY"
SIGNATURE_FIELD = "signature"
SIGNATURE_KEY_ID_FIELD = "signature_key_id"
SIGNATURE_NOTE_FIELD = "signature_note"
SIGNATURE_VERSION = "hmac-sha256-v1"
UNSIGNED_NOTE = (
    f"unsigned: no report key configured (set {REPORT_KEY_ENV} or pass --key-file)"
)

# Fields excluded from (or managed by) the MAC. signature_key_id IS covered by
# the signature so a swapped key id breaks verification.
_STRIPPED_FIELDS = {SIGNATURE_FIELD, SIGNATURE_NOTE_FIELD}


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    status: str  # "verified" | "tampered" | "unsigned" | "unreadable" | "no-key"
    key_id: str | None = None

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def resolve_report_key(key_file: Path | str | None = None) -> bytes | None:
    """Key material for report signing.

    ``key_file`` wins when given; otherwise the ``DEEPFAKE_LENS_REPORT_KEY``
    environment variable is used as literal key material (UTF-8). Returns
    None when neither is configured — callers must then emit an unsigned
    report with a note rather than failing or fabricating a signature.
    """
    if key_file is not None:
        try:
            content = Path(key_file).read_bytes().strip()
        except OSError:
            return None
        return content or None
    env_value = os.environ.get(REPORT_KEY_ENV, "").strip()
    return env_value.encode("utf-8") if env_value else None


def key_id_for(key: bytes) -> str:
    """Non-secret key identifier: prefix of the key's SHA-256."""
    return f"{SIGNATURE_VERSION}:{hashlib.sha256(key).hexdigest()[:12]}"


def sign_report(report: dict[str, object], key: bytes | str | None, *, key_id: str | None = None) -> dict[str, object]:
    """Return a copy of ``report`` with an HMAC-SHA256 ``signature`` appended.

    The MAC covers the canonical JSON of every field except ``signature`` and
    ``signature_note`` (``signature_key_id`` is included so it cannot be
    swapped). HMAC proves integrity *to the key holder* only — anyone with the
    key can produce a valid signature, so this is tamper evidence for stored
    reports, not legal non-repudiation.

    With no key the report is returned unsigned: ``signature=None`` plus a
    ``signature_note`` explaining how to configure one.
    """
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    signed = {field: value for field, value in report.items() if field not in _STRIPPED_FIELDS}
    if not key_bytes:
        signed[SIGNATURE_FIELD] = None
        signed[SIGNATURE_NOTE_FIELD] = UNSIGNED_NOTE
        return signed
    signed[SIGNATURE_KEY_ID_FIELD] = key_id or key_id_for(key_bytes)
    signed[SIGNATURE_FIELD] = hmac.new(key_bytes, _canonical(signed), hashlib.sha256).hexdigest()
    return signed


def verify_report(report_or_path: dict[str, object] | Path | str, key: bytes | str | None) -> VerificationResult:
    """Verify a signed report dict or JSON file.

    ``verified`` is True only when a signature field is present, a key is
    available, and the recomputed MAC matches (``hmac.compare_digest``).
    """
    if isinstance(report_or_path, dict):
        payload: dict[str, object] | None = report_or_path
    else:
        try:
            loaded = json.loads(Path(report_or_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = None
        payload = loaded if isinstance(loaded, dict) else None
    if payload is None:
        return VerificationResult(False, "unreadable")
    signature = payload.get(SIGNATURE_FIELD)
    if signature is None:
        return VerificationResult(False, "unsigned")
    if not isinstance(signature, str):
        return VerificationResult(False, "tampered")
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    key_id = payload.get(SIGNATURE_KEY_ID_FIELD) if isinstance(payload.get(SIGNATURE_KEY_ID_FIELD), str) else None
    if not key_bytes:
        return VerificationResult(False, "no-key", key_id)
    body = {field: value for field, value in payload.items() if field not in _STRIPPED_FIELDS}
    expected = hmac.new(key_bytes, _canonical(body), hashlib.sha256).hexdigest()
    verified = hmac.compare_digest(expected, signature)
    return VerificationResult(verified, "verified" if verified else "tampered", key_id)


def sign_report_file(path: Path | str, key: bytes | str | None) -> dict[str, object]:
    """Sign a JSON report file in place; returns the signed payload."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    signed = sign_report(payload if isinstance(payload, dict) else {"report": payload}, key)
    Path(path).write_text(json.dumps(signed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return signed


def _canonical(report: dict[str, object]) -> bytes:
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

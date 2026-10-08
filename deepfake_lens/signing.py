"""HMAC-SHA256 report signatures (G30).

The MAC covers the canonical JSON of the whole report except the two fields
that carry the signature itself (``signature`` and ``signature_key_id``).
In particular it covers ``signature_note``, ``tool_version``, ``model_pins``
(which profile pins the tool ran with) and every item's ``sha256`` — a
one-character edit anywhere else makes verification report "변조됨".

HMAC proves integrity *to the key holder* only: anyone with the key can
produce a valid signature, so this is tamper evidence for stored reports,
not legal non-repudiation.
"""

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
TOOL_VERSION_FIELD = "tool_version"
MODEL_PINS_FIELD = "model_pins"
SIGNATURE_VERSION = "hmac-sha256-v1"
SIGNED_NOTE = (
    "HMAC-SHA256 서명: 서명 키 보유자에 대한 무결성 증명이며 법적 부인방지(전자서명)가 아닙니다. "
    "signature/signature_key_id를 제외한 본문 전체(이 문구 포함)가 서명 범위입니다."
)
UNSIGNED_NOTE = (
    f"서명 없음 (unsigned): 보고서 서명 키가 설정되지 않았습니다 — {REPORT_KEY_ENV} 환경 변수 또는 --key-file을 지정하세요."
)

# The only fields outside the MAC: the signature and the key id it was made
# with. A swapped key id is reported separately ("키 ID 불일치").
_EXCLUDED_FIELDS = frozenset({SIGNATURE_FIELD, SIGNATURE_KEY_ID_FIELD})

STATUS_REASONS = {
    "verified": "검증됨",
    "tampered": "변조됨",
    "key-mismatch": "키 ID 불일치",
    "unsigned": "서명 없음",
    "unreadable": "읽을 수 없음",
    "no-key": "검증 키 없음",
}


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    status: str  # "verified" | "tampered" | "key-mismatch" | "unsigned" | "unreadable" | "no-key"
    key_id: str | None = None

    @property
    def reason(self) -> str:
        """Korean reason shown to the examiner."""
        return STATUS_REASONS.get(self.status, self.status)

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        data["reason"] = self.reason
        return data


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


def sign_report(
    report: dict[str, object],
    key: bytes | str | None,
    *,
    key_id: str | None = None,
    model_pins: list[dict[str, object]] | None = None,
    tool_version: str | None = None,
) -> dict[str, object]:
    """Return a copy of ``report`` carrying an HMAC-SHA256 ``signature``.

    Before signing, the body is completed with ``tool_version`` and
    ``model_pins`` (explicit arguments win; otherwise a value already in the
    report is kept; otherwise the running tool version and the pins of the
    effective models directory are recorded) and a ``signature_note``. All
    of them are inside the MAC.

    With no key the report is returned unsigned: ``signature=None`` and a
    ``signature_note`` that says "서명 없음" and how to configure a key.
    """
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    body = {field: value for field, value in report.items() if field not in _EXCLUDED_FIELDS}
    if tool_version is not None or TOOL_VERSION_FIELD not in body:
        body[TOOL_VERSION_FIELD] = tool_version or _tool_version()
    if model_pins is not None or MODEL_PINS_FIELD not in body:
        body[MODEL_PINS_FIELD] = model_pins if model_pins is not None else _default_model_pins()
    if not key_bytes:
        body[SIGNATURE_NOTE_FIELD] = UNSIGNED_NOTE
        body[SIGNATURE_FIELD] = None
        return body
    body[SIGNATURE_NOTE_FIELD] = SIGNED_NOTE
    signature = hmac.new(key_bytes, _canonical(body), hashlib.sha256).hexdigest()
    body[SIGNATURE_KEY_ID_FIELD] = key_id or key_id_for(key_bytes)
    body[SIGNATURE_FIELD] = signature
    return body


def verify_report(
    report_or_path: dict[str, object] | Path | str,
    key: bytes | str | None,
    *,
    key_id: str | None = None,
) -> VerificationResult:
    """Verify a signed report dict or JSON file.

    ``verified`` is True only when a signature is present, a key is
    available, the report names that key's id, and the recomputed MAC over
    everything but ``signature``/``signature_key_id`` matches
    (``hmac.compare_digest``). A report signed under a different key id is
    "key-mismatch" (키 ID 불일치), not "tampered" (변조됨).
    """
    if isinstance(report_or_path, dict):
        payload: dict[str, object] | None = report_or_path
    else:
        try:
            loaded = json.loads(Path(report_or_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            loaded = None
        payload = loaded if isinstance(loaded, dict) else None
    if payload is None:
        return VerificationResult(False, "unreadable")
    signature = payload.get(SIGNATURE_FIELD)
    if signature is None:
        return VerificationResult(False, "unsigned")
    raw_key_id = payload.get(SIGNATURE_KEY_ID_FIELD)
    report_key_id = raw_key_id if isinstance(raw_key_id, str) else None
    if not isinstance(signature, str):
        return VerificationResult(False, "tampered", report_key_id)
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    if not key_bytes:
        return VerificationResult(False, "no-key", report_key_id)
    if report_key_id != (key_id or key_id_for(key_bytes)):
        return VerificationResult(False, "key-mismatch", report_key_id)
    body = {field: value for field, value in payload.items() if field not in _EXCLUDED_FIELDS}
    expected = hmac.new(key_bytes, _canonical(body), hashlib.sha256).hexdigest()
    verified = hmac.compare_digest(expected, signature)
    return VerificationResult(verified, "verified" if verified else "tampered", report_key_id)


def sign_report_file(path: Path | str, key: bytes | str | None) -> dict[str, object]:
    """Sign a JSON report file in place; returns the signed payload."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    signed = sign_report(payload if isinstance(payload, dict) else {"report": payload}, key)
    Path(path).write_text(json.dumps(signed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return signed


def signed_body_sha256(report: dict[str, object]) -> str:
    """SHA-256 of the canonical signed body — printed on HTML/PDF renderings
    so a paper copy can be tied to its signed JSON."""
    return hashlib.sha256(_canonical({k: v for k, v in report.items() if k not in _EXCLUDED_FIELDS})).hexdigest()


def _tool_version() -> str:
    from .core import TOOL_VERSION

    return TOOL_VERSION


def _default_model_pins() -> list[dict[str, object]]:
    from .profile_pins import profile_pins

    return profile_pins()


def _canonical(report: dict[str, object]) -> bytes:
    return json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

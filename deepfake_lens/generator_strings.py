"""Versioned generator / service string tables (TB-7; threat catalogue S-* service rows).

``models/generator-strings.json`` holds, with a source for every addition:

- ``tool_guess_rules`` — the generator-name markers of
  ``text_heuristics._direct_tool_guess`` (image metadata source guess; the
  phase-0 rules moved here unchanged, plus FLUX.2's ``AI generated;flux2``,
  ByteDance Dreamina/Seedream/Seedance, Luma, Amazon Nova, Azure OpenAI /
  Designer, Apple Image Playground);
- ``xmp_generator_names`` — ``evidence_rules.GENERATOR_NAMES`` (unchanged);
- ``c2pa_signers`` — C2PA signer / claim-generator strings and the service they
  belong to, used only to *name* the service as a neutral fact.

The strength of an item never comes from this table: the self-declaration
rules in evidence_rules / core decide it (this module adds data only).
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .result_types import EvidenceDirection, EvidenceItem, EvidenceKind, EvidenceStrength

TABLE_PATH = Path(__file__).resolve().parent / "models" / "generator-strings.json"
TABLE_SCHEMA = "generator-strings-v1"
SIGNER_TITLE = "C2PA 서명자·생성기 이름(등록표 대조)"


@lru_cache(maxsize=1)
def _table() -> tuple[str, str, dict[str, Any]]:
    raw = TABLE_PATH.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("schema") != TABLE_SCHEMA:
        raise ValueError(f"생성기 문자열 표 형식이 {TABLE_SCHEMA}가 아닙니다")
    return str(data.get("version", "")), hashlib.sha256(raw).hexdigest(), data


def table_label() -> str:
    version, digest, _ = _table()
    return f"생성기 문자열 표 {version}(sha256 {digest[:12]}…)"


def table_cache_tokens() -> list[str]:
    try:
        version, digest, _ = _table()
    except (OSError, ValueError) as exc:
        return [f"generator-strings:error:{type(exc).__name__}"]
    return [f"generator-strings:{version}:{digest}"]


def tool_guess_rules() -> list[tuple[str, list[str], str]]:
    return [(str(r["label"]), [str(m) for m in r["markers"]], str(r["reason"])) for r in _table()[2]["tool_guess_rules"]]


def xmp_generator_names() -> tuple[str, ...]:
    return tuple(str(name) for name in _table()[2]["xmp_generator_names"]["names"])


def c2pa_signer_evidence(validation: Mapping[str, object] | None) -> list[EvidenceItem]:
    """A neutral fact naming the service a manifest's signer / claim generator belongs to (no conclusion)."""
    if not validation or not validation.get("present"):
        return []
    signature = validation.get("signature")
    fields: list[tuple[str, str]] = []
    if isinstance(signature, Mapping):
        for key, label in (("common_name", "서명 인증서 이름"), ("issuer", "발급자")):
            if signature.get(key):
                fields.append((label, str(signature[key])[:200]))
    if validation.get("claim_generator"):
        fields.append(("생성기(claim_generator)", str(validation["claim_generator"])[:200]))
    if not fields:
        return []
    matches: list[tuple[str, str, dict[str, Any]]] = []
    for entry in _table()[2].get("c2pa_signers", []):
        needle = str(entry.get("match", "")).casefold()
        for label, value in fields:
            if needle and needle in value.casefold():
                matches.append((label, value, entry))
                break
    if not matches:
        return []
    shown = "; ".join(
        f"{label} 「{value}」 → 「{entry.get('service')}」(신뢰 수준: {entry.get('confidence')})" for label, value, entry in matches[:3]
    )
    trusted = "신뢰 서명자로 검증된" if validation.get("trusted") else "서명 신뢰가 확인되지 않은"
    return [EvidenceItem(
        SIGNER_TITLE,
        f"{trusted} 매니페스트의 이름 필드가 등록표 항목과 같습니다: {shown}. 이름은 서비스를 가리키는 표시일 뿐이며, 출처·생성 여부는 C2PA 검증 항목이 "
        f"서명·해시·출처 유형으로 따로 판단합니다. 기준: {table_label()}.",
        EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "c2pa",
    )]


__all__ = ["SIGNER_TITLE", "c2pa_signer_evidence", "table_cache_tokens", "table_label", "tool_guess_rules", "xmp_generator_names"]

"""C2PA and metadata forensics module.

Detects Content Authenticity Initiative (CAI) markers, C2PA manifests,
SynthID watermarks, and other provenance signals in image/video files.

This is the canonical provenance path. ``enhanced_forensics.py`` duplicates
part of the byte-marker scan for its legal-report output; see
``docs/consolidation-notes.md`` for the planned convergence.
"""

from __future__ import annotations

import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .c2pa_binding import HardBindingVerdict
from .layer_diagnostic import REFERENCE_BAND, UNAVAILABLE_BAND
from .native_path import native_safe_path
from .error_text import exception_text, failure_reason

MAX_FORENSIC_FILE_BYTES = 256 * 1024 * 1024  # 256 MB


# R8: MetadataForensicAnalysis.c2pa_status when the c2pa SDK is not installed.
C2PA_STATUS_SDK_MISSING = "sdk_missing"
C2PA_STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ForensicEvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class ProvenanceRecord:
    standard: str
    provider: str
    signed: bool
    claim_url: str | None
    details: dict[str, str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class MetadataForensicAnalysis:
    # D1: provenance-signal weight sum, reference only — no 50/20 band and no
    # verdict. Conclusions from metadata/C2PA come from the scan's evidence
    # rules (evidence_rules.py), never from this score.
    score: int
    reference_band: str
    reference_note: str
    signals: list[ForensicEvidenceSignal]
    limitations: list[str]
    provenance_records: list[ProvenanceRecord]
    has_c2pa: bool
    has_synthid: bool
    has_watermark: bool
    # R8: the SDK's C2PA status (validate_c2pa_manifest: valid / invalid /
    # absent / unavailable), or "sdk_missing" when only the byte scan ran.
    # "unavailable" means the reader failed — whether a manifest exists is
    # unknown, so the diagnostic says "C2PA 판독 불가(<reason>)", never
    # "C2PA 없음".
    c2pa_status: str = C2PA_STATUS_SDK_MISSING
    c2pa_error: str = ""

    def to_json(self) -> dict[str, object]:
        return asdict(self)



# G4 (round 5): the SDK's validation state is shown in Korean. The SDK
# reports "Valid"/"Trusted"/"Invalid" (and "WellFormed" in some releases);
# an unknown state is printed verbatim in corner brackets (an SDK value,
# not prose). Failure codes (assertion.action.malformed, …) are identifiers.
C2PA_STATE_LABELS = {
    "valid": "유효",
    "trusted": "신뢰됨",
    "invalid": "무효",
    "wellformed": "형식 정상(서명 검증 미완료)",
    "well-formed": "형식 정상(서명 검증 미완료)",
}


def c2pa_state_label(state: object) -> str:
    raw = str(state or "").strip()
    if not raw:
        return "알 수 없음"
    return C2PA_STATE_LABELS.get(raw.lower(), f"「{raw}」")


def c2pa_failure_codes_text(codes: object) -> str:
    """"실패 코드 a, b" for the SDK failure codes, or "세부 코드 없음"."""
    listed = [str(code) for code in (codes or []) if str(code)] if isinstance(codes, (list, tuple, set)) else []
    return f"실패 코드 {', '.join(sorted(listed))}" if listed else "세부 코드 없음"

def c2pa_unreadable_label(reason: str) -> str:
    """ "C2PA 판독 불가(<reason>)" — the provenance diagnostic's wording when the SDK failed (R8)."""
    return f"C2PA 판독 불가({reason or '원인 불명'})"


def analyze_metadata_forensic(path: Path | str) -> MetadataForensicAnalysis:
    """Analyze file metadata for C2PA, SynthID, and other provenance signals."""
    file_path = Path(path)
    if not file_path.is_file():
        return _error_analysis(f"파일이 존재하지 않습니다: {file_path}")

    try:
        file_size = file_path.stat().st_size
    except OSError as exc:
        return _error_analysis(f"파일 정보를 읽을 수 없습니다: {exception_text(exc)}")

    if file_size > MAX_FORENSIC_FILE_BYTES:
        return _error_analysis(f"파일이 너무 큽니다: {file_size} bytes (최대 {MAX_FORENSIC_FILE_BYTES})")

    try:
        data = file_path.read_bytes()
    except OSError as exc:
        return _error_analysis(f"파일 읽기 오류: {exception_text(exc)}")

    if len(data) == 0:
        return _error_analysis("파일이 비어 있습니다.")

    signals: list[ForensicEvidenceSignal] = []
    limitations: list[str] = []
    provenance_records: list[ProvenanceRecord] = []
    has_c2pa = False
    has_synthid = False
    has_watermark = False

    # Preferred path: real C2PA manifest validation via the official SDK.
    # When the manifest is verifiably present (or absent), the byte-scan
    # fallback below is not authoritative and is skipped/demoted.
    sdk_validation = validate_c2pa_manifest(file_path)
    c2pa_status = C2PA_STATUS_SDK_MISSING if sdk_validation is None else str(sdk_validation.get("status") or C2PA_STATUS_UNAVAILABLE)
    c2pa_error = ""
    if sdk_validation is not None and c2pa_status == C2PA_STATUS_UNAVAILABLE:
        # R8: the reader or validation raised (corrupt/truncated JUMBF,
        # unsupported container, I/O). Not "no manifest" — unknown.
        c2pa_error = str(sdk_validation.get("error") or "")
        has_c2pa = bool(sdk_validation.get("present"))
        unreadable = c2pa_unreadable_label(c2pa_error)
        signals.append(ForensicEvidenceSignal(
            unreadable,
            "공식 C2PA SDK가 매니페스트를 판독하지 못했습니다. 매니페스트가 없다는 뜻이 아니며, "
            "손상·절단되었거나 SDK가 지원하지 않는 형식일 수 있습니다.",
            0,
        ))
        limitations.append(f"{unreadable} — 출처 표준 메타데이터(C2PA)의 존재 여부를 확인하지 못했습니다.")
    elif sdk_validation is not None and sdk_validation.get("error_kind") == "hash_exclusion":
        # P0R-20: a hard-binding whose exclusions empty the asset. Reference-only
        # here (no verdict), but never shown as a mild "검증 미완료".
        has_c2pa = True
        binding = sdk_validation.get("hard_binding") or {}
        reason = str(binding.get("reason") if isinstance(binding, dict) else "") or "C2PA 해시 제외범위 이상"
        signals.append(ForensicEvidenceSignal("C2PA 하드 바인딩 제외범위 이상", reason, 0))
        limitations.append(reason)
    elif sdk_validation is not None and sdk_validation.get("present"):
        has_c2pa = True
        signature = sdk_validation.get("signature") or {}
        provenance_records.append(ProvenanceRecord(
            standard="C2PA",
            provider="CAI",
            signed=bool(signature),
            claim_url=None,
            details=sdk_validation,
        ))
        state = str(sdk_validation.get("state", ""))
        failures = list(sdk_validation.get("failure_codes") or [])
        if state.lower() in {"valid", "trusted"}:
            signer = str(signature.get("common_name", "알 수 없음"))
            signals.append(ForensicEvidenceSignal(
                "C2PA 매니페스트 검증됨",
                f"공식 SDK 검증 상태: {c2pa_state_label(state)}. 서명자: {signer}.",
                20,
            ))
        elif mismatches := sorted(str(code) for code in failures if str(code).lower().endswith(".mismatch")):
            # N6: content changed after signing — never the untrusted-signer wording.
            signals.append(ForensicEvidenceSignal(
                "C2PA 무결성 불일치",
                f"매니페스트 해시 불일치 — 서명 이후 내용이 변경됨 ({', '.join(mismatches)}).",
                10,
            ))
            limitations.append("C2PA 무결성 불일치: 매니페스트가 서명한 내용과 현재 파일이 다릅니다.")
        else:
            signals.append(ForensicEvidenceSignal(
                "C2PA 매니페스트 검증 미완료",
                f"SDK 검증 상태: {c2pa_state_label(state)} ({c2pa_failure_codes_text(failures)}). 신뢰 저장소에 없는 서명자일 수 있어 참고 수준입니다.",
                10,
            ))
        limitations.append("C2PA 검증 결과는 공식 c2pa-python SDK 기반입니다.")
    elif sdk_validation is None:
        # SDK missing: marker strings only. A coincidental byte match is not
        # a manifest, so this stays at reference weight.
        c2pa_record = _check_c2pa(data)
        if c2pa_record:
            provenance_records.append(c2pa_record)
            has_c2pa = True
            signals.append(ForensicEvidenceSignal(
                "C2PA 관련 문자열 발견",
                "매니페스트 검증이 아닌 문자열 탐지입니다. c2pa-python 설치 시 실제 검증이 가능합니다.",
                10,
            ))
    # else: the SDK ran and authoritatively found no manifest — no fallback.

    # Google tool markers are metadata attribution hints. SynthID itself is a
    # pixel-domain watermark and cannot be detected by byte scanning.
    synthid_record = _check_google_tool_metadata(data)
    if synthid_record:
        provenance_records.append(synthid_record)
        signals.append(ForensicEvidenceSignal(
            "Google 생성 도구 메타데이터 문자열",
            "Google 계열 도구를 식별하는 메타데이터 문자열입니다. SynthID 워터마크 검증은 아닙니다.",
            10,
        ))
    limitations.append("SynthID는 픽셀 도메인 워터마크이므로 바이트 스캔으로는 감지할 수 없습니다.")

    # Check other tool/watermark metadata strings
    watermark_record = _check_watermarks(data)
    if watermark_record:
        provenance_records.append(watermark_record)
        has_watermark = True
        signals.append(ForensicEvidenceSignal(
            "생성 도구 식별 문자열",
            f"{watermark_record.provider} 관련 메타데이터 문자열입니다(암호학적 워터마크 검증이 아닙니다).",
            10,
        ))

    # Check ExifTool JSON embedding
    exif_record = _check_exiftool_json(data)
    if exif_record:
        provenance_records.append(exif_record)
        signals.append(ForensicEvidenceSignal(
            "ExifTool JSON 임베딩",
            "ExifTool 형식의 메타데이터가 발견되었습니다.",
            10,
        ))

    # Check PNG chunks
    png_signals = _check_png_chunks(data)
    signals.extend(png_signals)

    # Check JPEG markers
    jpeg_signals = _check_jpeg_markers(data)
    signals.extend(jpeg_signals)

    # Limitations
    if not provenance_records and c2pa_status != C2PA_STATUS_UNAVAILABLE:
        limitations.append("출처 표준 메타데이터가 발견되지 않았습니다.")
    limitations.append("로컬 포렌식 분석 결과이며, 공식 검증이 필요합니다.")

    score = min(100, sum(signal.weight for signal in signals))

    return MetadataForensicAnalysis(
        score=score,
        reference_band=REFERENCE_BAND,
        reference_note=(
            f"출처 기록 {len(provenance_records)}건, 출처 신호 {len(signals)}개 "
            f"({c2pa_unreadable_label(c2pa_error) if c2pa_status == C2PA_STATUS_UNAVAILABLE else 'C2PA ' + ('있음' if has_c2pa else '없음')}, SynthID {'있음' if has_synthid else '없음'}, "
            f"워터마크 표식 {'있음' if has_watermark else '없음'}) — 신호 가중치 합 {score}/100은 미측정 참고값입니다."
        ),
        signals=signals,
        limitations=limitations,
        provenance_records=provenance_records,
        has_c2pa=has_c2pa,
        has_synthid=has_synthid,
        has_watermark=has_watermark,
        c2pa_status=c2pa_status,
        c2pa_error=c2pa_error,
    )


def _error_analysis(message: str) -> MetadataForensicAnalysis:
    return MetadataForensicAnalysis(
        score=0,
        reference_band=UNAVAILABLE_BAND,
        reference_note=message,
        signals=[],
        limitations=[message],
        provenance_records=[],
        has_c2pa=False,
        has_synthid=False,
        has_watermark=False,
    )


# --- Offline reader settings and the bundled trust list (techniques-2026 item 1) ---

# The trust anchors the validator checks a signer against. Before this the
# reader ran with c2pa-rs defaults — no trust anchors — so no signer was ever
# "signingCredential.trusted" and both strong C2PA rules (evidence_rules) were
# unreachable. The bundle is the C2PA conformance trust list snapshot
# described by models/c2pa-trust.json (source URL, upstream commit, list
# issue date, retrieval date, sha256); its bytes are checked against that
# sha256 before every use — a changed file fails the check (fail closed).
TRUST_BUNDLE_MANIFEST = Path(__file__).resolve().parent / "models" / "c2pa-trust.json"
# P0R-21: the frozen Interim Trust List (ITL) — a second, separate bundle. A signer is checked against the official
# list (+ operator anchors) first; only an untrusted signer is checked again against the ITL alone, and an ITL-only
# chain is recorded as such (trust_list "itl"), never as the current list.
ITL_BUNDLE_MANIFEST = Path(__file__).resolve().parent / "models" / "c2pa-itl.json"
# No revocation data is bundled: CRL/OCSP are not checked offline (OCSP fetching is off below).
C2PA_REVOCATION_NOTE = "인증서 폐기 여부는 확인하지 않음(오프라인 폐기 목록 미적용·OCSP 조회 안 함 — 폐기된 서명 인증서도 신뢰로 보일 수 있음)"
# Operator-added anchors (PEM file): a deployment's own CA, or a test CA.
TRUST_ANCHORS_ENV = "DEEPFAKE_LENS_C2PA_TRUST_ANCHORS"
# No network at analysis time: c2pa-rs would otherwise fetch a remote
# manifest named by the asset and OCSP responses for the signer.
OFFLINE_VERIFY_SETTINGS = {"remote_manifest_fetch": False, "ocsp_fetch": False, "verify_trust": True}


class C2paTrustError(RuntimeError):
    """The trust bundle or the operator's anchors cannot be used (Korean message) — the check fails."""


def _trust_bundle(manifest: Path | None = None) -> tuple[dict[str, object], str]:
    import hashlib
    import json

    manifest = manifest or TRUST_BUNDLE_MANIFEST  # looked up at call time (tests patch the module attribute)

    try:
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        pem_path = manifest.parent / str(meta["file"])
        data = pem_path.read_bytes()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise C2paTrustError(f"C2PA 신뢰 목록을 읽을 수 없습니다: {exception_text(exc)}") from exc
    if hashlib.sha256(data).hexdigest() != str(meta.get("sha256", "")):
        raise C2paTrustError(f"C2PA 신뢰 목록 무결성 불일치(models/{manifest.name}의 sha256과 다름)")
    return meta, data.decode("ascii", errors="strict")


def _operator_anchors() -> tuple[str, str] | None:
    """``(pem text, sha256)`` of the operator's anchors file, None when the variable is unset."""
    import hashlib
    import os

    value = os.environ.get(TRUST_ANCHORS_ENV, "").strip()
    if not value:
        return None
    try:
        data = Path(value).expanduser().read_bytes()
        text = data.decode("ascii")
    except (OSError, UnicodeError) as exc:
        raise C2paTrustError(f"{TRUST_ANCHORS_ENV}의 인증서 파일을 읽을 수 없습니다: {exception_text(exc)}") from exc
    if "-----BEGIN CERTIFICATE-----" not in text:
        raise C2paTrustError(f"{TRUST_ANCHORS_ENV}의 파일에 PEM 인증서가 없습니다")
    return text, hashlib.sha256(data).hexdigest()


def trust_basis() -> dict[str, object]:
    """What the signer is checked against — recorded with every validation (and in the cache key)."""
    meta, _ = _trust_bundle()
    itl, _ = _trust_bundle(ITL_BUNDLE_MANIFEST)
    operator = _operator_anchors()
    return {
        "id": meta.get("id"),
        "list_issue_date": meta.get("list_issue_date"),
        "retrieved": meta.get("retrieved"),
        "sha256": meta.get("sha256"),
        "operator_anchors_sha256": operator[1] if operator else None,
        "itl_id": itl.get("id"),
        "itl_sha256": itl.get("sha256"),
        "revocation": "not_checked",
    }


def trust_basis_text(basis: object) -> str:
    """"C2PA 공식 신뢰 목록 2026-08-05판(sha256 75cacc98b79e…)[ + 운영자 지정 앵커(sha256 …)]"."""
    if not isinstance(basis, dict) or not basis.get("id"):
        return "신뢰 목록 정보 없음"
    text = f"{basis.get('id')} {basis.get('list_issue_date')}판(sha256 {str(basis.get('sha256'))[:12]}…)"
    if basis.get("operator_anchors_sha256"):
        text += f" + 운영자 지정 앵커({TRUST_ANCHORS_ENV}, sha256 {str(basis['operator_anchors_sha256'])[:12]}…)"
    if basis.get("itl_sha256"):
        text += f"; 동결된 임시 신뢰 목록 ITL(sha256 {str(basis['itl_sha256'])[:12]}…)은 별도 확인"
    if basis.get("revocation") == "not_checked":
        text += f"; {C2PA_REVOCATION_NOTE}"
    return text


def trust_cache_tokens() -> list[str]:
    """The trust basis as scan-cache tokens (a changed list or operator anchor file re-analyzes); never raises."""
    try:
        basis = trust_basis()
    except C2paTrustError as exc:
        return [f"c2pa-trust:error:{str(exc)[:60]}"]
    return [f"c2pa-trust:{basis.get('id')}:{basis.get('sha256')}:operator:{basis.get('operator_anchors_sha256')}:itl:{basis.get('itl_sha256')}"]


def reader_settings(*, itl: bool = False) -> dict[str, object]:
    """The c2pa-python Settings dict: offline verification + bundled (and operator) trust anchors.

    ``itl`` (P0R-21): the frozen Interim Trust List's anchors alone, for the second check of an untrusted signer.
    """
    if itl:
        _, itl_pem = _trust_bundle(ITL_BUNDLE_MANIFEST)
        return {"verify": dict(OFFLINE_VERIFY_SETTINGS), "trust": {"trust_anchors": itl_pem}}
    _, pem = _trust_bundle()
    trust: dict[str, object] = {"trust_anchors": pem}
    operator = _operator_anchors()
    if operator is not None:
        trust["user_anchors"] = operator[0]
    return {"verify": dict(OFFLINE_VERIFY_SETTINGS), "trust": trust}


def validate_c2pa_manifest(path: Path | str) -> dict[str, object] | None:
    """Validate a C2PA manifest with the official c2pa-python SDK.

    Returns None only when the SDK is not installed (cannot validate).
    Otherwise always carries a ``status`` in
    ``{"valid", "invalid", "absent", "unavailable"}``:

    - ``valid`` / ``invalid``: the SDK read a manifest and completed
      validation. Non-``"valid"`` states usually mean the signer is not in
      the trust store rather than proof of tampering.
    - ``absent``: the SDK reported that the file holds no manifest
      (``C2paError.ManifestNotFound`` — "no JUMBF data found").
    - ``unavailable``: the reader raised anything else (I/O error, corrupt
      or truncated JUMBF, verify error, unsupported container — D8), or a
      manifest was opened but validation itself failed. Never collapsed
      into "absent": whether a manifest exists is unknown, and a
      corrupt-but-present manifest is forensically meaningful. ``error``
      carries ``<ExceptionClass>: <message>``; ``error_kind`` is
      ``"not_supported"`` when the SDK does not handle the container,
      ``"sdk_too_old"`` when the SDK has no Context/Settings API (offline
      settings and trust anchors cannot be applied) and ``"trust"`` when
      the trust bundle cannot be used.

    ``present`` remains as the boolean shorthand for "a manifest was read".

    techniques-2026: the reader runs offline (no remote manifest, no OCSP)
    against the bundled C2PA trust list (+ ``DEEPFAKE_LENS_C2PA_TRUST_ANCHORS``);
    the result also carries the validator's ``spec_version``, the
    ``trust_basis``, and the C2PA 2.x facts of :func:`manifest_facts`.
    """
    try:
        import c2pa
    except ImportError:
        return None
    if not hasattr(c2pa, "Context") or not hasattr(c2pa, "Settings"):
        return {"present": False, "status": "unavailable", "error_kind": "sdk_too_old",
                "error": "c2pa-python 0.38 미만 — 오프라인 검증 설정과 신뢰 목록을 적용할 수 없음"}
    try:
        settings = reader_settings()
        basis = trust_basis()
    except C2paTrustError as exc:
        return {"present": False, "status": "unavailable", "error_kind": "trust", "error": str(exc)}
    # R12-1: the SDK is native — it only ever sees an ASCII staged name.
    with native_safe_path(path) as native_media:
        try:
            context = c2pa.Context(c2pa.Settings.from_dict(settings))
            reader = c2pa.Reader(native_media, context=context)
        except Exception as exc:  # noqa: BLE001 - SDK error classes vary by release; classified below
            if _is_c2pa_error(c2pa, exc, "ManifestNotFound"):
                return {"present": False, "status": "absent", "error": exception_text(exc), "trust_basis": basis}
            return {
                "present": False,
                "status": "unavailable",
                "error": failure_reason(exc),
                "error_kind": "not_supported" if _is_c2pa_error(c2pa, exc, "NotSupported") else "reader_error",
            }
        try:
            import json

            state = str(reader.get_validation_state())
            manifest = reader.get_active_manifest() or {}
            results = reader.get_validation_results() or {}
            # P0R-20: the public json() omits the hard-binding assertion; its
            # exclusion ranges are only in detailed_json()'s assertion_store.
            try:
                detailed = json.loads(reader.detailed_json())
            except Exception:  # noqa: BLE001 - detailed_json is best-effort; absence is audited as a parse anomaly below
                detailed = {}
        except Exception as exc:
            return {"present": True, "status": "unavailable", "error": failure_reason(exc), "error_kind": "validation_error"}
        finally:
            try:
                reader.close()
            except Exception:
                pass
        # P0R-20: audit the hard-binding exclusions while the (possibly staged) file still exists — by chunk /
        # segment headers, never reading the whole file.
        binding = _audit_hard_binding(detailed, str(native_media))
        itl_trusted = binding.ok and _untrusted(results) and _itl_trusted(c2pa, native_media)

    success_codes: list[str] = []
    failure_codes: list[str] = []
    if isinstance(results, dict):
        for section in results.values():
            if not isinstance(section, dict):
                continue
            for key, sink in (("success", success_codes), ("failure", failure_codes)):
                for item in section.get(key, []) or []:
                    code = item.get("code") if isinstance(item, dict) else None
                    if code:
                        sink.append(str(code))
    signature = manifest.get("signature_info") if isinstance(manifest, dict) else None
    # The SDK reports "Valid"/"Trusted"/"Invalid" (capitalized); compare
    # case-insensitively — "Trusted" is a valid manifest with a trusted
    # signer in newer SDK releases.
    valid_state = state.lower() in {"valid", "trusted"}
    facts = manifest_facts(manifest if isinstance(manifest, dict) else {})
    # P0R-20: audit the active manifest's hard-binding (content hash) exclusions.
    # A valid signature whose exclusions empty the asset is still untrustworthy
    # — fail closed: force status "invalid" / error_kind "hash_exclusion" and
    # add a failure code so no "hash match" success remains.
    if not binding.ok:
        valid_state = False
        if "c2pa.hash.exclusion.invalid" not in failure_codes:
            failure_codes.append("c2pa.hash.exclusion.invalid")
    return {
        "present": True,
        "status": "valid" if valid_state else "invalid",
        "state": state,
        "hard_binding": binding.to_json(),
        "error_kind": "hash_exclusion" if not binding.ok else None,
        "trusted": binding.ok and "signingCredential.trusted" in success_codes
        and "signingCredential.trusted" not in failure_codes,
        # P0R-21: which list the signer chained to — the official C2PA Trust List (or an operator anchor), the
        # frozen Interim Trust List only, or neither.
        "trust_list": _trust_list_label(binding.ok and "signingCredential.trusted" in success_codes
                                        and "signingCredential.trusted" not in failure_codes, itl_trusted),
        "signature": dict(signature) if isinstance(signature, dict) else {},
        "success_codes": success_codes,
        "failure_codes": failure_codes,
        "title": manifest.get("title") if isinstance(manifest, dict) else None,
        "claim_generator": manifest.get("claim_generator") if isinstance(manifest, dict) else None,
        # IPTC digitalSourceType values declared by the manifest's actions
        # (e.g. trainedAlgorithmicMedia, digitalCapture) — the field that
        # distinguishes a generator's manifest from a camera's. Short codes
        # (last path segment) of the active manifest's own actions; the
        # full IRIs with where they were declared are in "source_type_declarations".
        "digital_source_types": sorted({str(item["value"]).rsplit("/", 1)[-1] for item in facts["source_type_declarations"] if item["where"] == "action"}),
        # techniques-2026: the validator's C2PA specification version (C2PA 2.3+
        # validation-results "specVersion") and what the signer was checked against.
        "spec_version": str(results.get("specVersion") or "") if isinstance(results, dict) else "",
        "trust_list_uri": str(results.get("trustListUri") or "") if isinstance(results, dict) else "",
        "trust_basis": basis,
        "sdk_version": _sdk_version(c2pa),
        **facts,
    }


# Bounds on what a manifest may contribute to a result row (it is untrusted input).
FACT_MAX_ITEMS = 16
FACT_MAX_TEXT = 200
WATERMARK_ACTIONS = ("c2pa.watermarked", "c2pa.watermarked.bound", "c2pa.watermarked.unbound")


def _sdk_version(sdk: object) -> str:
    try:
        # snake_case identifiers: the rendered reports keep identifiers, not English word runs.
        return f"c2pa_python {getattr(sdk, 'version')('c2pa-python')}, c2pa_rs {getattr(sdk, 'sdk_version')()}"
    except Exception:  # noqa: BLE001 - informational only
        return ""


def _text(value: object) -> str:
    return str(value)[:FACT_MAX_TEXT] if value is not None else ""


def _binding_value(value: object) -> str:
    """A soft-binding block value as text: a string verbatim, bytes / an int list as hex."""
    if isinstance(value, str):
        return value[:FACT_MAX_TEXT]
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()[:FACT_MAX_TEXT]
    if isinstance(value, list) and all(isinstance(item, int) and 0 <= item < 256 for item in value):
        return bytes(value).hex()[:FACT_MAX_TEXT]
    return _text(value)


def _actions(manifest: dict[str, object]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for assertion in manifest.get("assertions") or []:
        if isinstance(assertion, dict) and str(assertion.get("label", "")).startswith("c2pa.actions"):
            data = assertion.get("data")
            for action in (data.get("actions") if isinstance(data, dict) else None) or []:
                if isinstance(action, dict):
                    out.append(action)
    return out


def manifest_facts(manifest: dict[str, object]) -> dict[str, object]:
    """The C2PA 2.2–2.4 facts of an active manifest (techniques-2026 item 1).

    - ``source_type_declarations``: every ``digitalSourceType`` with where it
      was declared — ``action`` (the active manifest's own actions — the one
      that describes this asset) or ``ingredient`` (C2PA 2.4 allows it on an
      ingredient without a manifest — describes an input, not this asset);
    - ``ai_disclosures``: ``c2pa.ai-disclosure`` assertions (2.4) — modelType,
      modelName, modelIdentifier, contentProfile.humanOversightLevel;
    - ``soft_bindings``: ``c2pa.soft-binding`` assertions — ``alg`` and the
      block values (text; bytes as hex);
    - ``watermark_actions``: ``c2pa.watermarked`` / ``.bound`` / ``.unbound``;
    - ``embedded_data``: ``c2pa.embedded-data*`` assertions (2.2) — label and format;
    - ``claim_spec_version`` (2.4: ``claim_generator_info[].specVersion``;
      older: the claim-level ``specVersion``) and ``claim_version``.

    Everything is bounded (:data:`FACT_MAX_ITEMS`, :data:`FACT_MAX_TEXT`).
    """
    declarations: list[dict[str, str]] = []
    watermark_actions: list[str] = []
    for action in _actions(manifest):
        name = _text(action.get("action"))
        if name in WATERMARK_ACTIONS and name not in watermark_actions:
            watermark_actions.append(name)
        if isinstance(action.get("digitalSourceType"), str):
            declarations.append({"value": _text(action["digitalSourceType"]), "where": "action", "action": name})
    for ingredient in manifest.get("ingredients") or []:
        if isinstance(ingredient, dict) and isinstance(ingredient.get("digitalSourceType"), str):
            declarations.append({
                "value": _text(ingredient["digitalSourceType"]), "where": "ingredient",
                "action": _text(ingredient.get("relationship")), "title": _text(ingredient.get("title")),
            })
    disclosures: list[dict[str, str]] = []
    bindings: list[dict[str, object]] = []
    embedded: list[dict[str, str]] = []
    labels: list[str] = []
    for assertion in manifest.get("assertions") or []:
        if not isinstance(assertion, dict):
            continue
        label = _text(assertion.get("label"))
        labels.append(label)
        data = assertion.get("data") if isinstance(assertion.get("data"), dict) else {}
        assert isinstance(data, dict)
        if label.startswith("c2pa.ai-disclosure"):
            profile = data.get("contentProfile") if isinstance(data.get("contentProfile"), dict) else {}
            assert isinstance(profile, dict)
            disclosures.append({
                "modelType": _text(data.get("modelType")), "modelName": _text(data.get("modelName")),
                "modelIdentifier": _text(data.get("modelIdentifier")),
                "humanOversightLevel": _text(profile.get("humanOversightLevel")),
            })
        elif label.startswith("c2pa.soft-binding"):
            values = [_binding_value(block.get("value")) for block in (data.get("blocks") or []) if isinstance(block, dict)]
            bindings.append({"alg": _text(data.get("alg")), "values": values[:FACT_MAX_ITEMS]})
        elif label.startswith("c2pa.embedded-data"):
            embedded.append({"label": label, "format": _text(data.get("dc:format") or data.get("format") or assertion.get("format"))})
    claim_spec = ""
    for info in manifest.get("claim_generator_info") or []:
        if isinstance(info, dict) and info.get("specVersion"):
            claim_spec = _text(info["specVersion"])
            break
    return {
        "source_type_declarations": declarations[:FACT_MAX_ITEMS],
        "ai_disclosures": disclosures[:FACT_MAX_ITEMS],
        "soft_bindings": bindings[:FACT_MAX_ITEMS],
        "watermark_actions": watermark_actions,
        "embedded_data": embedded[:FACT_MAX_ITEMS],
        "assertion_labels": sorted(set(labels))[: FACT_MAX_ITEMS * 2],
        "claim_spec_version": claim_spec or _text(manifest.get("specVersion") or ""),
        "claim_version": _text(manifest.get("claim_version") or ""),
    }


def _untrusted(results: object) -> bool:
    """No ``signingCredential.trusted`` success code in the SDK's validation results."""
    if not isinstance(results, dict):
        return True
    for section in results.values():
        if isinstance(section, dict):
            for item in section.get("success", []) or []:
                if isinstance(item, dict) and item.get("code") == "signingCredential.trusted":
                    return False
    return True


def _itl_trusted(sdk: object, native_media: object) -> bool:
    """P0R-21: the signer chains to the frozen Interim Trust List (checked against its anchors alone)."""
    try:
        context = sdk.Context(sdk.Settings.from_dict(reader_settings(itl=True)))  # type: ignore[attr-defined]
        reader = sdk.Reader(native_media, context=context)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - the first validation already recorded the manifest; this only labels its trust
        return False
    try:
        return not _untrusted(reader.get_validation_results() or {})
    except Exception:  # noqa: BLE001 - as above
        return False
    finally:
        try:
            reader.close()
        except Exception:  # noqa: BLE001, S110 - closing a reader that failed is best effort
            pass


def _trust_list_label(trusted: bool, itl_trusted: bool) -> str | None:
    import os

    if trusted:
        return "c2pa_or_operator" if os.environ.get(TRUST_ANCHORS_ENV, "").strip() else "c2pa"
    return "itl" if itl_trusted else None


def _active_assertion_store(detailed: object) -> dict[str, object]:
    """The active manifest's ``assertion_store`` (label -> data) from ``detailed_json`` output."""
    if not isinstance(detailed, dict):
        return {}
    manifests = detailed.get("manifests")
    active = detailed.get("active_manifest")
    if not isinstance(manifests, dict):
        return {}
    manifest = manifests.get(active) if active in manifests else None
    if manifest is None and manifests:
        manifest = next(iter(manifests.values()))
    store = manifest.get("assertion_store") if isinstance(manifest, dict) else None
    return store if isinstance(store, dict) else {}


def _audit_hard_binding(detailed: object, media_path: str) -> "HardBindingVerdict":
    """P0R-20: audit the active manifest's hard-binding (content hash) exclusions."""
    from .c2pa_binding import audit_assertion_store

    return audit_assertion_store(_active_assertion_store(detailed), media_path)


def _is_c2pa_error(sdk: object, exc: BaseException, kind: str) -> bool:
    """True when ``exc`` is the SDK's ``C2paError.<kind>`` (class attribute
    in c2pa-python >= 0.10) or, for older releases, its message starts
    with ``"<kind>:"``."""
    base = getattr(sdk, "C2paError", None)
    cls = getattr(base, kind, None) if base is not None else None
    if isinstance(cls, type) and isinstance(exc, cls):
        return True
    return str(exc).startswith(f"{kind}:") or type(exc).__name__.endswith(kind)


def _digital_source_types(node: object, depth: int = 0) -> set[str]:
    """Collect every ``digitalSourceType`` value in a manifest (bounded walk)."""
    if depth > 12:
        return set()
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "digitalSourceType" and isinstance(value, str):
                found.add(value.rsplit("/", 1)[-1])
            else:
                found |= _digital_source_types(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            found |= _digital_source_types(value, depth + 1)
    return found


def _check_c2pa(data: bytes) -> ProvenanceRecord | None:
    """Check for C2PA manifest in file."""
    # C2PA manifest is typically in a JUMBF box
    # Look for C2PA signature box or CAI markers
    c2pa_markers = [b"c2pa", b"jumbf", b"Content Credentials", b"cai manifest"]
    
    for marker in c2pa_markers:
        if marker in data:
            idx = data.find(marker)
            # Try to find claim URL
            claim_url = None
            search_region = data[max(0, idx-200):min(len(data), idx+500)]
            
            # Look for HTTP URLs
            if b"http" in search_region:
                url_start = search_region.find(b"http")
                url_region = search_region[url_start:url_start+200]
                # Find URL end (null byte, space, or quote)
                for end_char in [b"\x00", b" ", b'"', b"'", b">", b"<"]:
                    url_end = url_region.find(end_char)
                    if url_end > 0:
                        claim_url = url_region[:url_end].decode("utf-8", errors="ignore")
                        break
            
            # Look for signature information
            signed = b"signature" in search_region.lower() or b"signed" in search_region.lower()
            
            return ProvenanceRecord(
                standard="C2PA",
                provider="CAI",
                signed=signed,
                claim_url=claim_url,
                details={"marker": marker.decode("utf-8", errors="ignore"), "position": str(idx)},
            )
    
    return None


def _check_google_tool_metadata(data: bytes) -> ProvenanceRecord | None:
    """Find Google tool-identifying metadata strings.

    These are attribution hints only. SynthID itself is embedded in pixels
    and is invisible to byte scanning, so this must never be reported as
    SynthID watermark detection.
    """
    google_markers = [
        b"Google", b"SynthID", b"GenerativeAI", b"AI.Generated",
        b"google.com/synthid", b"deepmind", b"gemini",
    ]

    for marker in google_markers:
        if marker in data:
            idx = data.find(marker)
            context = data[max(0, idx-50):min(len(data), idx+100)]

            try:
                context_str = context.decode("utf-8", errors="ignore")
                printable_ratio = sum(1 for c in context_str if c.isprintable()) / max(1, len(context_str))
                if printable_ratio > 0.5:
                    return ProvenanceRecord(
                        standard="ToolMetadata",
                        provider="Google",
                        signed=False,
                        claim_url=None,
                        details={"marker": marker.decode("utf-8", errors="ignore"), "position": str(idx)},
                    )
            except Exception:
                continue

    return None


def _check_watermarks(data: bytes) -> ProvenanceRecord | None:
    """Check for other watermarks."""
    watermark_markers = {
        b"Adobe Firefly": "Adobe",
        b"Content Credentials": "CAI",
        b"Stability AI": "Stability",
        b"Midjourney": "Midjourney",
        b"Microsoft MAI": "Microsoft",
        b"Getty Generative": "Getty",
        b"Luma Uni": "Luma",
        b"Krea AI": "Krea",
        b"Gamma Imagine": "Gamma",
        b"Monica AI": "Monica",
        b"Recraft": "Recraft",
        b"Ideogram": "Ideogram",
        b"Leonardo": "Leonardo",
    }

    for marker, provider in watermark_markers.items():
        if marker in data:
            return ProvenanceRecord(
                standard="ToolMetadata",
                provider=provider,
                signed=False,
                claim_url=None,
                details={"marker": marker.decode("utf-8", errors="ignore")},
            )

    return None


def _check_exiftool_json(data: bytes) -> ProvenanceRecord | None:
    """Check for ExifTool JSON embedding."""
    # Look for ExifTool JSON in JPEG COM marker
    if data[:2] == b"\xff\xd8":  # JPEG
        offset = 2
        while offset < len(data) - 1:
            if data[offset] != 0xFF:
                break
            marker = data[offset + 1]
            if marker == 0xFE:  # COM marker
                length = struct.unpack(">H", data[offset+2:offset+4])[0]
                com_data = data[offset+4:offset+2+length]
                if b"ExifTool" in com_data or b"{" in com_data:
                    return ProvenanceRecord(
                        standard="ExifTool",
                        provider="ExifTool",
                        signed=False,
                        claim_url=None,
                        details={"marker": "JPEG COM"},
                    )
                offset += 2 + length
            elif marker in (0xD8, 0xD9):
                break
            else:
                length = struct.unpack(">H", data[offset+2:offset+4])[0]
                offset += 2 + length

    return None


def _check_png_chunks(data: bytes) -> list[ForensicEvidenceSignal]:
    """Check PNG chunks for provenance signals."""
    signals = []
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return signals

    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset+4])[0]
        chunk_type = data[offset+4:offset+8]

        # Check for text chunks with provenance info
        if chunk_type in (b"tEXt", b"iTXt", b"zTXt"):
            chunk_data = data[offset+8:offset+8+length]
            if b"Author" in chunk_data or b"Copyright" in chunk_data:
                signals.append(ForensicEvidenceSignal(
                    "PNG 출처 청크",
                    f"PNG {chunk_type.decode()} 청크에 저작권/저자 정보가 있습니다.",
                    8,
                ))

        offset += 12 + length
        if chunk_type == b"IEND":
            break

    return signals


def _check_jpeg_markers(data: bytes) -> list[ForensicEvidenceSignal]:
    """Check JPEG markers for provenance signals."""
    signals = []
    if data[:2] != b"\xff\xd8":
        return signals

    offset = 2
    while offset < len(data) - 1:
        if data[offset] != 0xFF:
            break
        marker = data[offset + 1]

        if marker == 0xE1:  # APP1 (EXIF)
            length = struct.unpack(">H", data[offset+2:offset+4])[0]
            app_data = data[offset+4:offset+2+length]
            if b"Exif" in app_data:
                signals.append(ForensicEvidenceSignal(
                    "EXIF 메타데이터",
                    "EXIF 메타데이터가 포함되어 있습니다.",
                    5,
                ))
            offset += 2 + length
        elif marker in (0xD8, 0xD9):
            break
        else:
            if offset + 3 < len(data):
                length = struct.unpack(">H", data[offset+2:offset+4])[0]
                offset += 2 + length
            else:
                break

    return signals

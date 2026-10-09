"""Validation of the rows posted to ``/api/report`` (N11).

The web GUI posts back the ``items`` rows a scan or upload returned. Before
round 6 the report endpoint rebuilt ``ScanItem`` objects from whatever it got
— ``{"items": [{"path": 3}]}`` rendered an HTML report about "3" with HTTP
200. Every row is now checked against the scan-result item contract
(``contracts/deepfake-lens-scan-result-v2.schema.json``, ``$defs.item`` and
the ``result`` / ``evidence_item`` / ``coverage_entry`` / ``signal`` objects
it references) and a row that does not fit is refused with a Korean reason
(HTTP 400).

The package has no runtime dependencies, so this is a small stdlib
validator of exactly the constraints the schema states (required fields,
types, enums, the sha256 pattern, a reason for skipped/failed coverage).
``tests/test_servers.py`` checks the tuples below against the schema file
so the two cannot drift apart.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# contracts/deepfake-lens-scan-result-v2.schema.json $defs.item.required
ITEM_REQUIRED = ("path", "name", "kind", "status", "size_bytes", "result")
# $defs.result.required
RESULT_REQUIRED = (
    "verdict_code", "verdict_label", "grade", "grade_label", "evidence", "coverage",
    "probability", "probability_ci", "score_is_calibrated", "reference_signals",
    "score", "band", "band_label", "verdict", "signals", "limitations",
    "source_guess", "next_checks",
)
# $defs.evidence_item.required
EVIDENCE_REQUIRED = (
    "title", "detail", "kind", "direction", "strength", "layer", "probability",
    "probability_ci", "calibration_id", "measured_on", "raw_score",
)
# $defs.coverage_entry.required / $defs.signal.required
COVERAGE_REQUIRED = ("check", "status", "reason")
SIGNAL_REQUIRED = ("title", "detail", "weight")

# Enums as the schema lists them.
VERDICT_CODES = ("manipulation_evidence", "authenticity_evidence", "undetermined")
VERDICT_LABELS = ("조작·생성 근거 있음", "원본성 근거 있음", "판단 불가")
GRADES = ("evidence", "reference")
BANDS = ("high", "low", "unknown")
EVIDENCE_KINDS = ("deterministic", "statistical", "lexical")
EVIDENCE_DIRECTIONS = ("synthetic", "authentic", "neutral")
EVIDENCE_STRENGTHS = ("strong", "moderate", "weak")
COVERAGE_STATUSES = ("ran", "skipped", "failed")
# Item string fields (schema $defs.item.properties: "type": "string").
ITEM_STRING_FIELDS = ("path", "name", "kind", "status")
# Optional item fields that are a string or null when present.
ITEM_OPTIONAL_STRING_FIELDS = ("error", "duplicate_of")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_TYPE_LABELS = {
    str: "문자열",
    int: "정수",
    bool: "참/거짓 값",
    list: "배열",
    dict: "JSON 객체",
}


class ItemContractError(ValueError):
    """A posted row does not fit the item contract; ``str()`` is the Korean reason."""


def _codes(names: "list[str] | tuple[str, ...]") -> str:
    """Field names and enum values as backticked code (identifiers, not prose)."""
    return ", ".join(f"`{name}`" for name in names)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require(obj: Mapping[str, object], fields: tuple[str, ...], where: str) -> None:
    missing = [name for name in fields if name not in obj]
    if missing:
        raise ItemContractError(f"{where}에 필수 필드가 없습니다: {_codes(missing)}")


def _expect(value: object, kind: type, field: str) -> None:
    ok = _is_int(value) if kind is int else isinstance(value, kind)
    if not ok:
        raise ItemContractError(f"`{field}` 값은 {_TYPE_LABELS[kind]}이어야 합니다")


def _enum(value: object, allowed: tuple[str, ...], field: str) -> None:
    if value not in allowed:
        raise ItemContractError(f"`{field}` 값이 허용 목록({_codes(allowed)})에 없습니다")


def _number_or_null(value: object, field: str) -> None:
    if value is not None and not _is_number(value):
        raise ItemContractError(f"`{field}` 값은 숫자 또는 null이어야 합니다")


def _array_or_null(value: object, field: str) -> None:
    if value is not None and not isinstance(value, list):
        raise ItemContractError(f"`{field}` 값은 배열 또는 null이어야 합니다")


def _string_or_null(value: object, field: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ItemContractError(f"`{field}` 값은 문자열 또는 null이어야 합니다")


def _objects(value: object, field: str) -> list[Mapping[str, object]]:
    _expect(value, list, field)
    assert isinstance(value, list)
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise ItemContractError(f"`{field}[{index}]` 값은 JSON 객체여야 합니다")
    return value


def _check_evidence(entries: list[Mapping[str, object]]) -> None:
    for index, entry in enumerate(entries):
        where = f"evidence[{index}]"
        _require(entry, EVIDENCE_REQUIRED, where)
        _enum(entry["kind"], EVIDENCE_KINDS, f"{where}.kind")
        _enum(entry["direction"], EVIDENCE_DIRECTIONS, f"{where}.direction")
        _enum(entry["strength"], EVIDENCE_STRENGTHS, f"{where}.strength")
        _number_or_null(entry["probability"], f"{where}.probability")
        _array_or_null(entry["probability_ci"], f"{where}.probability_ci")
        _string_or_null(entry["calibration_id"], f"{where}.calibration_id")
        _string_or_null(entry["measured_on"], f"{where}.measured_on")
        if entry["raw_score"] is not None and not _is_int(entry["raw_score"]):
            raise ItemContractError(f"`{where}.raw_score` 값은 정수 또는 null이어야 합니다")


def _check_coverage(entries: list[Mapping[str, object]]) -> None:
    for index, entry in enumerate(entries):
        where = f"coverage[{index}]"
        _require(entry, COVERAGE_REQUIRED, where)
        _expect(entry["check"], str, f"{where}.check")
        _enum(entry["status"], COVERAGE_STATUSES, f"{where}.status")
        _expect(entry["reason"], str, f"{where}.reason")
        if entry["status"] in ("skipped", "failed") and not entry["reason"]:
            raise ItemContractError(f"`{where}.reason`은 미실행·실패 검사에 필수입니다")


def _check_signals(entries: list[Mapping[str, object]], field: str) -> None:
    for index, entry in enumerate(entries):
        _require(entry, SIGNAL_REQUIRED, f"{field}[{index}]")


def _check_result(result: Mapping[str, object]) -> None:
    _require(result, RESULT_REQUIRED, "result")
    _enum(result["verdict_code"], VERDICT_CODES, "result.verdict_code")
    _enum(result["verdict_label"], VERDICT_LABELS, "result.verdict_label")
    _enum(result["grade"], GRADES, "result.grade")
    _enum(result["band"], BANDS, "result.band")
    _number_or_null(result["probability"], "result.probability")
    _array_or_null(result["probability_ci"], "result.probability_ci")
    _expect(result["score_is_calibrated"], bool, "result.score_is_calibrated")
    _expect(result["score"], int, "result.score")
    for field in ("verdict", "grade_label", "band_label"):
        _expect(result[field], str, f"result.{field}")
    for field in ("limitations", "next_checks"):
        _expect(result[field], list, f"result.{field}")
    _expect(result["source_guess"], dict, "result.source_guess")
    _check_evidence(_objects(result["evidence"], "result.evidence"))
    _check_coverage(_objects(result["coverage"], "result.coverage"))
    _check_signals(_objects(result["reference_signals"], "result.reference_signals"), "result.reference_signals")
    _check_signals(_objects(result["signals"], "result.signals"), "result.signals")


def check_report_item(row: object) -> None:
    """Raise :class:`ItemContractError` unless ``row`` fits the scan-result item contract."""
    if not isinstance(row, dict):
        raise ItemContractError("항목은 JSON 객체여야 합니다")
    _require(row, ITEM_REQUIRED, "항목")
    for field in ITEM_STRING_FIELDS:
        _expect(row[field], str, field)
    if not row["path"]:
        raise ItemContractError("`path` 값이 비어 있습니다")
    _expect(row["size_bytes"], int, "size_bytes")
    size = row["size_bytes"]
    if isinstance(size, int) and size < 0:
        raise ItemContractError("`size_bytes` 값은 0 이상이어야 합니다")
    for field in ITEM_OPTIONAL_STRING_FIELDS:
        _string_or_null(row.get(field), field)
    sha = row.get("sha256")
    if sha is not None and not (isinstance(sha, str) and SHA256_PATTERN.match(sha)):
        raise ItemContractError("`sha256` 값은 64자리 소문자 16진수 또는 null이어야 합니다")
    result = row["result"]
    if result is not None:
        _expect(result, dict, "result")
        assert isinstance(result, dict)
        _check_result(result)

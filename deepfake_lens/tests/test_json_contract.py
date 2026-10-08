from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from deepfake_lens.core import (
    SCAN_JSON_SCHEMA_VERSION,
    SourceGuess,
    build_classification_result,
    scan_directory,
    scan_to_json,
    scan_to_json_text,
)
from deepfake_lens.result_types import (
    ClassificationResult,
    CoverageEntry,
    CoverageStatus,
    EvidenceDirection,
    EvidenceItem,
    EvidenceKind,
    EvidenceSignal,
    EvidenceStrength,
    Grade,
    Verdict,
)
from deepfake_lens.serialization import _classification_result_from_json

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULT_SCHEMA = REPO_ROOT / "contracts" / "deepfake-lens-scan-result-v2.schema.json"


class ScanJsonContractTest(unittest.TestCase):
    def test_scan_json_payload_carries_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text("As an AI language model, I can help.", encoding="utf-8")

            summary, items = scan_directory(root)
            payload = scan_to_json(summary, items)

            self.assertEqual(payload["schema_version"], SCAN_JSON_SCHEMA_VERSION)
            # G5/G6: contract v2 (verdict/evidence/coverage) bumped the version.
            self.assertEqual(payload["schema_version"], 2)
            self.assertIn("summary", payload)
            self.assertIn("items", payload)

    def test_scan_json_text_round_trips_with_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text("plain note", encoding="utf-8")

            summary, items = scan_directory(root)
            payload = json.loads(scan_to_json_text(summary, items))

            self.assertEqual(payload["schema_version"], SCAN_JSON_SCHEMA_VERSION)
            item = payload["items"][0]
            self.assertIn("status", item)
            self.assertIn("score", item["result"])
            self.assertIn("band", item["result"])
            self.assertIn("signals", item["result"])
            self.assertIn("limitations", item["result"])


def _full_result() -> ClassificationResult:
    return build_classification_result(
        subject="이미지",
        evidence=[
            EvidenceItem("C2PA 서명: 생성형 AI 출처 선언", "trainedAlgorithmicMedia", EvidenceKind.DETERMINISTIC,
                         EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG, "c2pa"),
            EvidenceItem("외부 모델 출력", "보정됨", EvidenceKind.STATISTICAL, EvidenceDirection.SYNTHETIC,
                         EvidenceStrength.MODERATE, "model", probability=0.91, probability_ci=(0.85, 0.95),
                         calibration_id="cal-aide-v1", measured_on="corpus-x@sha:abc/test", raw_score=91),
            EvidenceItem("AI 자기표현 문구", "x", EvidenceKind.LEXICAL, EvidenceDirection.SYNTHETIC,
                         EvidenceStrength.WEAK, "text"),
        ],
        coverage=[
            CoverageEntry("metadata", CoverageStatus.RAN),
            CoverageEntry("face_manipulation", CoverageStatus.SKIPPED, "얼굴 미검출"),
            CoverageEntry("model:aide", CoverageStatus.FAILED, "RuntimeError: boom"),
        ],
        source_guess=SourceGuess.unknown(),
        limitations=["l"],
        next_checks=["n"],
        reference_signals=[EvidenceSignal("픽셀 앙상블(참고, 미측정)", "d", 70)],
    )


class ResultContractV2Test(unittest.TestCase):
    """New result fields survive to_json -> JSON text -> from_json."""

    def test_round_trip_preserves_every_v2_field(self) -> None:
        result = _full_result()
        payload = json.loads(json.dumps(result.to_json(), ensure_ascii=False))
        restored = _classification_result_from_json(payload)
        self.assertEqual(restored.verdict_code, Verdict.MANIPULATION_EVIDENCE)
        self.assertEqual(restored.grade, Grade.EVIDENCE)
        self.assertEqual(restored.evidence, result.evidence)
        self.assertEqual(restored.coverage, result.coverage)
        self.assertEqual(restored.reference_signals, result.reference_signals)
        self.assertEqual(restored.probability, 0.91)
        self.assertEqual(restored.probability_ci, (0.85, 0.95))
        self.assertTrue(restored.score_is_calibrated)
        self.assertEqual(restored.score, 91)
        self.assertEqual(restored.band, result.band)
        self.assertEqual(restored.signals, result.signals)
        self.assertEqual(restored.to_json(), result.to_json())

    def test_json_shape_uses_enum_values(self) -> None:
        payload: dict[str, Any] = _full_result().to_json()
        self.assertEqual(payload["verdict_code"], "manipulation_evidence")
        self.assertEqual(payload["verdict_label"], "조작·생성 근거 있음")
        self.assertEqual(payload["grade"], "evidence")
        self.assertEqual(payload["band"], "high")
        self.assertEqual(payload["evidence"][0]["kind"], "deterministic")
        self.assertEqual(payload["evidence"][1]["probability_ci"], [0.85, 0.95])
        self.assertEqual(payload["coverage"][2], {"check": "model:aide", "status": "failed", "reason": "RuntimeError: boom"})

    def test_v1_record_loads_as_undetermined(self) -> None:
        legacy = {"score": 80, "band": "medium", "band_label": "주의", "verdict": "old", "signals": [], "limitations": [],
                  "source_guess": {"label": "x", "confidence": "unknown", "reasons": []}, "next_checks": []}
        restored = _classification_result_from_json(legacy)
        self.assertEqual(restored.verdict_code, Verdict.UNDETERMINED)
        self.assertEqual(restored.evidence, [])
        self.assertEqual(restored.coverage, [])
        self.assertEqual(restored.band.value, "medium")  # read-compat only

    def test_scan_payload_matches_vendored_result_schema(self) -> None:
        schema = json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text("As an AI language model, I can help.", encoding="utf-8")
            (root / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            payload = scan_to_json(*scan_directory(root))
        self.assertEqual(payload["schema_version"], schema["properties"]["schema_version"]["const"])
        _check_object(self, payload["summary"], schema["$defs"]["summary"], schema)
        result_schema = schema["$defs"]["result"]
        for item in payload["items"]:
            result = item["result"]
            if result is None:
                continue
            _check_object(self, result, result_schema, schema)
            self.assertNotEqual(result["band"], "medium")


def _resolve(schema: dict, root: dict) -> dict:
    ref = schema.get("$ref")
    if ref:
        return root["$defs"][ref.rsplit("/", 1)[-1]]
    return schema


def _check_object(test: unittest.TestCase, value: object, schema: dict, root: dict) -> None:
    """Minimal validator for the subset of JSON Schema the contract uses
    (required, enum, const, type object/array/number/null) — the stdlib-only
    CI job has no jsonschema package."""
    schema = _resolve(schema, root)
    if "enum" in schema:
        test.assertIn(value, schema["enum"])
    if "const" in schema:
        test.assertEqual(value, schema["const"])
    types = schema.get("type")
    if types is not None:
        allowed = types if isinstance(types, list) else [types]
        py: dict[str, type | tuple[type, ...]] = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None), "integer": int, "number": (int, float)}
        test.assertTrue(any(isinstance(value, py[t]) for t in allowed), f"{value!r} is not {allowed}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            test.assertIn(key, value)
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                _check_object(test, value[key], sub, root)
    if isinstance(value, list) and "items" in schema:
        for element in value:
            _check_object(test, element, schema["items"], root)


if __name__ == "__main__":
    unittest.main()

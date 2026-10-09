from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
import zipfile
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
# N3: real JSON Schema validation (draft 2020-12, as the schema declares)
# when the dev extra's jsonschema is installed; the CI extras and QA jobs
# install it, the stdlib-only job falls back to _check_object below.
HAVE_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None
HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None
HAVE_PIL = importlib.util.find_spec("PIL") is not None


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
        for item in payload["items"]:
            _check_object(self, item, schema["$defs"]["item"], schema)
        result_schema = schema["$defs"]["result"]
        for item in payload["items"]:
            result = item["result"]
            if result is None:
                continue
            _check_object(self, result, result_schema, schema)
            self.assertNotEqual(result["band"], "medium")


def write_contract_folder(folder: Path) -> Path:
    """Every row shape a scan emits: analyzed image/text, archive container
    and members, symlink (skipped), duplicate, unsupported and unreadable
    (failed check) files."""
    folder.mkdir(parents=True, exist_ok=True)
    a1111 = (REPO_ROOT / "fixtures" / "benchmark" / "a1111-metadata-marker.png").read_bytes()
    (folder / "a1111.png").write_bytes(a1111)
    (folder / "a1111-copy.png").write_bytes(a1111)
    (folder / "note.txt").write_text("As an AI language model, I can help.", encoding="utf-8")
    (folder / "data.xyz").write_bytes(b"opaque")
    (folder / "empty.jpg").write_bytes(b"")
    (folder / "fake.gif").write_text("not a gif", encoding="utf-8")
    with zipfile.ZipFile(folder / "bundle.zip", "w") as archive:
        archive.writestr("in/a1111.png", a1111)
        archive.writestr("../escape.txt", "traversal")
    if hasattr(os, "symlink"):
        try:
            (folder / "link.png").symlink_to("a1111.png")
        except OSError:
            pass
    return folder


def _schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))
    return loaded


def _validator() -> Any:
    import jsonschema

    schema = _schema()
    cls = jsonschema.validators.validator_for(schema)
    cls.check_schema(schema)
    return cls(schema)


def _errors(payload: object) -> list[str]:
    return [f"{'/'.join(str(p) for p in error.absolute_path)}: {error.message}" for error in _validator().iter_errors(payload)]


@unittest.skipUnless(HAVE_JSONSCHEMA, "jsonschema not installed (dev extra) — _check_object covers the stdlib job")
class ScanJsonSchemaValidationTest(unittest.TestCase):
    """N3: real scan outputs validate against the v2 schema under draft 2020-12."""

    def test_schema_declares_draft_2020_12_and_is_valid(self) -> None:
        import jsonschema

        self.assertIs(jsonschema.validators.validator_for(_schema()), jsonschema.Draft202012Validator)

    def test_null_result_rows_validate(self) -> None:
        """The draft-2020 bug: ``"type": [object, null]`` next to ``$ref`` rejected null."""
        payload = {
            "schema_version": 2,
            "summary": {"total": 1, "analyzed": 0, "unsupported_or_failed": 0, "manipulation_evidence": 0,
                        "authenticity_evidence": 0, "undetermined": 0, "checks_failed": 0},
            "items": [{"path": "a", "name": "a", "kind": "unknown", "status": "skipped", "size_bytes": 0, "result": None}],
        }
        self.assertEqual(_errors(payload), [])

    def test_validator_rejects_contract_violations(self) -> None:
        """Negative control: the validator is live (bad band, missing field, empty failed reason, legacy summary key)."""
        with tempfile.TemporaryDirectory() as tmp:
            payload = scan_to_json(*scan_directory(write_contract_folder(Path(tmp) / "case")))
        analyzed = next(item for item in payload["items"] if item["result"] is not None)
        cases: list[tuple[str, Any]] = []
        bad = json.loads(json.dumps(payload))
        next(i for i in bad["items"] if i["path"] == analyzed["path"])["result"]["band"] = "medium"
        cases.append(("medium band", bad))
        bad = json.loads(json.dumps(payload))
        del next(i for i in bad["items"] if i["path"] == analyzed["path"])["result"]["coverage"]
        cases.append(("missing coverage", bad))
        bad = json.loads(json.dumps(payload))
        next(i for i in bad["items"] if i["path"] == analyzed["path"])["result"]["coverage"].append({"check": "x", "status": "failed", "reason": ""})
        cases.append(("empty failed reason", bad))
        bad = json.loads(json.dumps(payload))
        bad["summary"]["high"] = 1
        cases.append(("legacy band count", bad))
        for label, document in cases:
            with self.subTest(case=label):
                self.assertTrue(_errors(document), label)

    @unittest.skipUnless(HAVE_PIL, "Pillow not installed")
    def test_real_scan_with_every_row_shape_validates(self) -> None:
        from deepfake_lens.analysis_api import AnalysisOptions, scan_folder, scan_payload
        from deepfake_lens.signing import sign_report

        with tempfile.TemporaryDirectory() as tmp:
            folder = write_contract_folder(Path(tmp) / "case")
            options = AnalysisOptions(dedupe=True)
            summary, items, thresholds = scan_folder(folder, options)
            payload = json.loads(json.dumps(scan_payload(summary, items, thresholds, options), ensure_ascii=False))
        statuses = {item["status"] for item in payload["items"]}
        kinds = {item["kind"] for item in payload["items"]}
        self.assertTrue({"analyzed", "expanded", "duplicate", "unsupported"} <= statuses, statuses)
        if hasattr(os, "symlink"):
            self.assertIn("skipped", statuses)
        self.assertIn("archive", kinds)
        self.assertTrue(any(item["result"] is None for item in payload["items"]))
        self.assertTrue(any(
            entry["status"] == "failed"
            for item in payload["items"] if item["result"] for entry in item["result"]["coverage"]
        ))
        self.assertEqual(_errors(payload), [])
        self.assertEqual(_errors(sign_report(payload, b"contract-test-key")), [])
        self.assertEqual(_errors(sign_report(payload, None)), [])

    @unittest.skipUnless(HAVE_FASTAPI and HAVE_PIL, "fastapi + httpx not installed — stream payload")
    def test_stream_result_payload_validates(self) -> None:
        from collections import OrderedDict
        from unittest import mock

        from fastapi.testclient import TestClient

        from deepfake_lens import webapp_api
        from deepfake_lens.api_server import create_app

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(webapp_api, "_READ_ROOTS", OrderedDict()):
            folder = write_contract_folder(Path(tmp).resolve() / "case")
            webapp_api.configure_read_roots(folder)
            client = TestClient(create_app(default_folder=folder))
            headers = {"host": "localhost", "X-Deepfake-Lens-Client": "contract"}
            with client.stream("POST", "/api/scan/stream", params={"directory": str(folder)}, headers=headers) as response:
                self.assertEqual(response.status_code, 200)
                body = "".join(response.iter_text())
            api = client.get("/api/scan", params={"folder": str(folder)}, headers=headers)
            self.assertEqual(api.status_code, 200, api.text)
        result_block = next(block for block in body.split("\n\n") if block.startswith("event: result"))
        stream = json.loads("".join(line[len("data: "):] for line in result_block.splitlines() if line.startswith("data: ")))
        self.assertTrue(any(item["result"] is None for item in stream["items"]))
        self.assertEqual(_errors(stream), [])
        self.assertEqual(_errors(api.json()), [])


def _resolve(schema: dict, root: dict) -> dict:
    ref = schema.get("$ref")
    if ref:
        return root["$defs"][ref.rsplit("/", 1)[-1]]
    return schema


def _check_object(test: unittest.TestCase, value: object, schema: dict, root: dict) -> None:
    """Minimal validator for the subset of JSON Schema the contract uses
    (required, enum, const, anyOf, type object/array/number/null) — the
    stdlib-only CI job has no jsonschema package; ScanJsonSchemaValidationTest
    runs the real draft 2020-12 validator where it is installed (N3)."""
    schema = _resolve(schema, root)
    if "anyOf" in schema:
        failures = []
        for branch in schema["anyOf"]:
            try:
                _check_object(test, value, branch, root)
            except AssertionError as exc:
                failures.append(str(exc))
            else:
                break
        else:
            test.fail(f"{value!r} matches no anyOf branch: {failures}")
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

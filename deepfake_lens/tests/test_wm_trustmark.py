"""techniques-2026 item 4: Adobe TrustMark decoding and the C2PA soft-binding cross-check.

The packet / rule cases need no runtime. The fixture cases need onnxruntime
and the pinned decoder files in the models folder (the ``tech`` gate image;
scripts/fetch_watermark_models.py) and use fixtures/trustmark
(scripts/build_trustmark_fixture.py).
"""

from __future__ import annotations

import importlib.util
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deepfake_lens.result_types import CoverageStatus, EvidenceDirection, EvidenceStrength, Verdict

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures" / "trustmark"
HAVE_ORT = importlib.util.find_spec("onnxruntime") is not None
HAVE_IMAGING = all(importlib.util.find_spec(name) is not None for name in ("numpy", "PIL"))


def _decoders_present() -> bool:
    if not (HAVE_ORT and HAVE_IMAGING):
        return False
    from deepfake_lens.wm_trustmark import available_variants

    return bool(available_variants()[0])


def _packet(payload: str, schema: int) -> str:
    """DataLayer.process_encode (binary mode) with the vendored BCH."""
    from deepfake_lens.vendor_trustmark_bch import BCH
    from deepfake_lens.wm_trustmark import BCH_POLYNOMIAL, SCHEMAS

    _, data_bits, t = SCHEMAS[schema]
    bch = BCH(t, BCH_POLYNOMIAL)
    data = payload[:data_bits]
    padded = data + "0" * ((8 - len(data) % 8) % 8)
    ecc = bch.encode(bytearray(int(padded[i:i + 8], 2) for i in range(0, len(padded), 8)))
    return data + "".join(format(b, "08b") for b in ecc)[:bch.get_ecc_bits()] + format(schema, "04b")


class _Reading:
    def __init__(self, variant: str, schema: int, payload: str) -> None:
        from deepfake_lens.wm_trustmark import SCHEMAS

        self.variant, self.schema, self.payload = variant, schema, payload
        self.schema_name = SCHEMAS[schema][0]

    @property
    def soft_binding_value(self) -> str:
        return f"{self.schema}*{self.payload}"


class TrustMarkPacketTest(unittest.TestCase):
    def test_every_schema_round_trips_with_correctable_errors(self) -> None:
        from deepfake_lens.wm_trustmark import SCHEMAS, decode_packet

        rng = random.Random(5)
        for schema, (_, data_bits, t) in SCHEMAS.items():
            with self.subTest(schema=schema):
                payload = "".join(rng.choice("01") for _ in range(data_bits))
                bits = list(_packet(payload, schema))
                for index in rng.sample(range(90), t):
                    bits[index] = "1" if bits[index] == "0" else "0"
                self.assertEqual(decode_packet("".join(bits)), (schema, payload))

    def test_malformed_packets_are_rejected(self) -> None:
        from deepfake_lens.wm_trustmark import decode_packet

        self.assertIsNone(decode_packet("01" * 10))
        self.assertIsNone(decode_packet("2" * 100))


def _validation(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "present": True, "status": "valid", "trusted": True, "failure_codes": [], "success_codes": ["assertion.dataHash.match"],
        "source_type_declarations": [{"value": "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia", "where": "action", "action": "c2pa.created"}],
        "soft_bindings": [{"alg": "com.adobe.trustmark.Q", "values": ["2*" + "01" * 34]}],
    }
    base.update(overrides)
    return base


class TrustMarkEvidenceRuleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reading = _Reading("Q", 2, "01" * 34)

    def test_reading_alone_is_a_neutral_fact_with_measured_fpr(self) -> None:
        from deepfake_lens.media_marks import trustmark_evidence

        [item] = trustmark_evidence([self.reading], None)
        self.assertEqual((item.direction, item.strength), (EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK))
        self.assertIn("AI 생성 사실이 아닙니다", item.detail)
        self.assertIn("240장", item.detail)

    def test_match_with_trusted_manifest_is_a_cross_validation_fact(self) -> None:
        from deepfake_lens.media_marks import TRUSTMARK_CROSS_MATCH_TITLE, trustmark_evidence

        items = trustmark_evidence([self.reading], _validation())
        titles = [i.title for i in items]
        self.assertIn(TRUSTMARK_CROSS_MATCH_TITLE, titles)
        self.assertFalse(any(i.direction == EvidenceDirection.SYNTHETIC for i in items))  # the C2PA item decides, not this one

    def test_match_after_hard_binding_change_links_the_signed_declaration(self) -> None:
        from deepfake_lens.media_marks import TRUSTMARK_CROSS_LINKED_TITLE, trustmark_evidence

        items = trustmark_evidence([self.reading], _validation(failure_codes=["assertion.dataHash.mismatch"], status="invalid"))
        [linked] = [i for i in items if i.title == TRUSTMARK_CROSS_LINKED_TITLE]
        self.assertEqual((linked.direction, linked.strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG))

    def test_untrusted_or_tampered_assertions_never_link(self) -> None:
        from deepfake_lens.media_marks import trustmark_evidence

        for overrides in ({"trusted": False, "failure_codes": ["signingCredential.untrusted", "assertion.dataHash.mismatch"]},
                          {"failure_codes": ["assertion.hashedURI.mismatch", "assertion.dataHash.mismatch"]}):
            with self.subTest(overrides=overrides):
                items = trustmark_evidence([self.reading], _validation(**overrides))
                self.assertFalse(any(i.direction == EvidenceDirection.SYNTHETIC for i in items))

    def test_capture_declaration_is_not_linked(self) -> None:
        from deepfake_lens.media_marks import trustmark_evidence

        capture = [{"value": "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture", "where": "action", "action": "c2pa.created"}]
        items = trustmark_evidence([self.reading], _validation(failure_codes=["assertion.dataHash.mismatch"], source_type_declarations=capture))
        self.assertFalse(any(i.direction != EvidenceDirection.NEUTRAL for i in items))

    def test_other_variant_or_value_is_a_mismatch_fact(self) -> None:
        from deepfake_lens.media_marks import trustmark_evidence

        for reading in (_Reading("P", 2, "01" * 34), _Reading("Q", 2, "10" * 34)):
            with self.subTest(variant=reading.variant):
                items = trustmark_evidence([reading], _validation())
                self.assertIn("TrustMark–C2PA 소프트 바인딩 불일치", [i.title for i in items])


class TrustMarkCoverageTest(unittest.TestCase):
    def test_without_onnxruntime_the_check_is_a_dependency_skip(self) -> None:
        from deepfake_lens.media_marks import MarkLayers, TRUSTMARK_CHECK, _trustmark_layer

        layers = MarkLayers()
        with mock.patch.dict(sys.modules, {"onnxruntime": None}):
            _trustmark_layer(REPO / "fixtures" / "benchmark" / "real-like-texture.png", None, layers)
        [entry] = layers.coverage
        self.assertEqual((entry.check, entry.status, entry.reason), (TRUSTMARK_CHECK, CoverageStatus.SKIPPED, "의존성 부재: onnxruntime"))

    @unittest.skipUnless(HAVE_ORT and HAVE_IMAGING, "onnxruntime, numpy and Pillow required")
    def test_without_weights_the_check_is_skipped_with_the_fetch_script(self) -> None:
        from deepfake_lens.media_marks import MarkLayers, _trustmark_layer

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"DEEPFAKE_LENS_MODELS_DIR": tmp}):
            layers = MarkLayers()
            _trustmark_layer(REPO / "fixtures" / "benchmark" / "real-like-texture.png", None, layers)
        [entry] = layers.coverage
        self.assertEqual(entry.status, CoverageStatus.SKIPPED)
        self.assertIn("가중치 없음: trustmark-decoder_Q.onnx", entry.reason)
        self.assertIn("fetch_watermark_models.py", entry.reason)

    @unittest.skipUnless(HAVE_ORT and HAVE_IMAGING, "onnxruntime, numpy and Pillow required")
    def test_a_decoder_with_other_bytes_is_refused(self) -> None:
        from deepfake_lens.media_marks import MarkLayers, _trustmark_layer

        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"DEEPFAKE_LENS_MODELS_DIR": tmp}):
            Path(tmp, "trustmark-decoder_Q.onnx").write_bytes(b"not the pinned decoder")
            image = Path(tmp) / "gray.png"
            Image.new("RGB", (256, 256), (120, 130, 140)).save(image)
            layers = MarkLayers()
            _trustmark_layer(image, None, layers)
        [entry] = layers.coverage
        self.assertEqual(entry.status, CoverageStatus.FAILED)
        self.assertIn("미고정 모델: trustmark-decoder_Q.onnx", entry.reason)


@unittest.skipUnless(_decoders_present() and (FIXTURES / "tm-q.png").is_file(), "onnxruntime + pinned TrustMark decoders + fixtures/trustmark required (tech image)")
class TrustMarkFixtureTest(unittest.TestCase):
    def _scan(self, name: str):
        from deepfake_lens.core import analyze_file

        with mock.patch.dict(os.environ, {"DEEPFAKE_LENS_C2PA_TRUST_ANCHORS": str(FIXTURES / "test-ca-cert.pem")}):
            item = analyze_file(FIXTURES / name)
        assert item.result is not None
        return item.result

    def test_plain_watermark_is_read_as_a_fact(self) -> None:
        result = self._scan("tm-q.png")
        [found] = [e for e in result.evidence if e.title == "TrustMark 워터마크 판독(변형 Q)"]
        self.assertIn("스키마 BCH_4", found.detail)
        self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)

    def test_signed_and_watermarked_cross_validates(self) -> None:
        result = self._scan("tm-q-signed.png")
        self.assertIn("TrustMark–C2PA 소프트 바인딩 교차검증 일치", [e.title for e in result.evidence])
        self.assertEqual(result.verdict_code, Verdict.MANIPULATION_EVIDENCE)

    def test_edited_after_signing_is_linked_through_the_soft_binding(self) -> None:
        result = self._scan("tm-q-signed-edited.png")
        titles = [e.title for e in result.evidence]
        self.assertIn("C2PA 무결성 불일치", titles)
        self.assertIn("C2PA 소프트 바인딩으로 확인된 생성형 AI 출처 선언", titles)
        self.assertEqual(result.verdict_code, Verdict.MANIPULATION_EVIDENCE)

    def test_other_id_is_a_mismatch(self) -> None:
        result = self._scan("tm-q-signed-other-id.png")
        self.assertIn("TrustMark–C2PA 소프트 바인딩 불일치", [e.title for e in result.evidence])


if __name__ == "__main__":
    unittest.main()

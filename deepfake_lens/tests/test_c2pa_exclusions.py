"""P0R-20 / P0R-21: the C2PA hard-binding exclusion attack (threat G3, disclosed 2026-10-02) and the trust lists.

A ``c2pa.hash.data`` assertion lists byte ranges left out of the signed content hash. c2pa-rs (up to 0.91.3; the
gate's c2pa-python 0.38) requires one exclusion to equal the manifest location but does not check the others, so a
signer can add ranges that drop real content — or every other byte — and the SDK still reports **Trusted**
(fixtures ``exclusion-twozone.png``, ``exclusion-allcontent.png``, built by scripts/build_c2pa_exclusion_fixtures.py).
Such a manifest must never be valid/trusted evidence: the ``c2pa`` check fails with a Korean reason.

P0R-21: the frozen Interim Trust List is a separate bundle; a signer chaining only to it is labelled ``itl`` and its
declaration is moderate, never strong; revocation is not checked offline and every basis text says so.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from deepfake_lens import c2pa_binding as cb
from deepfake_lens.result_types import CoverageStatus, EvidenceDirection, EvidenceStrength, Verdict

HAVE_SDK = importlib.util.find_spec("c2pa") is not None
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "c2pa-2.4"
ATTACK_CA = FIXTURES / "exclusion-attack-ca-cert.pem"


class DataHashRangeRulesTest(unittest.TestCase):
    """The pure range rules (no SDK)."""

    STORE = [(100, 400)]

    def check(self, exclusions: object, size: int = 10_000) -> cb.HardBindingVerdict:
        return cb.evaluate_data_hash(exclusions, file_size=size, store_ranges=self.STORE, store_located=True)

    def test_the_store_alone_is_trustworthy(self) -> None:
        self.assertTrue(self.check([{"start": 100, "length": 300}]).ok)

    def test_a_second_range_over_content_is_refused(self) -> None:
        verdict = self.check([{"start": 100, "length": 300}, {"start": 400, "length": 2000}])
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.excluded_outside_store, 2000)
        self.assertIn("C2PA 해시 제외범위 이상", verdict.reason)
        self.assertIn("서명 유효 여부와 무관하게 출처 근거로 쓰지 않습니다", verdict.reason)

    def test_whole_file_and_all_content_are_refused(self) -> None:
        self.assertFalse(self.check([{"start": 0, "length": 10_000}]).ok)
        all_content = [{"start": 0, "length": 100}, {"start": 100, "length": 300}, {"start": 400, "length": 9600}]
        verdict = self.check(all_content)
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.hashed_bytes, 0)

    def test_overlap_out_of_range_negative_and_malformed_are_refused(self) -> None:
        for bad in (
            [{"start": 100, "length": 300}, {"start": 350, "length": 10}],
            [{"start": 9_990, "length": 50}],
            [{"start": -1, "length": 5}],
            [{"start": "0", "length": 5}],
            [{"start": True, "length": 5}],
            "all",
            [3],
        ):
            with self.subTest(bad=bad):
                self.assertFalse(self.check(bad).ok)

    def test_framing_slack_outside_the_store_is_allowed(self) -> None:
        self.assertTrue(self.check([{"start": 100 - cb.STORE_SLACK_BYTES, "length": 300 + cb.STORE_SLACK_BYTES}]).ok)
        self.assertFalse(self.check([{"start": 100 - cb.STORE_SLACK_BYTES - 1, "length": 301 + cb.STORE_SLACK_BYTES}]).ok)

    def test_an_unlocated_store_still_needs_hashed_content(self) -> None:
        unlocated = cb.evaluate_data_hash([{"start": 0, "length": 9_990}], file_size=10_000, store_ranges=[], store_located=False)
        self.assertFalse(unlocated.ok)
        self.assertTrue(cb.evaluate_data_hash([{"start": 0, "length": 500}], file_size=10_000, store_ranges=[], store_located=False).ok)

    def test_bmff_exclusions_outside_the_allow_list_are_refused(self) -> None:
        self.assertTrue(cb.evaluate_bmff_hash([{"xpath": "/uuid"}, {"xpath": "/ftyp"}]).ok)
        for bad in ([{"xpath": "/mdat"}], [{"xpath": "/moov/trak"}], [{"length": 3}], "x"):
            with self.subTest(bad=bad):
                self.assertFalse(cb.evaluate_bmff_hash(bad).ok)


class StoreLocationTest(unittest.TestCase):
    def test_a_missing_binding_in_a_png_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            shutil.copyfile(FIXTURES / "ai-generated.png", path)
            verdict = cb.audit_assertion_store({"c2pa.actions.v2": {}}, path)
        self.assertFalse(verdict.ok)
        self.assertEqual(verdict.kind, "missing")

    def test_the_store_is_located_by_chunk_headers_only(self) -> None:
        """A C2PA-signed video can be gigabytes: the PNG walk reads 8-byte chunk headers, never the content."""
        class Counting:
            def __init__(self, handle):  # type: ignore[no-untyped-def]
                self.handle, self.read_bytes = handle, 0

            def seek(self, *args):  # type: ignore[no-untyped-def]
                return self.handle.seek(*args)

            def read(self, n=-1):  # type: ignore[no-untyped-def]
                data = self.handle.read(n)
                self.read_bytes += len(data)
                return data

        source = (FIXTURES / "ai-generated.png").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.png"
            idat_at = source.index(b"IDAT") - 4
            # one 512 MiB IDAT chunk (sparse) after the original chunks up to IDAT
            import struct

            with path.open("wb") as handle:
                handle.write(source[:idat_at] + struct.pack(">I", 512 * 1024 * 1024) + b"IDAT")
                handle.truncate(idat_at + 8 + 512 * 1024 * 1024 + 4)
                handle.seek(0, os.SEEK_END)
                handle.write(b"\x00\x00\x00\x00IEND\xaeB`\x82")
            with path.open("rb") as raw:
                counting = Counting(raw)
                stores = cb.locate_png_store(counting, path.stat().st_size)  # type: ignore[arg-type]
        self.assertEqual(len(stores), 1)
        self.assertLess(counting.read_bytes, 4096)


@unittest.skipUnless(HAVE_SDK, "c2pa-python 없음")
class ExclusionAttackFixturesTest(unittest.TestCase):
    """The attack files through the scan path: the SDK trusts two of them; the c2pa check fails for all five."""

    ATTACKS = ("exclusion-twozone.png", "exclusion-allcontent.png", "exclusion-twozone.jpg",
               "exclusion-wholefile.png", "exclusion-paststore.png")

    def setUp(self) -> None:
        env = mock.patch.dict(os.environ, {"DEEPFAKE_LENS_C2PA_TRUST_ANCHORS": str(ATTACK_CA)})
        env.start()
        self.addCleanup(env.stop)

    def test_the_sdk_itself_trusts_the_two_range_attack(self) -> None:
        from deepfake_lens.c2pa import validate_c2pa_manifest

        for name in ("exclusion-twozone.png", "exclusion-allcontent.png"):
            with self.subTest(name=name):
                validation = validate_c2pa_manifest(FIXTURES / name)
                assert validation is not None
                self.assertEqual(validation["state"], "Trusted")  # what a validator without this check reports
                self.assertEqual((validation["status"], validation["trusted"], validation["error_kind"]), ("invalid", False, "hash_exclusion"))
                self.assertIn("c2pa.hash.exclusion.invalid", validation["failure_codes"])  # type: ignore[operator]

    def test_every_attack_fails_the_c2pa_check_and_concludes_nothing(self) -> None:
        from deepfake_lens.analysis_api import AnalysisOptions, scan_file_run

        for name in self.ATTACKS:
            with self.subTest(name=name):
                [item] = scan_file_run(FIXTURES / name, AnalysisOptions(pixel_mode="off")).items
                result = item.result
                assert result is not None
                [entry] = [c for c in result.coverage if c.check == "c2pa"]
                self.assertEqual(entry.status, CoverageStatus.FAILED, entry.reason)
                self.assertTrue(entry.reason.startswith("AnalyzerError: C2PA 해시 제외범위 이상"), entry.reason)
                self.assertEqual(result.verdict_code, Verdict.UNDETERMINED)
                for evidence in result.evidence:
                    if evidence.layer == "c2pa":
                        self.assertNotIn(evidence.strength, (EvidenceStrength.STRONG, EvidenceStrength.MODERATE), evidence)

    def test_honest_manifests_keep_their_binding(self) -> None:
        from deepfake_lens.c2pa import validate_c2pa_manifest

        for name in ("ai-generated.png", "camera-capture.png", "composite-ai.png"):
            with self.subTest(name=name):
                validation = validate_c2pa_manifest(FIXTURES / name)
                assert validation is not None
                self.assertTrue(validation["hard_binding"]["ok"], validation["hard_binding"])  # type: ignore[index]


@unittest.skipUnless(HAVE_SDK, "c2pa-python 없음")
class InterimTrustListTest(unittest.TestCase):
    """P0R-21: the frozen ITL is its own bundle; an ITL-only signer is labelled and moderate."""

    def test_the_basis_names_the_itl_and_says_revocation_is_not_checked(self) -> None:
        from deepfake_lens.c2pa import C2PA_REVOCATION_NOTE, trust_basis, trust_basis_text

        basis = trust_basis()
        self.assertEqual(basis["itl_id"], "C2PA-INTERIM-TRUST-LIST")
        self.assertEqual(basis["revocation"], "not_checked")
        text = trust_basis_text(basis)
        self.assertIn("동결된 임시 신뢰 목록 ITL", text)
        self.assertIn(C2PA_REVOCATION_NOTE, text)

    def test_a_changed_itl_file_fails_closed(self) -> None:
        from deepfake_lens import c2pa as c2pa_module

        with tempfile.TemporaryDirectory() as tmp:
            meta = json.loads(c2pa_module.ITL_BUNDLE_MANIFEST.read_text(encoding="utf-8"))
            (Path(tmp) / meta["file"]).write_text("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n", encoding="ascii")
            (Path(tmp) / "c2pa-itl.json").write_text(json.dumps(meta), encoding="utf-8")
            with mock.patch.object(c2pa_module, "ITL_BUNDLE_MANIFEST", Path(tmp) / "c2pa-itl.json"):
                with self.assertRaises(c2pa_module.C2paTrustError):
                    c2pa_module.trust_basis()

    def test_an_itl_only_signer_is_labelled_and_moderate(self) -> None:
        import hashlib

        from deepfake_lens import c2pa as c2pa_module
        from deepfake_lens.analysis_api import AnalysisOptions, scan_file_run

        with tempfile.TemporaryDirectory() as tmp:
            pem = (FIXTURES / "test-ca-cert.pem").read_bytes()  # stands in for an ITL root
            (Path(tmp) / "itl.pem").write_bytes(pem)
            meta = dict(json.loads(c2pa_module.ITL_BUNDLE_MANIFEST.read_text(encoding="utf-8")), file="itl.pem", sha256=hashlib.sha256(pem).hexdigest())
            (Path(tmp) / "c2pa-itl.json").write_text(json.dumps(meta), encoding="utf-8")
            with mock.patch.object(c2pa_module, "ITL_BUNDLE_MANIFEST", Path(tmp) / "c2pa-itl.json"), \
                    mock.patch.dict(os.environ, {"DEEPFAKE_LENS_C2PA_TRUST_ANCHORS": ""}):
                validation = c2pa_module.validate_c2pa_manifest(FIXTURES / "ai-generated.png")
                [item] = scan_file_run(FIXTURES / "ai-generated.png", AnalysisOptions(pixel_mode="off")).items
        assert validation is not None
        self.assertEqual((validation["trusted"], validation["trust_list"]), (False, "itl"))
        assert item.result is not None
        main = [e for e in item.result.evidence if e.title.startswith("C2PA 서명(동결된 임시 신뢰 목록 ITL)")]
        self.assertEqual(len(main), 1, item.result.evidence)
        self.assertEqual((main[0].direction, main[0].strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.MODERATE))


if __name__ == "__main__":
    unittest.main()

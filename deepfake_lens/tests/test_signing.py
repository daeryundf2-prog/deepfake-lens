"""Tests for HMAC-SHA256 report signing (R-9a)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepfake_lens.signing import (
    REPORT_KEY_ENV,
    key_id_for,
    resolve_report_key,
    sign_report,
    verify_report,
)

KEY = b"test-key-material-0123456789abcdef0123"  # P0R-15: ≥ 32 bytes


def _report() -> dict:
    return {"schema_version": 1, "summary": {"total": 3, "high": 1}, "items": [{"path": "a.png", "score": 80}]}


class SignVerifyTest(unittest.TestCase):
    def test_round_trip_verifies(self) -> None:
        signed = sign_report(_report(), KEY)
        self.assertIsInstance(signed["signature"], str)
        self.assertEqual(len(signed["signature"]), 64)
        self.assertTrue(signed["signature_key_id"].startswith("hmac-sha256-v1:"))
        result = verify_report(signed, KEY)
        self.assertTrue(result.verified)
        self.assertEqual(result.status, "verified")

    def test_verify_from_file_path(self) -> None:
        signed = sign_report(_report(), KEY)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps(signed), encoding="utf-8")
            self.assertTrue(verify_report(path, KEY).verified)

    def test_tampered_field_fails_verification(self) -> None:
        signed = sign_report(_report(), KEY)
        tampered = json.loads(json.dumps(signed))
        tampered["summary"]["high"] = 0
        result = verify_report(tampered, KEY)
        self.assertFalse(result.verified)
        self.assertEqual(result.status, "tampered")

    def test_swapped_key_id_fails_verification(self) -> None:
        signed = sign_report(_report(), KEY)
        tampered = dict(signed, signature_key_id="hmac-sha256-v1:000000000000")
        self.assertFalse(verify_report(tampered, KEY).verified)

    def test_wrong_key_fails(self) -> None:
        signed = sign_report(_report(), KEY)
        self.assertFalse(verify_report(signed, b"other-key").verified)

    def test_missing_key_writes_unsigned_with_note(self) -> None:
        signed = sign_report(_report(), None)
        self.assertIsNone(signed["signature"])
        self.assertIn("서명 없음", signed["signature_note"])  # R4: note is Korean-only
        result = verify_report(signed, KEY)
        self.assertFalse(result.verified)
        self.assertEqual(result.status, "unsigned")

    def test_verify_without_key_reports_no_key(self) -> None:
        signed = sign_report(_report(), KEY)
        result = verify_report(signed, None)
        self.assertFalse(result.verified)
        self.assertEqual(result.status, "no-key")

    def test_unreadable_input(self) -> None:
        self.assertEqual(verify_report("/nonexistent/report.json", KEY).status, "unreadable")

    def test_signing_is_deterministic_and_strips_prior_signature(self) -> None:
        signed_once = sign_report(_report(), KEY)
        signed_twice = sign_report(signed_once, KEY)
        self.assertEqual(signed_once["signature"], signed_twice["signature"])
        # G30: a signed report now carries its own signature_note *inside*
        # the MAC (it used to be stripped and left unsigned); re-signing
        # replaces it with the same note, so the signature is stable.
        self.assertEqual(signed_twice["signature_note"], signed_once["signature_note"])
        self.assertTrue(verify_report(signed_twice, KEY).verified)

    def test_swapped_key_id_reported_as_key_mismatch(self) -> None:
        signed = sign_report(_report(), KEY)
        result = verify_report(dict(signed, signature_key_id="hmac-sha256-v1:000000000000"), KEY)
        self.assertEqual(result.status, "key-mismatch")
        self.assertEqual(result.reason, "키 ID 불일치")
        self.assertEqual(verify_report(signed, b"other-key").reason, "키 ID 불일치")

    def test_note_tool_version_and_model_pins_are_signed(self) -> None:
        signed = sign_report(_report(), KEY, model_pins=[{"profile": "aide-runtime", "pin": {"sha256": "ab" * 32}}])
        self.assertIn("tool_version", signed)
        self.assertEqual(signed["model_pins"][0]["profile"], "aide-runtime")
        for field, value in (("signature_note", "x"), ("tool_version", "9.9.9"), ("model_pins", [])):
            tampered = dict(signed, **{field: value})
            result = verify_report(tampered, KEY)
            self.assertEqual(result.status, "tampered", field)
            self.assertEqual(result.reason, "변조됨")

    def test_executed_models_record_the_profiles_that_ran(self) -> None:
        """P0R-14 (review §4-4): renamed from test_default_model_pins_record_unpinned_profiles — the signed body
        recorded the pins of every profile in the models folder (model_pins); it now records the profiles that ran
        on its rows (executed_models: profile, pin, threshold, calibration id), unpinned ones with pin null."""
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "a-runtime.json").write_text(json.dumps({"name": "a"}), encoding="utf-8")
            Path(tmp, "b-runtime.json").write_text(json.dumps({"pin": {"revision": "abc123"}, "threshold": 0.7, "calibration_id": "cal-b"}), encoding="utf-8")
            report = _report()
            report["items"] = [{"path": "x.png", "result": {"model_analysis": {"models": [
                {"profile": str(Path(tmp, "b-runtime.json")), "model": "b", "available": True},
                {"profile": str(Path(tmp, "a-runtime.json")), "model": "a", "available": False},
            ]}}}]
            with patch.dict(os.environ, {"DEEPFAKE_LENS_MODELS_DIR": tmp}):
                signed = sign_report(report, KEY)
        self.assertNotIn("model_pins", signed)
        self.assertEqual(signed["executed_models"], [
            {"profile": "b-runtime", "model": "b", "pin": {"revision": "abc123"}, "threshold": 0.7, "calibration_id": "cal-b"},
        ])
        self.assertTrue(verify_report(signed, KEY).verified)

    def test_the_signed_body_has_its_time_and_no_temp_folder(self) -> None:
        """P0R-14 (review §4-2, §4-5): generation time inside the MAC; the host temp folder never signed."""
        report = dict(_report(), temp_folder={"fallback": True, "base": "/host/tmp"})
        signed = sign_report(report, KEY)
        self.assertNotIn("temp_folder", signed)
        self.assertRegex(str(signed["generated_at"]), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d$")
        tampered = dict(signed, generated_at="2001-01-01T00:00:00+00:00")
        self.assertEqual(verify_report(tampered, KEY).reason, "변조됨")

    def test_a_short_key_is_refused_for_signing_not_for_verifying(self) -> None:
        """P0R-15: a key below MIN_REPORT_KEY_BYTES never signs; an old report signed with one still verifies."""
        from deepfake_lens.signing import MIN_REPORT_KEY_BYTES, ReportKeyError, _canonical

        short = b"x" * (MIN_REPORT_KEY_BYTES - 1)
        with self.assertRaises(ReportKeyError) as ctx:
            sign_report(_report(), short)
        self.assertIn("보고서 서명 키가 너무 짧습니다", str(ctx.exception))
        sign_report(_report(), b"x" * MIN_REPORT_KEY_BYTES)  # the minimum itself signs
        import hashlib
        import hmac

        body = dict(sign_report(_report(), None), signature_note="서명됨")
        body.pop("signature", None)
        body["signature_note"] = __import__("deepfake_lens.signing", fromlist=["SIGNED_NOTE"]).SIGNED_NOTE
        legacy = dict(body, signature=hmac.new(short, _canonical(body), hashlib.sha256).hexdigest(), signature_key_id=key_id_for(short))
        self.assertTrue(verify_report(legacy, short).verified)

    def test_unsigned_note_says_unsigned_in_korean(self) -> None:
        signed = sign_report(_report(), None)
        self.assertIn("서명 없음", signed["signature_note"])
        self.assertEqual(verify_report(signed, KEY).reason, "서명 없음")


class KeyResolutionTest(unittest.TestCase):
    def test_env_var_key(self) -> None:
        with patch.dict(os.environ, {REPORT_KEY_ENV: "env-secret-0123456789abcdef0123456789"}):
            self.assertEqual(resolve_report_key(), b"env-secret-0123456789abcdef0123456789")
            self.assertEqual(key_id_for(b"env-secret-0123456789abcdef0123456789"), key_id_for(resolve_report_key()))

    def test_key_file_wins_over_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "key.bin"
            key_file.write_bytes(b"file-key\n")
            with patch.dict(os.environ, {REPORT_KEY_ENV: "env-secret-0123456789abcdef0123456789"}):
                self.assertEqual(resolve_report_key(key_file), b"file-key")

    def test_no_key_returns_none(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop(REPORT_KEY_ENV, None)
            self.assertIsNone(resolve_report_key())
            self.assertIsNone(resolve_report_key("/nonexistent/key.bin"))


class CliSigningTest(unittest.TestCase):
    def test_scan_json_out_signed_via_env_key(self) -> None:
        from deepfake_lens.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            root.mkdir()
            (root / "note.txt").write_text("plain note", encoding="utf-8")
            # P11 (round 8): the report used to be written into the scanned
            # folder itself — now refused (exit 2); it goes next to it.
            out = Path(tmp) / "report.json"
            with patch.dict(os.environ, {REPORT_KEY_ENV: "cli-secret-0123456789abcdef0123456789"}):
                rc = main(["scan", str(root), "--no-default-engine", "--json-out", str(out), "--sign", "--format", "json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIsInstance(payload["signature"], str)
            self.assertTrue(verify_report(out, b"cli-secret-0123456789abcdef0123456789").verified)

    def test_scan_json_out_unsigned_without_key(self) -> None:
        from deepfake_lens.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            root.mkdir()
            (root / "note.txt").write_text("plain note", encoding="utf-8")
            # P11 (round 8): the report used to be written into the scanned
            # folder itself — now refused (exit 2); it goes next to it.
            out = Path(tmp) / "report.json"
            env = {key: value for key, value in os.environ.items() if key != REPORT_KEY_ENV}
            with patch.dict(os.environ, env, clear=True):
                rc = main(["scan", str(root), "--no-default-engine", "--json-out", str(out), "--sign", "--format", "json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIsNone(payload["signature"])
            self.assertIn("서명 없음", payload["signature_note"])  # R4: note is Korean-only


if __name__ == "__main__":
    unittest.main()


class ShortKeyCommandsTest(unittest.TestCase):
    """P0R-15: the CLI refuses a short signing key before any work; servers refuse to start with one."""

    def test_scan_sign_with_a_short_key_file_and_web_with_a_short_env_key(self) -> None:
        import contextlib
        import io

        from deepfake_lens.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp, "key")
            key.write_text("short", encoding="utf-8")
            (Path(tmp) / "case").mkdir()
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = main(["scan", str(Path(tmp) / "case"), "--sign", "--key-file", str(key), "--json-out", str(Path(tmp) / "r.json")])
            self.assertEqual(code, 2)
            self.assertIn("보고서 서명 키가 너무 짧습니다", err.getvalue())
            self.assertFalse((Path(tmp) / "r.json").exists())
            err = io.StringIO()
            with patch.dict(os.environ, {REPORT_KEY_ENV: "short"}), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = main(["web", "--folder", str(Path(tmp) / "case"), "--port", "1"])
            self.assertEqual(code, 2)
            self.assertIn("DEEPFAKE_LENS_REPORT_KEY", err.getvalue())

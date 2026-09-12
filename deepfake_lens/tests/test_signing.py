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

KEY = b"test-key-material"


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
        self.assertIn("unsigned", signed["signature_note"])
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
        self.assertNotIn("signature_note", signed_twice)


class KeyResolutionTest(unittest.TestCase):
    def test_env_var_key(self) -> None:
        with patch.dict(os.environ, {REPORT_KEY_ENV: "env-secret"}):
            self.assertEqual(resolve_report_key(), b"env-secret")
            self.assertEqual(key_id_for(b"env-secret"), key_id_for(resolve_report_key()))

    def test_key_file_wins_over_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            key_file = Path(tmp) / "key.bin"
            key_file.write_bytes(b"file-key\n")
            with patch.dict(os.environ, {REPORT_KEY_ENV: "env-secret"}):
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
            root = Path(tmp)
            (root / "note.txt").write_text("plain note", encoding="utf-8")
            out = root / "report.json"
            with patch.dict(os.environ, {REPORT_KEY_ENV: "cli-secret"}):
                rc = main(["scan", str(root), "--json-out", str(out), "--sign", "--format", "json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIsInstance(payload["signature"], str)
            self.assertTrue(verify_report(out, b"cli-secret").verified)

    def test_scan_json_out_unsigned_without_key(self) -> None:
        from deepfake_lens.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text("plain note", encoding="utf-8")
            out = root / "report.json"
            env = {key: value for key, value in os.environ.items() if key != REPORT_KEY_ENV}
            with patch.dict(os.environ, env, clear=True):
                rc = main(["scan", str(root), "--json-out", str(out), "--sign", "--format", "json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIsNone(payload["signature"])
            self.assertIn("unsigned", payload["signature_note"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from deepfake_lens.core import SCAN_JSON_SCHEMA_VERSION, scan_directory, scan_to_json, scan_to_json_text


class ScanJsonContractTest(unittest.TestCase):
    def test_scan_json_payload_carries_schema_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "note.txt").write_text("As an AI language model, I can help.", encoding="utf-8")

            summary, items = scan_directory(root)
            payload = scan_to_json(summary, items)

            self.assertEqual(payload["schema_version"], SCAN_JSON_SCHEMA_VERSION)
            self.assertEqual(payload["schema_version"], 1)
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


if __name__ == "__main__":
    unittest.main()

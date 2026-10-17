"""TB-8: the fixed imwatermark messages of the open reference pipelines that embed one by default."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from deepfake_lens.media_marks import load_registry, sdxl_check, sdxl_evidence
from deepfake_lens.result_types import EvidenceDirection, EvidenceStrength

HAVE_PIXELS = all(importlib.util.find_spec(m) is not None for m in ("numpy", "cv2", "PIL"))


class RegistryEntriesTest(unittest.TestCase):
    def entries(self) -> dict[str, dict]:
        return {e["id"]: e for e in load_registry().for_algorithm("imwatermark-dwtDct")}

    def test_messages_are_the_source_constants(self) -> None:
        entries = self.entries()
        # black-forest-labs/flux src/flux/util.py: [int(bit) for bit in bin(WATERMARK_MESSAGE)[2:]] — 46 bits.
        flux = 0b001010101111111010000111100111001111010100101110
        self.assertEqual(entries["flux1-reference-imwatermark-dwtdct-46"]["message_bits"], bin(flux)[2:])
        self.assertEqual(len(bin(flux)[2:]), 46)
        # imwatermark 'bytes': numpy.unpackbits of the UTF-8 bytes (MSB first).
        for key, text in (("sd1-txt2img-imwatermark-dwtdct-bytes", b"StableDiffusionV1"), ("sd2-scripts-imwatermark-dwtdct-bytes", b"SDV2")):
            self.assertEqual(entries[key]["message_bits"], "".join(f"{b:08b}" for b in text))
        sdxl = 0b101100111110110010010000011110111011000110011110
        self.assertEqual(entries["sdxl-imwatermark-dwtdct-48"]["message_bits"], bin(sdxl)[2:])

    def test_every_entry_cites_a_commit(self) -> None:
        for entry in self.entries().values():
            self.assertTrue(entry["source"] and entry["checked"] and entry["generator"])
        self.assertTrue(_raw()["no_default_watermark"]["note"].startswith("확인: 기본 워터마크 없음"))


def _raw() -> dict:
    import json

    from deepfake_lens.media_marks import REGISTRY_PATH

    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


@unittest.skipUnless(HAVE_PIXELS, "numpy, opencv and Pillow required")
class EmbeddedMessageTest(unittest.TestCase):
    def test_each_message_names_its_pipeline(self) -> None:
        import numpy as np
        from PIL import Image

        from deepfake_lens.wm_imwatermark import encode_array_for_tests

        # Seeded noise (dwtDct round-trips reliably on textured content, as test_wm_sdxl); 1024 px so the 136-bit SD 1
        # message gets enough block votes per bit (at 512 px it does not round-trip exactly).
        base = np.clip(128 + np.random.RandomState(1).normal(0, 64, (1024, 1024, 3)), 0, 255).astype(np.uint8)
        for entry in load_registry().for_algorithm("imwatermark-dwtDct"):
            with self.subTest(entry=entry["id"]), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "m.png"
                Image.fromarray(encode_array_for_tests(base, entry["message_bits"])).save(path)
                outcome = sdxl_check(path)
                assert outcome.entry is not None
                self.assertEqual(outcome.entry["id"], entry["id"])
                [item] = sdxl_evidence(outcome)
                self.assertEqual(item.title, entry["title"])
                self.assertEqual((item.direction, item.strength), (EvidenceDirection.SYNTHETIC, EvidenceStrength.STRONG))
                n = len(entry["message_bits"])
                self.assertIn(f"{n}/{n}", item.detail)


if __name__ == "__main__":
    unittest.main()

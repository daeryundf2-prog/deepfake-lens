"""R18-8 (round 18): GUI review marks are keyed by content and scan folder, not by the row path.

``itemKey`` was ``item.path || item.name`` over one global store (browser
``localStorage`` and ``~/.deepfake-lens/reviews.json``): another case's
``IMG_0001.jpg`` showed this case's memo and "검토됨", and the JSON/CSV
exports carried them. Keys are now ``v2|<scope>|sha256:<hash>`` with the
scanned folder (or the page's uploads) as the scope; pre-v2 keys are never
attached to a row.

The suite has no JavaScript engine in the reference environment, so the key
function is checked in the source; where ``node`` or macOS ``osascript`` is
present it is also run.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

GUI_JS = Path(__file__).resolve().parents[1] / "gui.js"


def _key_block() -> str:
    source = GUI_JS.read_text(encoding="utf-8")
    start = source.index("        const REVIEW_KEY_VERSION")
    end = source.index("        let reviewPushTimer")
    return source[start:end]


class ReviewKeySourceTest(unittest.TestCase):
    def test_the_key_is_content_and_scope(self) -> None:
        block = _key_block()
        self.assertIn("const REVIEW_KEY_VERSION = 'v2';", block)
        self.assertIn("function reviewScope() { return lastScanRoot ? 'root:' + lastScanRoot : uploadReviewScope; }", block)
        self.assertIn("const what = sha ? 'sha256:' + sha : 'path:' + (item.path || item.name || '');", block)
        self.assertIn("return `${REVIEW_KEY_VERSION}|${reviewScope()}|${what}`;", block)
        source = GUI_JS.read_text(encoding="utf-8")
        self.assertNotIn("function itemKey(item) { return item.path || item.name || ''; }", source)
        # Every read and write of a mark goes through itemKey (store, star, memo, filter, exports).
        self.assertEqual(re.findall(r"reviewStore\[(?!itemKey\(|key\]|k\]|changedKey\])[^\]]*\]", source), [])


def _js_engine() -> list[str] | None:
    if shutil.which("node"):
        return ["node"]
    if shutil.which("osascript"):
        return ["osascript", "-l", "JavaScript"]
    return None


@unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
class ReviewKeyBehaviourTest(unittest.TestCase):
    def test_same_name_in_two_cases_and_two_contents_get_four_keys(self) -> None:
        engine = _js_engine()
        assert engine is not None
        body = (
            "var lastScanRoot = '/cases/A';\n" + _key_block() + "\n"
            "var a = {path: 'IMG_0001.jpg', name: 'IMG_0001.jpg', sha256: 'a'.repeat(64)};\n"
            "var b = {path: 'IMG_0001.jpg', name: 'IMG_0001.jpg', sha256: 'b'.repeat(64)};\n"
            "var out = [itemKey(a), itemKey(b)]; lastScanRoot = '/cases/B'; out.push(itemKey(a), itemKey({path: 'x.bin'}));\n"
        )
        script = (
            f"{body}console.log(out.join('\\n'));" if engine[0] == "node" else f"function run() {{\n{body}return out.join('\\n');\n}}"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "keys.js"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([*engine, str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        keys = done.stdout.strip().splitlines()
        self.assertEqual(keys, [
            "v2|root:/cases/A|sha256:" + "a" * 64,
            "v2|root:/cases/A|sha256:" + "b" * 64,
            "v2|root:/cases/B|sha256:" + "a" * 64,
            "v2|root:/cases/B|path:x.bin",
        ])


if __name__ == "__main__":
    unittest.main()

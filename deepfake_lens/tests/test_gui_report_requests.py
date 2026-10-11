"""GUI report requests (gui.js).

R19-13 (round 19): a report refused with 409 because files changed after the
scan names those files in the error the GUI shows.

The reference environment may lack a JavaScript engine, so the source is
checked; where ``node`` or macOS ``osascript`` is present the function also runs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

GUI_JS = Path(__file__).resolve().parents[1] / "gui.js"


def _function(name: str) -> str:
    """The source of the top-level ``function``/``async function`` ``name`` in gui.js (brace-matched)."""
    source = GUI_JS.read_text(encoding="utf-8")
    start = source.index(f"function {name}(")
    if source[start - 6:start] == "async ":
        start -= 6
    depth = 0
    for index in range(source.index("{", start), len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unbalanced function {name}")


def _js_engine() -> list[str] | None:
    if shutil.which("node"):
        return ["node", "-e"]
    if shutil.which("osascript"):
        return ["osascript", "-l", "JavaScript", "-e"]
    return None


def _run_js(body: str) -> str:
    engine = _js_engine()
    assert engine is not None
    done = subprocess.run([*engine, body], capture_output=True, text=True, timeout=60)
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return (done.stdout or done.stderr).strip()


class ChangedRowsErrorSourceTest(unittest.TestCase):
    def test_api_error_names_the_changed_rows(self) -> None:
        source = _function("apiError")
        self.assertIn("j.changed_rows", source)
        self.assertIn("바뀐 파일", source)


@unittest.skipUnless(_js_engine(), "no JavaScript engine (node or osascript)")
class ChangedRowsErrorBehaviourTest(unittest.TestCase):
    def test_the_409_message_lists_the_paths(self) -> None:
        payload = {
            "error": "검사 뒤 바뀐 파일이 있습니다 — 다시 검사하십시오(보고서를 만들지 않았습니다)",
            "changed_rows": [{"path": f"f{index}.png"} for index in range(7)],
        }
        # The function made synchronous (its only await is res.json()) so any engine runs it.
        sync = _function("apiError").replace("async function", "function").replace("await ", "")
        script = sync + "\nconst res = {status: 409, headers: {get: () => 'application/json'}, json: () => (" + json.dumps(payload) + ")};"
        if _js_engine()[0] == "node":  # type: ignore[index]
            script += "\nconsole.log(apiError(res));"
        else:
            script += "\napiError(res);"
        text = _run_js(script)
        self.assertIn(payload["error"], text)
        self.assertIn("바뀐 파일: f0.png, f1.png, f2.png, f3.png, f4.png 외 2개", text)


if __name__ == "__main__":
    unittest.main()

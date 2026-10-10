"""R12-4 (round 12): previews and heatmaps of a non-UTF-8 file name.

Before R12-4 the GUI built ``/api/preview?path=<text>`` with
``encodeURIComponent``, which throws "URI malformed" on the lone surrogate
a non-UTF-8 name decodes to — the result viewer showed "미리보기 실패: URI
malformed" (English) — and no other URL encoding could reach the file:
percent-encoded raw bytes are decoded by the server as U+FFFD.

Now every row carries ``path_b64`` (URL-safe base64 of ``os.fsencode`` of
its real relative path; not on archive members), a heatmap carries
``pixel_analysis.heatmap_path_b64``, the folder scan payload carries
``scan_root_b64``, and both servers' ``/api/preview`` and ``/api/heatmap``
accept ``path_b64``/``root_b64`` under the same read-root checks. The GUI
always uses them and words a loading failure in Korean.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import threading
import unittest
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch
from urllib.parse import quote

from deepfake_lens import webapp_api
from deepfake_lens.json_text import PATH_B64_INVALID, fs_b64decode, fs_b64encode
from deepfake_lens.result_text import escape_row_path
from deepfake_lens.result_types import ScanItem
from deepfake_lens.tests.test_core import _photo_like_pixel_fn, _write_valid_rgb_png

HAVE_FASTAPI = importlib.util.find_spec("fastapi") is not None and importlib.util.find_spec("httpx") is not None
GUI_JS = Path(__file__).resolve().parents[1] / "gui.js"
# "증거" in CP949 — not valid UTF-8.
CP949_NAME = b"\xc1\xf5\xb0\xc5.png"
NAME = os.fsdecode(CP949_NAME)
NON_UTF8_SKIP = "파일 시스템이 UTF-8이 아닌 파일 이름을 허용하지 않음(Windows·macOS)"
PLAYWRIGHT_SKIP = "node + playwright + Chromium not installed (headless GUI check)"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


class FsB64Test(unittest.TestCase):
    def test_round_trip_keeps_the_bytes(self) -> None:
        for text in ("plain.png", "증거.png", NAME, "a b/c#d?.png", "x\\y:z.png"):
            encoded = fs_b64encode(text)
            self.assertRegex(encoded, r"^[A-Za-z0-9_-]+=*$")
            self.assertEqual(fs_b64decode(encoded), text)
            self.assertEqual(base64.urlsafe_b64decode(encoded), os.fsencode(text))
        self.assertEqual(fs_b64decode(fs_b64encode(NAME).rstrip("=")), NAME)  # padding optional

    def test_malformed_values_are_refused_in_korean(self) -> None:
        for bad in ("", "@@@", "a/b", "a+b", _b64(b"a\x00b"), "=" * 4, "A" * 20000):
            with self.assertRaises(ValueError) as caught:
                fs_b64decode(bad)
            self.assertEqual(str(caught.exception), PATH_B64_INVALID)


class RowFieldTest(unittest.TestCase):
    def test_row_path_b64_is_the_real_relative_path(self) -> None:
        for real in ("a.png", "sub/증거.png", NAME, "evil.zip::inner/a.png", "tri:::c.png", "bs\\:x.png"):
            row = ScanItem(escape_row_path(real), Path(real).name, "image", "analyzed", 1).to_json()
            self.assertEqual(fs_b64decode(str(row["path_b64"])), real, real)
            self.assertEqual(list(row)[:2], ["path", "path_b64"])

    def test_archive_member_rows_have_no_path_b64(self) -> None:
        row = ScanItem("a.zip::x.png", "x.png", "image", "analyzed", 1, container="a.zip", member="x.png", member_index=1).to_json()
        self.assertNotIn("path_b64", row)


class UploadRowTest(unittest.TestCase):
    def test_upload_rows_name_the_client_path_never_the_temp_file(self) -> None:
        """An upload row's path is replaced by the client's name after to_json;
        its path_b64 follows it (QA-OUT-4 compares upload and scan rows)."""
        record = ScanItem("/tmp/tmpabc123.png", "tmpabc123.png", "image", "analyzed", 1).to_json()
        record["path"] = escape_row_path("증거::사진.png")
        webapp_api._mark_uploads([record])
        self.assertEqual(fs_b64decode(str(record["path_b64"])), "증거::사진.png")
        member: dict[str, object] = {"path": "a.zip::x.png", "member": "x.png", "container": "a.zip"}
        webapp_api._mark_uploads([member])
        self.assertNotIn("path_b64", member)


class _ServerCase(unittest.TestCase):
    HEADERS = {"X-Deepfake-Lens-Client": "gui"}

    def setUp(self) -> None:
        roots: Any = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        home = base / "home"
        home.mkdir()
        for key, value in (("HOME", str(home)), ("DEEPFAKE_LENS_LOG_DIR", str(home / "logs"))):
            self.addCleanup(self._restore_env, key, os.environ.get(key))
            os.environ[key] = value
        self.folder = base / "case"
        self.folder.mkdir()
        self.outside = base / "outside.png"
        photo = base / "photo.png"
        _write_valid_rgb_png(photo, 160, 160, _photo_like_pixel_fn(160, 160, seed=3))
        self.photo = photo.read_bytes()
        self.outside.write_bytes(self.photo)
        try:
            with open(os.fsencode(self.folder) + b"/" + CP949_NAME, "wb") as handle:
                handle.write(self.photo)
        except (OSError, UnicodeError):
            raise unittest.SkipTest(NON_UTF8_SKIP)
        if NAME not in os.listdir(self.folder):
            raise unittest.SkipTest(NON_UTF8_SKIP)
        (self.folder / "plain.png").write_bytes(self.photo)

    @staticmethod
    def _restore_env(key: str, value: str | None) -> None:
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def _web(self) -> Callable[[str], tuple[int, bytes, str]]:
        from deepfake_lens.webapp import build_server

        server = build_server("127.0.0.1", 0, default_folder=self.folder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]

        def get(path: str) -> tuple[int, bytes, str]:
            request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", headers=self.HEADERS)
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    return response.status, response.read(), response.headers.get("Content-Type", "")
            except urllib.error.HTTPError as exc:
                return exc.code, exc.read(), exc.headers.get("Content-Type", "")

        return get

    def _api(self) -> Callable[[str], tuple[int, bytes, str]]:
        from fastapi.testclient import TestClient

        from deepfake_lens import api_server

        # As api_server.run_server does: the operator's --folder is the read root.
        webapp_api.configure_read_roots(self.folder)
        client = TestClient(api_server.create_app(default_folder=self.folder), raise_server_exceptions=False)

        def get(path: str) -> tuple[int, bytes, str]:
            response = client.get(path, headers={"host": "localhost", **self.HEADERS})
            return response.status_code, response.content, response.headers.get("content-type", "")

        return get

    def _check_leg(self, leg: str, get: Callable[[str], tuple[int, bytes, str]]) -> None:
        status, body, _ = get("/api/scan?folder=" + quote(str(self.folder)) + "&pixel=deep&heatmaps=true")
        self.assertEqual(status, 200, (leg, body[:300]))
        scan = json.loads(body)
        self.assertEqual(fs_b64decode(scan["scan_root_b64"]), scan["scan_root"])
        root_b64 = scan["scan_root_b64"]
        rows = {item["path"]: item for item in scan["items"]}
        self.assertEqual(sorted(rows), sorted([NAME, "plain.png"]), leg)
        self.assertEqual(base64.urlsafe_b64decode(rows[NAME]["path_b64"]), CP949_NAME)
        for name, row in rows.items():
            status, data, ctype = get(f"/api/preview?path_b64={row['path_b64']}&root_b64={root_b64}")
            self.assertEqual((status, ctype.split(";")[0]), (200, "image/png"), (leg, name, data[:200]))
            self.assertEqual(data, self.photo, (leg, name))
            heatmap = (row["result"].get("pixel_analysis") or {}).get("heatmap_path_b64")
            self.assertIsInstance(heatmap, str, (leg, name, row["result"].get("pixel_analysis")))
            assert isinstance(heatmap, str)
            self.assertEqual(fs_b64decode(heatmap), row["result"]["pixel_analysis"]["heatmap_path"])
            status, data, _ = get(f"/api/heatmap?path_b64={heatmap}&root_b64={root_b64}")
            self.assertEqual(status, 200, (leg, name, data[:200]))
            self.assertTrue(data.startswith(b"\x89PNG"), (leg, name))
        # The bytes are percent-encoded raw bytes in the old parameter: still unreachable (U+FFFD).
        status, _, _ = get("/api/preview?path=" + quote(os.fsencode(str(self.folder / NAME))) + "&root=" + quote(scan["scan_root"]))
        self.assertNotEqual(status, 200, leg)
        # Same read-root checks: a relative path_b64 that climbs out, an absolute one outside.
        for path_b64 in (_b64(b"../outside.png"), _b64(os.fsencode(str(self.outside)))):
            status, body, _ = get(f"/api/preview?path_b64={path_b64}&root_b64={root_b64}")
            self.assertEqual(status, 403, (leg, body[:200]))
            self.assertNotIn(self.photo[:64], body)
        status, body, _ = get(f"/api/heatmap?path_b64={_b64(os.fsencode(str(self.outside)))}&root_b64={root_b64}")
        self.assertEqual(status, 403, (leg, body[:200]))
        for endpoint in ("preview", "heatmap"):
            status, body, _ = get(f"/api/{endpoint}?path_b64=%40%40&root_b64={root_b64}")
            self.assertEqual((status, body.decode("utf-8")), (400, PATH_B64_INVALID), (leg, endpoint))


class PreviewByBytesServersTest(_ServerCase):
    def test_web_server(self) -> None:
        self._check_leg("web", self._web())

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
    def test_api_server(self) -> None:
        self._check_leg("api", self._api())


class FileAndFolderByBytesServersTest(_ServerCase):
    """R13-3 (round 13): /api/analyze-file and /api/scan address a non-UTF-8 path by its bytes.

    ``?file=`` / ``?folder=`` could not name such a path at all (a percent-
    encoded raw byte is decoded as U+FFFD: 404 / 400); ``file_b64`` /
    ``folder_b64`` take URL-safe base64 of the file-system bytes, under the
    same read-root checks, on both servers.
    """

    SUB = b"sub\xc1\xf5"

    def _check_leg(self, leg: str, get: Callable[[str], tuple[int, bytes, str]]) -> None:
        folder = os.fsencode(self.folder)
        os.makedirs(folder + b"/" + self.SUB, exist_ok=True)
        with open(folder + b"/" + self.SUB + b"/" + CP949_NAME, "wb") as handle:
            handle.write(self.photo)
        target = folder + b"/" + CP949_NAME
        # Before: the text parameter cannot carry the bytes.
        status, _, _ = get("/api/analyze-file?file=" + quote(target))
        self.assertEqual(status, 404, leg)
        status, _, _ = get("/api/scan?folder=" + quote(folder + b"/" + self.SUB))
        self.assertEqual(status, 400, leg)
        # file_b64: the same analysis as the ASCII-named copy.
        status, body, _ = get("/api/analyze-file?file_b64=" + _b64(target))
        self.assertEqual(status, 200, (leg, body[:300]))
        named = json.loads(body)
        self.assertEqual(os.fsencode(fs_b64decode(named["file_b64"])), target)
        status, body, _ = get("/api/analyze-file?file=" + quote(str(self.folder / "plain.png")))
        self.assertEqual(status, 200, (leg, body[:300]))
        plain = json.loads(body)
        for key in ("verdict_code", "grade", "evidence"):
            self.assertEqual(named.get(key), plain.get(key), (leg, key))
        # folder_b64: the subfolder is scanned (not the default folder), sync and async.
        status, body, _ = get("/api/scan?folder_b64=" + _b64(folder + b"/" + self.SUB))
        self.assertEqual(status, 200, (leg, body[:300]))
        scan = json.loads(body)
        self.assertEqual(os.fsencode(fs_b64decode(scan["scan_root_b64"])), folder + b"/" + self.SUB)
        self.assertEqual([item["path"] for item in scan["items"]], [NAME], leg)
        status, body, _ = get("/api/scan?async=1&folder_b64=" + _b64(folder + b"/" + self.SUB))
        self.assertEqual(status, 200, (leg, body[:300]))
        self.assertIn("job_id", json.loads(body))
        # Same read-root checks as the text parameters.
        outside_folder = os.fsencode(self.folder.parent)
        for query in (
            "/api/analyze-file?file_b64=" + _b64(os.fsencode(self.outside)),
            "/api/analyze-file?file_b64=" + _b64(b"../outside.png"),
            "/api/scan?folder_b64=" + _b64(outside_folder),
            "/api/scan?async=1&folder_b64=" + _b64(outside_folder),
        ):
            status, body, _ = get(query)
            self.assertEqual(status, 403, (leg, query, body[:200]))
            self.assertNotIn(self.photo[:64], body)
        # A malformed value is the Korean 400.
        for query in ("/api/analyze-file?file_b64=%40%40", "/api/scan?folder_b64=%40%40", "/api/scan?async=1&folder_b64=%40%40"):
            status, body, _ = get(query)
            self.assertEqual(status, 400, (leg, query))
            self.assertIn(PATH_B64_INVALID, json.loads(body).get("error") or json.loads(body).get("detail"), (leg, query))

    def test_web_server(self) -> None:
        self._check_leg("web", self._web())

    @unittest.skipUnless(HAVE_FASTAPI, "fastapi + httpx not installed")
    def test_api_server(self) -> None:
        self._check_leg("api", self._api())


class GuiSourceTest(unittest.TestCase):
    """The GUI never builds a media URL from a path as text."""

    def test_media_requests_use_base64_bytes_only(self) -> None:
        source = GUI_JS.read_text(encoding="utf-8")
        self.assertNotIn("/api/preview?path=", source)
        self.assertNotIn("/api/heatmap?path=", source)
        self.assertIn("'/api/preview?path_b64='", source)
        self.assertIn("'/api/heatmap?path_b64='", source)
        for label in ("미리보기 실패", "히트맵 로딩 실패", "스튜디오 로딩 실패"):
            line = next(line for line in source.splitlines() if label in line)
            self.assertIn("mediaErrorText(e)", line)
            self.assertNotIn("e.message", line)

    def test_media_error_text_is_korean(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is not installed")
        source = GUI_JS.read_text(encoding="utf-8")
        start = source.index("function mediaErrorText(e)")
        end = source.index("\n        }\n", start) + len("\n        }\n")
        script = source[start:end] + textwrap.dedent(
            """
            let uri; try { encodeURIComponent('\\udcc1'); } catch (e) { uri = e; }
            const out = [mediaErrorText(uri), mediaErrorText(new TypeError('Failed to fetch')),
                         mediaErrorText(new Error('서버에 연결할 수 없습니다')), mediaErrorText(undefined)];
            process.stdout.write(JSON.stringify(out));
            """
        )
        done = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=60, check=True)
        texts = json.loads(done.stdout)
        self.assertEqual(texts[2], "서버에 연결할 수 없습니다")
        for text in texts:
            self.assertRegex(text, "[가-힣]")
            self.assertNotIn("URI", text)
            self.assertNotIn("fetch", text)


GUI_PROBE = r"""
const { chromium } = require('playwright');
(async () => {
  const [port, folder] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 2000 } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.goto(`http://127.0.0.1:${port}/gui`, { waitUntil: 'networkidle' });
  await page.fill('#folder-path', folder);
  await page.click('#analyze-btn');
  await page.waitForSelector('#results-section:not([hidden])', { timeout: 300000 });
  const cards = page.locator('#res-list .res');
  const out = [];
  for (let i = 0; i < await cards.count(); i++) {
    const card = cards.nth(i);
    await card.locator('.res-main').click();
    const detail = card.locator('.res-detail');
    await detail.locator('img.preview-media, .pv-slot .note:not(:has-text("로딩"))').first().waitFor({ timeout: 60000 });
    await page.waitForTimeout(300);
    const loaded = await detail.locator('img.preview-media').evaluateAll(els => els.map(e => e.complete && e.naturalWidth > 0));
    out.push({ main: await card.locator('.res-main').innerText(), loaded, slot: await detail.locator('.pv-slot').allInnerTexts() });
  }
  process.stdout.write(JSON.stringify({ cards: out, errors }));
  await browser.close();
})().catch(e => { process.stderr.write(String(e && e.stack || e)); process.exit(3); });
"""


def _playwright_ready() -> bool:
    node = shutil.which("node")
    if node is None:
        return False
    probe = "const {chromium}=require('playwright');chromium.launch().then(b=>b.close()).then(()=>process.exit(0),()=>process.exit(1))"
    try:
        return subprocess.run([node, "-e", probe], capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class HeadlessGuiTest(_ServerCase):
    """The result viewer shows the non-UTF-8 row's preview (headless Chromium)."""

    def test_gui_previews_a_non_utf8_row(self) -> None:
        if not _playwright_ready():
            self.skipTest(PLAYWRIGHT_SKIP)
        self._web()
        with tempfile.TemporaryDirectory() as work:
            script = Path(work) / "probe.js"
            script.write_text(GUI_PROBE, encoding="utf-8")
            done = subprocess.run(
                [str(shutil.which("node")), str(script), str(self.port), str(self.folder)],
                capture_output=True, text=True, timeout=600,
            )
        self.assertEqual(done.returncode, 0, done.stderr[-1500:])
        probe = json.loads(done.stdout)
        self.assertEqual(probe["errors"], [])
        self.assertEqual(len(probe["cards"]), 2, probe)
        for card in probe["cards"]:
            self.assertEqual(card["loaded"], [True], card)
            self.assertFalse(any("URI" in text or "실패" in text for text in card["slot"]), card)
        self.assertTrue(any("\\udcc1" in card["main"] for card in probe["cards"]), probe)


if __name__ == "__main__":
    unittest.main()

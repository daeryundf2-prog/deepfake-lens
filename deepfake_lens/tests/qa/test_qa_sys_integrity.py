"""QA-SYS-6 / QA-SYS-7 — report signature coverage and read-root confinement (WP-G: G30, G31).

Runs without network weights: the scans use ``no_default_engine`` and the
web tests drive the stdlib server (``webapp.build_server``) on an ephemeral
loopback port. ``deepfake-lens security`` runs this module as its behavioral
check set.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

from deepfake_lens import webapp_api
from deepfake_lens.core import scan_directory, scan_to_json
from deepfake_lens.reports import HEATMAP_PLACEHOLDER, _heatmap_img, extract_signed_report
from deepfake_lens.signing import REPORT_KEY_ENV, sign_report, verify_report

KEY = b"qa-sys-6-report-key"
PIN_SHA = "0123456789abcdef" * 4
SECRET = b"QA-SYS-7-SECRET-BYTES-do-not-serve"


def _write_generator_png(path: Path) -> None:
    """A PNG whose A1111 `parameters` chunk is strong deterministic evidence."""
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("parameters", "a portrait photo\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1, Model: sd_xl_base_1.0")
    image = Image.new("RGB", (160, 160))
    image.putdata([((x * 7) % 256, (y * 5) % 256, (x * y) % 256) for y in range(160) for x in range(160)])
    image.save(path, pnginfo=info)


def _write_secret_png(path: Path) -> None:
    """A real PNG followed by marker bytes — the bytes a leak would carry."""
    from PIL import Image

    Image.new("RGB", (32, 32), (200, 10, 10)).save(path)
    with path.open("ab") as handle:
        handle.write(SECRET)


def _leaves(node: Any, prefix: tuple[Any, ...] = ()) -> Iterator[tuple[tuple[Any, ...], Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, prefix + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _leaves(value, prefix + (index,))
    else:
        yield prefix, node


def _flip(value: Any) -> Any:
    """Change exactly one character (or the smallest unit) of a leaf."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if value is None:
        return "x"
    text = str(value)
    if not text:
        return "x"
    return ("b" if text[0] == "a" else "a") + text[1:]


def _set(node: Any, path: tuple[Any, ...], value: Any) -> None:
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


class QaSys6SignatureCoversWholeReportTest(unittest.TestCase):
    """QA-SYS-6: 보고서 JSON의 임의 필드(결론, 근거, note, 모델 해시) 한 글자 변경 후 검증 → 모든 경우 "변조됨"."""

    @classmethod
    def setUpClass(cls) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_generator_png(root / "generated.png")
            (root / "memo.txt").write_text("회의 메모: 다음 주 일정 확인", encoding="utf-8")
            summary, items = scan_directory(root)
        cls.report = scan_to_json(summary, items)
        cls.signed = sign_report(cls.report, KEY, model_pins=[
            {"profile": "aide-runtime", "pin": {"sha256": PIN_SHA}},
            {"profile": "sbi-effnet-runtime", "pin": None},
        ])

    def test_signed_report_verifies(self) -> None:
        """QA-SYS-6: an untouched signed report verifies."""
        result = verify_report(json.loads(json.dumps(self.signed)), KEY)
        self.assertTrue(result.verified, result)
        self.assertEqual(result.reason, "검증됨")

    def test_named_fields_are_inside_the_signature(self) -> None:
        """QA-SYS-6: verdict, evidence, note, model pin, item sha256 and tool version each flip to 변조됨."""
        generated = next(i for i, item in enumerate(self.signed["items"]) if item["name"] == "generated.png")
        item = self.signed["items"][generated]
        self.assertEqual(item["result"]["verdict_code"], "manipulation_evidence")
        self.assertTrue(item["result"]["evidence"])
        self.assertTrue(item["sha256"])
        named = {
            "결론(verdict_code)": ("items", generated, "result", "verdict_code"),
            "결론 문장(verdict)": ("items", generated, "result", "verdict"),
            "근거 제목": ("items", generated, "result", "evidence", 0, "title"),
            "근거 종류": ("items", generated, "result", "evidence", 0, "kind"),
            "note(signature_note)": ("signature_note",),
            "모델 해시(model_pins sha256)": ("model_pins", 0, "pin", "sha256"),
            "모델 프로필 이름": ("model_pins", 1, "profile"),
            "파일 해시(item sha256)": ("items", generated, "sha256"),
            "도구 버전": ("tool_version",),
        }
        for label, path in named.items():
            with self.subTest(field=label):
                tampered = json.loads(json.dumps(self.signed))
                node: Any = tampered
                for key in path:
                    node = node[key]
                _set(tampered, path, _flip(node))
                result = verify_report(tampered, KEY)
                self.assertEqual(result.status, "tampered")
                self.assertEqual(result.reason, "변조됨")

    def test_every_field_is_inside_the_signature(self) -> None:
        """QA-SYS-6: a one-character change to any leaf except signature/key id is 변조됨."""
        leaves = [path for path, _ in _leaves(self.signed) if path[0] not in {"signature", "signature_key_id"}]
        self.assertGreater(len(leaves), 50)
        for path in leaves:
            tampered = json.loads(json.dumps(self.signed))
            node: Any = tampered
            for key in path:
                node = node[key]
            _set(tampered, path, _flip(node))
            result = verify_report(tampered, KEY)
            self.assertEqual(result.reason, "변조됨", path)

    def test_key_id_swap_is_its_own_reason(self) -> None:
        """QA-SYS-6: a swapped key id is reported as 키 ID 불일치, distinct from 변조됨."""
        swapped = dict(self.signed, signature_key_id="hmac-sha256-v1:000000000000")
        self.assertEqual(verify_report(swapped, KEY).reason, "키 ID 불일치")

    def test_signed_json_file_from_cli_detects_note_edit(self) -> None:
        """QA-SYS-6: `scan --sign --json-out` file with one note character changed is 변조됨."""
        from deepfake_lens.cli import main

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            root.mkdir()
            (root / "note.txt").write_text("plain note", encoding="utf-8")
            out = Path(tmp) / "report.json"
            with patch.dict(os.environ, {REPORT_KEY_ENV: KEY.decode()}), patch("sys.stdout"):
                self.assertEqual(main(["scan", str(root), "--no-default-engine", "--json-out", str(out), "--sign", "--format", "json"]), 0)
            self.assertTrue(verify_report(out, KEY).verified)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertIn("model_pins", payload)
            self.assertIn("tool_version", payload)
            payload["signature_note"] = _flip(payload["signature_note"])
            out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self.assertEqual(verify_report(out, KEY).reason, "변조됨")

    def test_web_report_signed_with_env_key_and_tamper_detected(self) -> None:
        """QA-SYS-6: /api/report HTML and JSON are signed with DEEPFAKE_LENS_REPORT_KEY."""
        body = json.dumps({"items": self.report["items"]}).encode("utf-8")
        with patch.object(webapp_api, "_READ_ROOTS", OrderedDict()), patch.dict(os.environ, {REPORT_KEY_ENV: KEY.decode()}):
            html = webapp_api._report_payload(body, "html")
            signed_json = webapp_api._report_payload(body, "json")
        assert isinstance(html, bytes) and isinstance(signed_json, dict)
        embedded = extract_signed_report(html.decode("utf-8"))
        assert embedded is not None
        self.assertTrue(verify_report(embedded, KEY).verified)
        self.assertIn("보고서 서명: HMAC-SHA256 서명됨", html.decode("utf-8"))
        self.assertTrue(verify_report(signed_json, KEY).verified)
        self.assertIn("model_pins", signed_json)
        embedded["items"][0]["result"]["verdict_code"] = _flip(embedded["items"][0]["result"]["verdict_code"])
        self.assertEqual(verify_report(embedded, KEY).reason, "변조됨")

    def test_web_report_without_key_says_unsigned(self) -> None:
        """QA-SYS-6: without a key the web report states 서명 없음 explicitly."""
        body = json.dumps({"items": self.report["items"]}).encode("utf-8")
        env = {k: v for k, v in os.environ.items() if k != REPORT_KEY_ENV}
        with patch.object(webapp_api, "_READ_ROOTS", OrderedDict()), patch.dict(os.environ, env, clear=True):
            html = webapp_api._report_payload(body, "html")
            pdf = webapp_api._report_payload(body, "pdf")
        assert isinstance(html, bytes) and isinstance(pdf, bytes)
        self.assertIn("서명 없음", html.decode("utf-8"))
        embedded = extract_signed_report(html.decode("utf-8"))
        assert embedded is not None
        self.assertIsNone(embedded["signature"])
        self.assertEqual(verify_report(embedded, KEY).reason, "서명 없음")
        self.assertIn(b"UNSIGNED", pdf)


class _ServerFixture(unittest.TestCase):
    """A live stdlib web server whose only read root is ``self.root``."""

    def setUp(self) -> None:
        from deepfake_lens import webapp

        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.root = base / "case"
        self.outside = base / "elsewhere"
        self.root.mkdir()
        self.outside.mkdir()
        # Heatmaps go to the tool-owned output root (R-IN-1), kept in the
        # temp dir here instead of ~/.cache.
        self.heatmaps = base / "heatmap-root"
        env = patch.dict(os.environ, {"DEEPFAKE_LENS_HEATMAP_DIR": str(self.heatmaps)})
        env.start()
        self.addCleanup(env.stop)
        (self.root / "memo.txt").write_text("사건 메모", encoding="utf-8")
        _write_secret_png(self.outside / "secret.png")
        (self.outside / "secret.txt").write_bytes(SECRET)
        server = webapp.build_server("127.0.0.1", 0, default_folder=self.root)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_address[1]}"

    def request(self, path: str, body: dict[str, object] | None = None) -> tuple[int, bytes]:
        from deepfake_lens.webapp import CLIENT_HEADER

        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {CLIENT_HEADER: "qa"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def assertNoSecret(self, body: bytes) -> None:
        self.assertNotIn(SECRET, body)
        self.assertNotIn(base64.b64encode(SECRET)[:24], body)
        png_head = (self.outside / "secret.png").read_bytes()[:64]
        self.assertNotIn(base64.b64encode(png_head)[:40], body)

    def assertDenied(self, status: int, body: bytes) -> None:
        self.assertEqual(status, 403, body[:200])
        self.assertNoSecret(body)


class QaSys7ReadRootConfinementTest(_ServerFixture):
    """QA-SYS-7: /api/scan?folder=/ 등 등록되지 않은 경로로 요청. heatmap_path를 외부 파일로 지정한 report 요청 → 모두 403, 응답에 파일 내용 0바이트."""

    def _scan_rows_with_heatmap(self) -> list[dict[str, object]]:
        _write_generator_png(self.root / "photo.png")
        _, items = scan_directory(self.root, pixel_mode="deep", heatmaps=True)
        rows = [item.to_json() for item in items]
        with_heatmap = [row for row in rows if (((row.get("result") or {}).get("pixel_analysis") or {}).get("heatmap_path"))]  # type: ignore[union-attr]
        self.assertTrue(with_heatmap, "fixture must produce a heatmap")
        for row in with_heatmap:
            heatmap = Path(row["result"]["pixel_analysis"]["heatmap_path"])  # type: ignore[index]
            self.assertTrue(heatmap.is_relative_to(self.heatmaps), "heatmaps never land in the evidence folder (R-IN-1)")
        return rows

    def test_scan_of_filesystem_root_is_403(self) -> None:
        """QA-SYS-7: GET /api/scan?folder=/ → 403 {"error": "허용되지 않은 경로"}."""
        status, body = self.request("/api/scan?folder=/&no_default_engine=true")
        self.assertDenied(status, body)
        self.assertEqual(json.loads(body)["error"], "허용되지 않은 경로")

    def test_scan_outside_and_traversal_and_async_are_403(self) -> None:
        """QA-SYS-7: outside folder, ../ traversal and async scans are all 403."""
        for query in (
            f"folder={self.outside}",
            f"folder={self.root}/../{self.outside.name}",
            f"folder={self.outside}&async=1",
        ):
            with self.subTest(query=query):
                self.assertDenied(*self.request("/api/scan?" + query + "&no_default_engine=true"))

    def test_request_cannot_register_a_root(self) -> None:
        """QA-SYS-7: a refused scan registers nothing; the outside file stays unreadable."""
        self.request(f"/api/scan?folder={self.outside}&no_default_engine=true")
        self.assertEqual(list(webapp_api._READ_ROOTS), [self.root])
        self.assertDenied(*self.request(f"/api/heatmap?path={self.outside / 'secret.png'}&root={self.outside}"))
        self.assertDenied(*self.request(f"/api/preview?path={self.outside / 'secret.png'}&root={self.outside}"))

    def test_scan_inside_root_still_works(self) -> None:
        """QA-SYS-7 control: the registered root scans normally."""
        status, body = self.request(f"/api/scan?folder={self.root}&no_default_engine=true")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["summary"]["total"], 1)

    def test_analyze_file_outside_root_is_403(self) -> None:
        """QA-SYS-7: /api/analyze-file for a file outside the roots → 403."""
        self.assertDenied(*self.request(f"/api/analyze-file?file={self.outside / 'secret.txt'}"))

    def test_report_with_outside_heatmap_is_403_in_every_format(self) -> None:
        """QA-SYS-7: report POST whose heatmap_path points outside → 403 for html, pdf and json."""
        rows = self._scan_rows_with_heatmap()
        for row in rows:
            pixel = ((row.get("result") or {}).get("pixel_analysis") or {})  # type: ignore[union-attr]
            if pixel.get("heatmap_path"):
                pixel["heatmap_path"] = str(self.outside / "secret.png")
        for fmt in ("html", "pdf", "json"):
            with self.subTest(format=fmt):
                self.assertDenied(*self.request(f"/api/report?format={fmt}", {"items": rows}))

    def test_report_with_inside_heatmap_renders_it(self) -> None:
        """QA-SYS-7 control: a heatmap under the root is still inlined."""
        rows = self._scan_rows_with_heatmap()
        status, body = self.request("/api/report?format=html", {"items": rows})
        self.assertEqual(status, 200)
        self.assertIn(b"data:image/png;base64", body)
        self.assertNoSecret(body)

    def test_tool_owned_heatmap_is_served_but_nothing_else_there(self) -> None:
        """QA-SYS-7 (heatmap root): /api/heatmap serves a tool-written *.heatmap.png
        from the heatmap output root; any other file placed there, and the same
        name outside it, stays 403."""
        rows = self._scan_rows_with_heatmap()
        heatmap = next(Path(row["result"]["pixel_analysis"]["heatmap_path"]) for row in rows  # type: ignore[index]
                       if ((row.get("result") or {}).get("pixel_analysis") or {}).get("heatmap_path"))  # type: ignore[union-attr]
        status, body = self.request(f"/api/heatmap?path={heatmap}&root={self.root}")
        self.assertEqual(status, 200)
        self.assertEqual(body, heatmap.read_bytes())
        planted = heatmap.parent / "planted.png"
        planted.write_bytes((self.outside / "secret.png").read_bytes())
        self.assertDenied(*self.request(f"/api/heatmap?path={planted}&root={self.root}"))
        renamed = self.outside / heatmap.name
        renamed.write_bytes((self.outside / "secret.png").read_bytes())
        self.assertDenied(*self.request(f"/api/heatmap?path={renamed}&root={self.root}"))

    def test_report_does_not_hash_outside_files(self) -> None:
        """QA-SYS-7: an item path outside the roots is never read — its sha256 stays null."""
        rows = [{"path": str(self.outside / "secret.txt"), "name": "secret.txt", "kind": "text", "status": "failed", "size_bytes": 0, "sha256": "f" * 64}]
        status, body = self.request("/api/report?format=json", {"items": rows})
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIsNone(payload["items"][0]["sha256"])
        self.assertNoSecret(body)


class QaSys7ReadRootUnitTest(unittest.TestCase):
    """QA-SYS-7: read-root registry rules behind the HTTP checks."""

    def setUp(self) -> None:
        roots = patch.object(webapp_api, "_READ_ROOTS", OrderedDict())
        roots.start()
        self.addCleanup(roots.stop)

    def test_no_registered_root_means_default_folder_only(self) -> None:
        """QA-SYS-7: with nothing registered only the default folder is readable — never everything."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp).resolve()
            self.assertEqual(webapp_api._require_read_root(folder, folder), folder)
            with self.assertRaises(webapp_api.ReadRootDenied):
                webapp_api._require_read_root(Path("/"), folder)
            with self.assertRaises(webapp_api.ReadRootDenied):
                webapp_api._scan_payload("folder=/", default_folder=folder)

    def test_oldest_root_is_evicted_first(self) -> None:
        """QA-SYS-7: the registry evicts the oldest registration, not an arbitrary one."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            dirs = [base / f"r{index:03d}" for index in range(webapp_api._READ_ROOTS_MAX + 1)]
            for directory in dirs:
                directory.mkdir()
                webapp_api._register_read_root(directory)
            registered = list(webapp_api._READ_ROOTS)
        self.assertEqual(len(registered), webapp_api._READ_ROOTS_MAX)
        self.assertNotIn(dirs[0], registered)
        self.assertEqual(registered[0], dirs[1])
        self.assertEqual(registered[-1], dirs[-1])

    def test_allow_root_cli_flag_is_repeatable(self) -> None:
        """QA-SYS-7: `web --allow-root A --allow-root B` registers both at server setup."""
        from deepfake_lens.cli_parser import build_parser

        parser, _ = build_parser()
        args = parser.parse_args(["web", "--folder", "/case", "--allow-root", "/a", "--allow-root", "/b"])
        self.assertEqual(args.allow_root, [Path("/a"), Path("/b")])
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            (base / "a").mkdir()
            (base / "b").mkdir()
            webapp_api.configure_read_roots(base / "a", [base / "b"])
            self.assertEqual(list(webapp_api._READ_ROOTS), [base / "a", base / "b"])

    def test_heatmap_outside_allowed_paths_is_a_placeholder(self) -> None:
        """QA-SYS-7: reports._heatmap_img never reads a rejected heatmap path."""
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "secret.png"
            _write_secret_png(secret)
            html = _heatmap_img(str(secret), allow_path=lambda _path: False)
            self.assertNotIn("base64", html)
            self.assertNotIn("secret", html)
            self.assertIn(HEATMAP_PLACEHOLDER, html)
            self.assertIn("base64", _heatmap_img(str(secret), allow_path=lambda _path: True))


if __name__ == "__main__":
    unittest.main()

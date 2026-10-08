"""`deepfake-lens security`: behavioral security checks (G31).

The checks used to grep the source for marker strings ("allow_lan",
"_READ_ROOTS", …), which passed as long as the words existed — including
while /api/scan let any caller register any folder as a read root. Every
check below now *exercises* the behavior it names, and the QA suites for
report signatures (QA-SYS-6) and read-root confinement (QA-SYS-7) are run
and reported test by test. A check that cannot run is a failure, not a pass.

The network-marker scan stays a static guardrail (it lists findings; it is
not one of the behavioral checks).
"""

from __future__ import annotations

import json
import tempfile
import traceback
import unittest
from pathlib import Path
from typing import Any, Callable


# Substring markers for network capability. "subprocess" is deliberately not
# a marker: video.py legitimately spawns ffmpeg for frame extraction. The
# markers cover the standard-library network surface plus common client libs;
# a determined bypass is possible, so this stays a guardrail, not an audit.
NETWORK_MARKERS = [
    "requests.",
    "urllib.request",
    "http.client",
    "socket.create_connection",
    "socket.socket",
    "ssl.",
    "smtplib",
    "ftplib",
    "telnetlib",
    "xmlrpc",
    "urlopen",
    "websocket",
    "asyncio.open_connection",
    "asyncio.start_server",
    "aiohttp",
    "httpx",
]
# vendor_weights.py fetches checkpoints only on an explicit
# `vendor-weights --fetch` invocation and refuses outright under --offline;
# the network surface is deliberate, user-gated, and sha256-verified.
ALLOWED_NETWORK_FILES = {"security.py", "webapp.py", "vendor_weights.py"}

# QA suites run by the security check. Tests ship only in the source tree
# (pyproject excludes deepfake_lens.tests from the wheel); without them the
# check reports a failure instead of silently passing.
SECURITY_QA_MODULES = ("deepfake_lens.tests.qa.test_qa_sys_integrity",)


def _check_web_binds_localhost() -> tuple[bool, str]:
    from .webapp import build_server

    try:
        server = build_server("0.0.0.0", 0)
    except ValueError:
        return True, "0.0.0.0 바인드가 --allow-lan 없이 거부됨"
    server.server_close()
    return False, "0.0.0.0 바인드가 --allow-lan 없이 허용됨"


def _check_lan_requires_token() -> tuple[bool, str]:
    from .webapp import build_server

    try:
        server = build_server("0.0.0.0", 0, allow_lan=True)
    except ValueError:
        return True, "--allow-lan이 --token 없이 거부됨"
    server.server_close()
    return False, "--allow-lan이 --token 없이 허용됨"


def _check_client_header_required() -> tuple[bool, str]:
    from .webapp import CLIENT_HEADER, api_request_allowed

    without = api_request_allowed({}, token=None)
    with_header = api_request_allowed({CLIENT_HEADER: "gui"}, token=None)
    wrong_token = api_request_allowed({"X-Deepfake-Lens-Token": "wrong"}, token="secret")
    passed = not without and with_header and not wrong_token
    return passed, f"헤더 없음 허용={without}, 헤더 있음 허용={with_header}, 잘못된 토큰 허용={wrong_token}"


def _check_symlinks_opt_in() -> tuple[bool | None, str]:
    from .scan_cache import _iter_files

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "target.txt").write_text("x", encoding="utf-8")
        try:
            (root / "link.txt").symlink_to(root / "target.txt")
        except (OSError, NotImplementedError) as exc:
            return None, f"이 환경에서 심볼릭 링크를 만들 수 없음: {type(exc).__name__}"
        default = [path.name for path in _iter_files(root, recursive=False)]
        opted = [path.name for path in _iter_files(root, recursive=False, allow_symlinks=True)]
    passed = "link.txt" not in default and "link.txt" in opted
    return passed, f"기본={default}, allow_symlinks={opted}"


def _check_oversize_skip() -> tuple[bool, str]:
    from .core import scan_directory

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "big.txt").write_text("0123456789", encoding="utf-8")
        _, items = scan_directory(tmp, max_file_bytes=4)
    statuses = [item.status for item in items]
    return statuses == ["skipped"], f"max_file_bytes=4, 10바이트 파일 상태={statuses}"


def _check_report_redaction() -> tuple[bool, str]:
    from .core import ScanItem, summarize
    from .reports import write_html_report

    secret_dir = "case-7731-suspect-home"
    items = [ScanItem(f"{secret_dir}/photo.txt", "photo.txt", "text", "failed", 0, error="x")]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.html"
        write_html_report(out, summarize(items, capped=False), items, redact_paths=True)
        html = out.read_text(encoding="utf-8")
    leaked = secret_dir in html
    return not leaked, "redact_paths 보고서에 디렉터리 경로 " + ("노출" if leaked else "없음")


_INLINE_CHECKS: tuple[tuple[str, Callable[[], tuple[bool | None, str]]], ...] = (
    ("웹 서버는 기본적으로 localhost에만 바인드", _check_web_binds_localhost),
    ("LAN 바인드는 토큰 필수", _check_lan_requires_token),
    ("토큰 없는 API 요청은 사용자 정의 헤더 필수 (CSRF/드라이브바이 차단)", _check_client_header_required),
    ("심볼릭 링크 추적은 명시적 옵트인", _check_symlinks_opt_in),
    ("크기 상한 초과 파일은 건너뜀", _check_oversize_skip),
    ("보고서 경로 마스킹(redact_paths) 동작", _check_report_redaction),
)


class _CollectingResult(unittest.TestResult):
    def __init__(self) -> None:
        super().__init__()
        self.outcomes: list[dict[str, object]] = []

    def _record(self, test: unittest.TestCase, status: str, detail: str = "") -> None:
        description = test.shortDescription() or ""
        self.outcomes.append({
            "name": f"{description.split(':', 1)[0] if description else 'QA'}: {test.id()}",
            "passed": status == "passed",
            "status": status,
            "detail": detail[-800:],
        })

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self._record(test, "passed")

    def addFailure(self, test: unittest.TestCase, err: Any) -> None:
        super().addFailure(test, err)
        self._record(test, "failed", "".join(traceback.format_exception(*err)))

    def addError(self, test: unittest.TestCase, err: Any) -> None:
        super().addError(test, err)
        self._record(test, "failed", "".join(traceback.format_exception(*err)))

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        # A skipped security test proves nothing — reported, never a pass.
        self._record(test, "skipped", reason)


def run_security_qa(modules: tuple[str, ...] = SECURITY_QA_MODULES) -> list[dict[str, object]]:
    """Run the QA security suites; one check entry per test case."""
    checks: list[dict[str, object]] = []
    for module in modules:
        try:
            suite = unittest.defaultTestLoader.loadTestsFromName(module)
        except (ImportError, AttributeError) as exc:
            checks.append({
                "name": f"QA 모듈 {module}",
                "passed": False,
                "status": "failed",
                "detail": f"QA 테스트를 불러올 수 없음(소스 트리에서 실행하세요): {type(exc).__name__}: {exc}",
            })
            continue
        result = _CollectingResult()
        suite.run(result)
        if not result.outcomes:
            checks.append({"name": f"QA 모듈 {module}", "passed": False, "status": "failed", "detail": "실행된 테스트 없음"})
        checks.extend(result.outcomes)
    return checks


def build_security_check(root: Path | str) -> dict[str, object]:
    package = Path(root) / "deepfake_lens"
    if not package.is_dir():
        package = Path(__file__).resolve().parent
    findings = []
    for path in sorted(package.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        markers = [marker for marker in NETWORK_MARKERS if marker in text]
        if markers and path.name not in ALLOWED_NETWORK_FILES:
            findings.append({"path": str(path), "severity": "high", "issue": "unexpected network-capable import or call", "markers": markers})
    checks: list[dict[str, object]] = []
    for name, check in _INLINE_CHECKS:
        try:
            outcome, detail = check()
        except Exception as exc:  # noqa: BLE001 - a crashed check is reported as failed, never passed
            outcome, detail = False, f"검사 실행 실패: {type(exc).__name__}: {exc}"
        status = "skipped" if outcome is None else ("passed" if outcome else "failed")
        checks.append({"name": name, "passed": outcome is True, "status": status, "detail": detail})
    checks.extend(run_security_qa())
    failed = [check for check in checks if check["status"] == "failed"]
    qa_skipped = [check for check in checks if check["status"] == "skipped" and str(check["name"]).startswith("QA")]
    return {
        "version": "security-check-v2",
        "passed": not findings and not failed and not qa_skipped,
        "checks": checks,
        "findings": findings,
        "notes": [
            "각 검사는 실제 동작을 실행해 확인합니다(문자열 검색 아님). QA-SYS-6(서명 범위), QA-SYS-7(read-root 제한) 테스트를 함께 실행합니다.",
            "네트워크 마커 검사는 정적 가드레일이며 전체 보안 감사가 아닙니다.",
            "로컬 웹 서버는 localhost HTTP만 제공하며, scan/eval/train은 외부 네트워크 호출을 하지 않아야 합니다.",
        ],
    }


def write_security_check(root: Path | str, output_path: Path | str) -> dict[str, object]:
    payload = build_security_check(root)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload

from __future__ import annotations

import json
from pathlib import Path


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
ALLOWED_NETWORK_FILES = {"security.py", "webapp.py"}


def build_security_check(root: Path | str) -> dict[str, object]:
    package = Path(root) / "deepfake_lens"
    findings = []
    for path in sorted(package.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        markers = [marker for marker in NETWORK_MARKERS if marker in text]
        if markers and path.name not in ALLOWED_NETWORK_FILES:
            findings.append({"path": str(path), "severity": "high", "issue": "unexpected network-capable import or call", "markers": markers})
    checks = [
        {"name": "web binds localhost by default", "passed": "allow_lan" in (package / "webapp.py").read_text(encoding="utf-8")},
        {"name": "symlink following is opt-in", "passed": "allow_symlinks: bool = False" in (package / "core.py").read_text(encoding="utf-8")},
        {"name": "oversize skip option exists", "passed": "max_file_bytes" in (package / "core.py").read_text(encoding="utf-8")},
        {"name": "report redaction exists", "passed": "redact_paths" in (package / "reports.py").read_text(encoding="utf-8")},
        {
            "name": "API requests require a custom header when no token is set (CSRF/drive-by guard)",
            "passed": "CLIENT_HEADER" in (package / "webapp.py").read_text(encoding="utf-8"),
        },
        {
            "name": "file-serving endpoints check a server-registered read root, not a caller-supplied one",
            "passed": "_READ_ROOTS" in (package / "webapp.py").read_text(encoding="utf-8"),
        },
    ]
    return {
        "version": "security-check-v1",
        "passed": not findings and all(bool(check["passed"]) for check in checks),
        "checks": checks,
        "findings": findings,
        "notes": [
            "This is a static local-only guardrail check, not a full security audit.",
            "The local web server is allowed to serve localhost HTTP; scan/eval/train should not initiate outbound network calls.",
        ],
    }


def write_security_check(root: Path | str, output_path: Path | str) -> dict[str, object]:
    payload = build_security_check(root)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload

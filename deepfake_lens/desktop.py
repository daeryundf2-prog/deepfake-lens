"""Desktop mode (DESK, 2026-10-23): the GUI for a person who does not use a terminal.

``deepfake-lens desktop`` starts the local web app the way ``web`` does and adds what a double-click user needs:

- a **signing key** that is created once in the user's data folder (:func:`prepare_signing_key`) when
  ``DEEPFAKE_LENS_REPORT_KEY`` is not set, so reports are signed without an environment variable;
- a **free port** (8765 upward) and, when this tool is already listening there, only the browser is opened;
- the **browser** is opened on the GUI (the URL is printed when that fails);
- a native **folder dialog** (:func:`pick_folder`) the GUI can ask for through ``POST /api/pick-folder`` — the only
  way the read roots grow after start-up, and it needs a person at this machine;
- **shutdown** from the GUI (``POST /api/shutdown``) and an **idle exit** when no GUI heartbeat and no scan job
  arrived for ``idle_exit_minutes``.

Nothing here runs unless the server was built with ``desktop=True``; the ``web`` command is unchanged.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .signing import MIN_REPORT_KEY_BYTES, REPORT_KEY_ENV, ReportKeyError

logger = logging.getLogger(__name__)

KEY_FILE_NAME = "report-key"
PICK_FOLDER_STUB_ENV = "DEEPFAKE_LENS_PICK_FOLDER_STUB"
"""Tests and the GUI smoke set this instead of a dialog: a folder path (chosen), ``""`` (cancelled) or ``501`` (no dialog)."""
DEFAULT_PORT = 8765
PORT_TRIES = 21
DEFAULT_IDLE_EXIT_MINUTES = 20
DIALOG_PROMPT = "검사할 폴더를 고르십시오"

DIALOG_BUSY = "폴더 선택 창이 이미 열려 있습니다 — 그 창에서 폴더를 고르거나 닫으십시오"
DIALOG_UNAVAILABLE = "이 환경에서는 폴더 선택 창을 열 수 없습니다 — 경로를 직접 입력하십시오"
DIALOG_FAILED = "폴더 선택 창을 열지 못했습니다 — 경로를 직접 입력하십시오"
SHUTDOWN_WHILE_SCANNING = "검사가 진행 중입니다 — 검사를 중단한 뒤 종료하십시오"
KEY_FILE_DAMAGED = (
    "서명 키 파일이 손상되었습니다: {path} — 이 파일을 지우면 다음 실행 때 새 키를 만듭니다"
    "(그 전의 감정서는 지운 키로만 검증됩니다)"
)
NO_KEY_PLACE = "사용자 데이터 폴더를 정할 수 없어 서명 키를 만들지 못했습니다 — 보고서는 서명 없음으로 나갑니다"
ROOTS_FULL = "등록할 수 있는 폴더 수(64)를 넘었습니다 — 프로그램을 종료하고 다시 시작하십시오"


class DialogUnavailable(RuntimeError):
    """No folder dialog can be shown here (headless, no helper)."""


class DialogBusy(RuntimeError):
    """A dialog is already open."""


# ── signing key ───────────────────────────────────────────────────────────────────────────────────────────────


def desktop_key_file() -> Path | None:
    """``<user data dir>/report-key`` or None when the user data folder cannot be placed (relative HOME …)."""
    from .user_data import user_data_dir

    folder = user_data_dir()
    return folder / KEY_FILE_NAME if folder is not None else None


def prepare_signing_key() -> tuple[str, Path | None]:
    """Make a report key available in :data:`REPORT_KEY_ENV` and say where it came from.

    Returns ``("env", None)`` when the variable is already set, ``("file", path)`` when the key file was read,
    ``("created", path)`` when a new 64-hex-character key was written, ``("none", path_or_None)`` when there is no
    place for a key (reports go out unsigned). A key file that cannot be read, is empty or shorter than
    :data:`MIN_REPORT_KEY_BYTES` raises :class:`ReportKeyError` — it is never overwritten, because earlier reports
    verify only with it.
    """
    if os.environ.get(REPORT_KEY_ENV, "").strip():
        return "env", None
    path = desktop_key_file()
    if path is None:
        return "none", None
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8", errors="strict").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise ReportKeyError(KEY_FILE_DAMAGED.format(path=path)) from exc
        if len(content.encode("utf-8")) < MIN_REPORT_KEY_BYTES:
            raise ReportKeyError(KEY_FILE_DAMAGED.format(path=path))
        os.environ[REPORT_KEY_ENV] = content
        return "file", path
    key = secrets.token_hex(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key + "\n")
    except FileExistsError:
        return prepare_signing_key()  # another instance made it first
    except OSError as exc:
        logger.warning("desktop signing key could not be written to %s: %s", path, exc)
        return "none", path
    os.environ[REPORT_KEY_ENV] = key
    return "created", path


# ── folder dialog ─────────────────────────────────────────────────────────────────────────────────────────────

_POWERSHELL_DIALOG = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$owner = New-Object System.Windows.Forms.Form; $owner.TopMost = $true; $owner.ShowInTaskbar = $false; "
    "$owner.Opacity = 0; $owner.Show(); $owner.Activate(); "
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
    "$d.Description = '{prompt}'; $d.ShowNewFolderButton = $false; "
    "try {{ $d.UseDescriptionForTitle = $true }} catch {{ }}; "
    "if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {{ [Console]::Out.Write($d.SelectedPath) }}; "
    "$owner.Close()"
)


def _dialog_command(prompt: str) -> list[str] | None:
    """The helper process that shows a folder dialog on this platform, or None when there is none."""
    if os.name == "nt":
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if exe is None:
            return None
        return [exe, "-NoProfile", "-NonInteractive", "-STA", "-Command", _POWERSHELL_DIALOG.format(prompt=prompt.replace("'", "''"))]
    if sys.platform == "darwin":
        exe = shutil.which("osascript")
        if exe is None:
            return None
        script = 'POSIX path of (choose folder with prompt "' + prompt.replace('"', '\\"') + '")'
        return [exe, "-e", script]
    if shutil.which("zenity"):
        return ["zenity", "--file-selection", "--directory", "--title", prompt]
    if shutil.which("kdialog"):
        return ["kdialog", "--getexistingdirectory", str(Path.home()), "--title", prompt]
    return None


def pick_folder(prompt: str = DIALOG_PROMPT) -> Path | None:
    """Show the OS folder dialog and return the chosen folder, or None when the person cancelled.

    Raises :class:`DialogUnavailable` when no dialog can be shown. The stub variable
    :data:`PICK_FOLDER_STUB_ENV` replaces the dialog for tests.
    """
    stub = os.environ.get(PICK_FOLDER_STUB_ENV)
    if stub is not None:
        if stub == "501":
            raise DialogUnavailable(DIALOG_UNAVAILABLE)
        return Path(stub) if stub else None
    command = _dialog_command(prompt)
    if command is None:
        raise DialogUnavailable(DIALOG_UNAVAILABLE)
    try:
        done = subprocess.run(command, capture_output=True, timeout=None, check=False)
    except OSError as exc:
        raise DialogUnavailable(DIALOG_FAILED) from exc
    if done.returncode != 0:
        # osascript: "User canceled." is exit 1; zenity: cancel is exit 1 — both are a cancel, not a failure.
        logger.info("folder dialog ended with %s: %s", done.returncode, done.stderr[:200])
        return None
    text = os.fsdecode(done.stdout).strip().replace("\r", "").replace("\n", "")
    return Path(text) if text else None


# ── server-side state ─────────────────────────────────────────────────────────────────────────────────────────


@dataclass
class DesktopState:
    """What the desktop server knows beyond the plain web server (one per process)."""

    idle_exit_minutes: int = DEFAULT_IDLE_EXIT_MINUTES
    key_source: str = "none"
    key_file: Path | None = None
    last_activity: float = field(default_factory=time.monotonic)
    dialog_lock: threading.Lock = field(default_factory=threading.Lock)
    shutdown: Callable[[], None] | None = None
    shutting_down: bool = False

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def idle_for(self) -> float:
        return time.monotonic() - self.last_activity

    def should_exit(self, *, scanning: bool, now: float | None = None) -> bool:
        """True when idle exit is on, nothing is scanning and the GUI has been silent long enough."""
        if self.idle_exit_minutes <= 0 or scanning:
            return False
        idle = (now if now is not None else time.monotonic()) - self.last_activity
        return idle >= self.idle_exit_minutes * 60

    def pick_folder_once(self, prompt: str = DIALOG_PROMPT) -> Path | None:
        """One dialog at a time: a second request while one is open raises :class:`DialogBusy`."""
        if not self.dialog_lock.acquire(blocking=False):
            raise DialogBusy(DIALOG_BUSY)
        try:
            return pick_folder(prompt)
        finally:
            self.dialog_lock.release()


STATE: DesktopState | None = None
"""Set by :func:`webapp.build_server` when ``desktop=True``; None in the plain web server."""


# ── port and browser ──────────────────────────────────────────────────────────────────────────────────────────


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def this_tool_listens(port: int, host: str = "127.0.0.1") -> bool:
    """True when a Deepfake Lens server already answers ``/api/stats`` on the port (the client header is sent)."""
    import json
    import urllib.error
    import urllib.request

    from .webapp import CLIENT_HEADER, CLIENT_HEADER_VALUE

    request = urllib.request.Request(f"http://{host}:{port}/api/stats", headers={CLIENT_HEADER: CLIENT_HEADER_VALUE})
    try:
        with urllib.request.urlopen(request, timeout=2) as answer:
            payload = json.loads(answer.read().decode("utf-8", "replace"))
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "ok" and "version" in payload


def choose_port(start: int = DEFAULT_PORT, tries: int = PORT_TRIES, host: str = "127.0.0.1") -> tuple[int, bool]:
    """``(port, already_running)``: the first free port from ``start``, or the port where this tool already runs."""
    for port in range(start, min(start + tries, 65536)):
        if port_is_free(port, host):
            return port, False
        if this_tool_listens(port, host):
            return port, True
    raise OSError(f"{start}부터 {tries}개 포트가 모두 사용 중입니다 — 다른 프로그램을 닫거나 --port를 지정하십시오")


def open_browser(url: str) -> bool:
    import webbrowser

    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:  # noqa: BLE001 - no browser is a message, not a crash
        return False


def machine_notes() -> list[str]:
    """One-line warnings about where and on what this runs (x64 only; OneDrive folders are slow and unverified)."""
    import platform

    notes: list[str] = []
    arch = platform.machine().lower()
    if os.name == "nt" and arch not in ("amd64", "x86_64"):
        notes.append(f"이 PC는 x64가 아닙니다({platform.machine()}) — 꾸러미는 x64 Windows에서만 검증했습니다")
    here = str(Path(sys.argv[0]).resolve().parent) if sys.argv and sys.argv[0] else ""
    if "onedrive" in here.lower():
        notes.append("프로그램이 OneDrive 폴더 안에 있습니다 — 느리고 검증되지 않은 위치입니다. C:\\DeepfakeLens 같은 로컬 폴더로 옮기십시오")
    return notes


# ── the command ───────────────────────────────────────────────────────────────────────────────────────────────


def run_desktop(
    *,
    folder: Path | None,
    port: int | None,
    no_browser: bool,
    idle_exit_minutes: int,
    models_dir: Path | None,
    key_source: str,
    key_file: Path | None,
    log_path: Path | None,
) -> int:
    """Start the desktop server, open the browser, run until shutdown/idle/Ctrl-C. Returns the exit code."""
    from .webapp import build_server

    host = "127.0.0.1"
    try:
        chosen, running = choose_port(port or DEFAULT_PORT, 1 if port else PORT_TRIES, host)
    except OSError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    url = f"http://{host}:{chosen}/gui"
    if running:
        print(f"Deepfake Lens가 이미 실행 중입니다: {url}", flush=True)
        if not no_browser and not open_browser(url):
            print(f"브라우저를 열지 못했습니다 — 주소창에 붙여 넣으십시오: {url}", flush=True)
        return 0
    state = DesktopState(idle_exit_minutes=idle_exit_minutes, key_source=key_source, key_file=key_file)
    server = build_server(host, chosen, default_folder=folder, models_dir=models_dir, desktop=state)
    state.shutdown = server.shutdown

    thread = threading.Thread(target=server.serve_forever, name="deepfake-lens-desktop", daemon=True)
    thread.start()
    lines = [
        "Deepfake Lens 데스크톱 모드",
        f"  화면 주소: {url}",
        "  이 창을 닫거나 Ctrl-C를 누르면 프로그램이 꺼집니다. 화면의 '종료' 단추로도 끌 수 있습니다.",
    ]
    if folder is not None:
        lines.append(f"  검사할 폴더: {folder}")
    lines.append("  폴더는 화면의 '검사할 폴더 고르기'로 고릅니다.")
    if key_source == "created":
        lines.append(f"  보고서 서명 키를 새로 만들었습니다: {key_file} (이 PC의 이 계정에서만 검증됩니다)")
    elif key_source == "file":
        lines.append(f"  보고서 서명 키: {key_file}")
    elif key_source == "env":
        lines.append(f"  보고서 서명 키: 환경 변수 {REPORT_KEY_ENV}")
    else:
        lines.append(f"  {NO_KEY_PLACE}")
    if log_path is not None:
        lines.append(f"  문제가 생기면 이 로그 파일을 보내 주십시오: {log_path}")
    if idle_exit_minutes > 0:
        lines.append(f"  {idle_exit_minutes}분 동안 화면이 열려 있지 않고 검사도 없으면 스스로 꺼집니다.")
    lines.extend("  주의: " + note for note in machine_notes())
    print("\n".join(lines), flush=True)
    if not no_browser and not open_browser(url):
        print(f"브라우저를 열지 못했습니다 — 브라우저 주소창에 이 주소를 붙여 넣으십시오:\n  {url}", flush=True)

    from .scan_jobs import STORE

    try:
        while thread.is_alive():
            thread.join(1.0)
            if state.shutting_down:
                break
            if state.should_exit(scanning=bool(STORE.running_async_jobs())):
                print(f"{idle_exit_minutes}분 동안 사용이 없어 종료합니다.", flush=True)
                server.shutdown()
                break
    finally:
        try:
            server.server_close()
        except OSError:
            pass
    print("Deepfake Lens를 종료했습니다.", flush=True)
    return 0

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
import ntpath
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from collections.abc import Mapping
from pathlib import Path
from typing import Callable

from .signing import MIN_REPORT_KEY_BYTES, REPORT_KEY_ENV, ReportKeyError
from .user_data import user_data_dir

logger = logging.getLogger(__name__)

KEY_FILE_NAME = "report-key"
PICK_FOLDER_STUB_ENV = "DEEPFAKE_LENS_PICK_FOLDER_STUB"
"""Tests and the GUI smoke set this instead of a dialog: a folder path (chosen), ``""`` (cancelled) or ``501`` (no dialog)."""
DEFAULT_PORT = 8765
PORT_TRIES = 21
DEFAULT_IDLE_EXIT_MINUTES = 20
DIALOG_PROMPT = "검사할 폴더를 고르십시오"

DIALOG_BUSY = "폴더 선택 창이 이미 열려 있습니다 — 그 창에서 폴더를 고르거나 닫으십시오"
NO_DIALOG_WAY_OUT = (
    "프로그램을 종료한 뒤 검사할 폴더를 'Deepfake Lens.cmd' 아이콘에 끌어 놓아 다시 시작하십시오"
    "(터미널에서는 --folder <폴더>)"
)
DIALOG_UNAVAILABLE = f"이 환경에서는 폴더 선택 창을 열 수 없습니다 — {NO_DIALOG_WAY_OUT}"
DIALOG_FAILED = f"폴더 선택 창을 열지 못했습니다 — {NO_DIALOG_WAY_OUT}"
SHUTDOWN_WHILE_SCANNING = "검사가 진행 중입니다 — 검사를 중단한 뒤 종료하십시오"
KEY_FILE_DAMAGED = (
    "서명 키 파일이 손상되었습니다: {path} — 이 파일을 지우면 다음 실행 때 새 키를 만듭니다"
    "(그 전의 감정서는 지운 키로만 검증됩니다)"
)
NO_KEY_PLACE = "사용자 데이터 폴더를 정할 수 없어 서명 키를 만들지 못했습니다 — 보고서는 서명 없음으로 나갑니다"
ROOTS_FULL = "등록할 수 있는 폴더 수(64)를 넘었습니다 — 프로그램을 종료하고 다시 시작하십시오"
DIALOG_TIMEOUT_SECONDS = 600
DIALOG_TIMED_OUT = "폴더 선택 창이 10분 동안 응답이 없어 닫았습니다 — 다시 누르십시오"
DIALOG_POLICY = f"폴더 선택 창을 열지 못했습니다(이 PC의 보안 정책이 PowerShell을 제한합니다) — {NO_DIALOG_WAY_OUT}"
FOLDER_TOO_WIDE = (
    "너무 넓은 폴더입니다({what}) — 이 도구의 기록·임시 파일까지 검사 대상이 되어 증거 폴더 원칙이 깨집니다. "
    "증거 파일이 든 폴더를 고르십시오"
)
BUNDLE_INSIDE_NOTE = "이 폴더 안에 프로그램 폴더가 있습니다 — 프로그램 파일도 검사 대상이 됩니다(증거만 든 폴더를 고르는 것이 좋습니다)"
ONEDRIVE_NOTE = (
    "OneDrive 폴더입니다 — 온라인 전용 파일은 내려받으면서 검사되고, OneDrive가 꺼져 있으면 건너뜁니다. "
    "증거는 로컬 폴더로 복사해 검사하는 것이 안전합니다"
)
ALREADY_RUNNING_NO_BROWSER_EXIT = 3
WAKE_GAP_SECONDS = 60.0
"""A loop tick this much later than the last one means the machine slept (3.12's Windows monotonic clock counts sleep)."""


class DialogUnavailable(RuntimeError):
    """No folder dialog can be shown here (headless, no helper)."""


class DialogBusy(RuntimeError):
    """A dialog is already open."""


class FolderTooWide(ValueError):
    """The chosen folder would contain this tool's own folders (home, a drive root, AppData, temp, the bundle)."""


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
    return _load_or_create_key(path, attempts=3)


def _load_or_create_key(path: Path, *, attempts: int) -> tuple[str, Path | None]:
    if attempts <= 0:
        raise ReportKeyError(KEY_FILE_DAMAGED.format(path=path))
    if os.path.lexists(path) and not path.is_file():
        raise ReportKeyError(KEY_FILE_DAMAGED.format(path=path))  # a broken link, a directory, a device
    if path.is_file():
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
        # Written whole into a private temp file, then linked into place only when no key exists yet: a second
        # instance started at the same moment reads a complete file or makes its own, never a half-written one.
        temp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key + "\n")
        try:
            os.link(temp, path)
        except FileExistsError:
            os.unlink(temp)
            return _load_or_create_key(path, attempts=attempts - 1)  # another instance made it first
        except OSError:
            # a file system without hard links (exFAT): rename into place only when nothing is there yet — a race
            # between two first starts is then possible but both read back the same file afterwards (B9 residual)
            if os.path.lexists(path):
                os.unlink(temp)
                return _load_or_create_key(path, attempts=attempts - 1)
            os.replace(temp, path)
        else:
            os.unlink(temp)
        _protect_key_file(path)
    except OSError as exc:
        logger.warning("desktop signing key could not be written to %s: %s", path, exc)
        return "none", path
    os.environ[REPORT_KEY_ENV] = key
    return "created", path


def _protect_key_file(path: Path) -> None:
    """Windows: the file gets an owner-only DACL (0o600 means nothing there; the inherited ACL lets Administrators read)."""
    if os.name != "nt":
        return
    try:
        from .windows_acl import make_owner_only

        if not make_owner_only(str(path)):
            logger.warning("desktop signing key %s keeps the inherited ACL (owner-only DACL could not be set)", path)
    except Exception:  # noqa: BLE001 - the key still works; the ACL is a hardening step
        logger.warning("desktop signing key %s keeps the inherited ACL", path, exc_info=True)


# ── folder dialog ─────────────────────────────────────────────────────────────────────────────────────────────

# The helper prints `OK:<path>` or `CANCEL` and exits 0; a PowerShell that cannot make WinForms objects
# (ConstrainedLanguage under AppLocker/WDAC) exits 3 before trying, so a policy block is never read as a cancel.
_POWERSHELL_DIALOG = (
    "if ($ExecutionContext.SessionState.LanguageMode -ne 'FullLanguage') {{ exit 3 }}; "
    "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false; "
    "Add-Type -AssemblyName System.Windows.Forms; "
    "$owner = New-Object System.Windows.Forms.Form; $owner.TopMost = $true; $owner.Text = '{prompt}'; "
    "$owner.Opacity = 0; $owner.Show(); $owner.Activate(); "
    "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
    "$d.Description = '{prompt}'; $d.ShowNewFolderButton = $false; "
    "try {{ $d.UseDescriptionForTitle = $true }} catch {{ }}; "
    "if ($d.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {{ [Console]::Out.Write('OK:' + $d.SelectedPath) }} "
    "else {{ [Console]::Out.Write('CANCEL') }}; "
    "$owner.Close()"
)
POLICY_EXIT = 3


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
    import subprocess

    from .shutdown import ShuttingDown, run_child

    try:
        done = run_child(command, capture_output=True, timeout=DIALOG_TIMEOUT_SECONDS)  # a tracked child (R15-1)
    except ShuttingDown:
        return None
    except subprocess.TimeoutExpired as exc:
        raise DialogUnavailable(DIALOG_TIMED_OUT) from exc
    except OSError as exc:
        raise DialogUnavailable(DIALOG_FAILED) from exc
    return dialog_result(command[0], done.returncode, done.stdout, done.stderr)


def dialog_result(program: str, returncode: int, stdout: bytes, stderr: bytes) -> Path | None:
    """The chosen folder, None for a cancel, :class:`DialogUnavailable` for anything else.

    Windows (powershell): exit 0 with ``OK:<path>`` or ``CANCEL``; exit :data:`POLICY_EXIT` is the language-mode
    refusal; any other end (a blocked Add-Type, a killed process) is a failure — it used to be read as a cancel.
    macOS (osascript): exit 1 with "User canceled" (-128) is a cancel. zenity/kdialog: exit 1 is a cancel.
    """
    text = os.fsdecode(stdout).lstrip("\ufeff").strip().replace("\r", "").replace("\n", "")
    err = os.fsdecode(stderr)[:300]
    name = Path(program).name.lower()
    if name.startswith(("powershell", "pwsh")):
        if returncode == POLICY_EXIT:
            raise DialogUnavailable(DIALOG_POLICY)
        if returncode == 0 and text == "CANCEL":
            return None
        if returncode == 0 and text.startswith("OK:"):
            chosen = text[3:].strip()
            return Path(chosen) if chosen else None
        logger.warning("folder dialog helper failed (exit %s): %s", returncode, err)
        raise DialogUnavailable(DIALOG_FAILED)
    if returncode == 0:
        raw = os.fsdecode(stdout).lstrip("\ufeff").rstrip("\r\n")  # only the line end: a folder name may end in a space
        return Path(raw) if raw.strip() else None
    if returncode == 1 and (name != "osascript" or "-128" in err or "canceled" in err.lower() or "cancelled" in err.lower()):
        return None  # a cancel
    logger.warning("folder dialog helper failed (exit %s): %s", returncode, err)
    raise DialogUnavailable(DIALOG_FAILED)


def folder_too_wide(folder: Path) -> str | None:
    """Why ``folder`` must not become a read root: it would contain this tool's own folders (or everything)."""
    import tempfile

    from .native_path import tool_cache_base
    from .user_data import user_data_dir

    try:
        chosen = folder.resolve()
    except OSError:
        return None
    anchor = chosen.anchor
    if anchor and chosen == Path(anchor):
        return "드라이브 전체"
    try:
        home = Path.home().resolve()
    except (RuntimeError, OSError):
        home = None
    if home is not None and chosen == home:
        return "사용자 폴더 전체"
    from .cli_logging import default_log_dir
    from .scan_jobs import store_folder

    inside: list[tuple[str, Path | None]] = [
        ("이 도구의 기록 폴더", user_data_dir()),
        ("이 도구의 캐시·로그 폴더", tool_cache_base()),
        ("이 도구의 로그 폴더", default_log_dir()),
        ("이 도구의 검사 작업 보관 폴더", store_folder()[0]),
        ("임시 폴더", Path(tempfile.gettempdir())),
    ]
    for what, inner in inside:
        if inner is None:
            continue
        try:
            inner = inner.resolve()
        except OSError:
            continue
        if inner == chosen or chosen in inner.parents:
            return f"{what}를 품습니다"
    return None


def folder_note(folder: Path) -> str | None:
    """A caution about the chosen folder shown next to the path (OneDrive placeholders; the bundle inside it)."""
    notes = []
    if "onedrive" in str(folder).lower():
        notes.append(ONEDRIVE_NOTE)
    try:
        prefix = Path(sys.prefix).resolve()
        chosen = folder.resolve()
        if prefix == chosen or chosen in prefix.parents:
            notes.append(BUNDLE_INSIDE_NOTE)
    except OSError:
        pass
    return " ".join(notes) or None


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


def set_exclusive_bind(sock: socket.socket) -> None:
    """Windows: SO_EXCLUSIVEADDRUSE — SO_REUSEADDR there lets a second socket bind a port that is already listening
    (two servers on 8765, requests split between them). Elsewhere SO_REUSEADDR only skips TIME_WAIT."""
    if os.name == "nt":
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        return
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """A bind probe and a connect probe: on macOS/Linux SO_REUSEADDR lets a loopback bind succeed over another
    program's wildcard listener (0.0.0.0:port), so something that answers a connect is never "free"."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        set_exclusive_bind(probe)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as peer:
        peer.settimeout(0.5)
        try:
            peer.connect((host, port))
        except OSError:
            return True
    return False


def instance_user() -> str:
    """A non-secret tag of the account running this process (desktop /api/stats carries it; another user's
    instance on a shared loopback — an RDP host — is not ours to reuse)."""
    import getpass
    import hashlib

    try:
        name = getpass.getuser()
    except Exception:  # noqa: BLE001 - no account name: the tag is still stable per process environment
        name = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    return hashlib.sha256(name.encode("utf-8", "replace")).hexdigest()[:12]


PROBE_TIMEOUT_SECONDS = 5.0


def this_tool_listens(port: int, host: str = "127.0.0.1") -> bool:
    """True when this user's Deepfake Lens **desktop** server already answers ``/api/stats`` on the port.

    A plain ``web`` server (no desktop routes) or another user's instance (RDP host: the loopback port space is
    shared) does not count — the launcher then moves to the next port instead of opening a GUI without the
    desktop controls or someone else's evidence.
    """
    import json
    import urllib.error
    import urllib.request

    from .webapp import CLIENT_HEADER, CLIENT_HEADER_VALUE

    request = urllib.request.Request(f"http://{host}:{port}/api/stats", headers={CLIENT_HEADER: CLIENT_HEADER_VALUE})
    # No proxy: a corporate HTTP_PROXY / registry proxy must not be asked about 127.0.0.1 (it would answer for it).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=PROBE_TIMEOUT_SECONDS) as answer:
            payload = json.loads(answer.read().decode("utf-8", "replace"))
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return (
        isinstance(payload, dict) and payload.get("status") == "ok" and "version" in payload
        and payload.get("desktop") is True and payload.get("instance_user") == instance_user()
    )


def choose_port(start: int = DEFAULT_PORT, tries: int = PORT_TRIES, host: str = "127.0.0.1") -> tuple[int, bool]:
    """``(port, already_running)``: the first free port from ``start``, or the port where this tool already runs."""
    for port in range(start, min(start + tries, 65536)):
        if this_tool_listens(port, host):  # first: on Windows a free-port probe can succeed on a port in use
            return port, True
        if port_is_free(port, host):
            return port, False
    raise OSError(f"{start}부터 {tries}개 포트가 모두 사용 중입니다 — 다른 프로그램을 닫거나 --port를 지정하십시오")


def open_browser(url: str) -> bool:
    import webbrowser

    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:  # noqa: BLE001 - no browser is a message, not a crash
        return False


def find_app_browser(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    exists: Callable[[str], bool] = os.path.exists,
    which: Callable[[str], str | None] = shutil.which,
) -> str | None:
    """The first installed Edge or Chrome executable (for an app window), or None."""
    platform = platform or sys.platform
    env: Mapping[str, str] = os.environ if environ is None else environ
    candidates: list[str] = []
    if platform == "win32":
        bases = [env.get(name) for name in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        for relative in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            candidates += [ntpath.join(base, relative) for base in bases if base]
    elif platform == "darwin":
        apps = [("Microsoft Edge.app", "Microsoft Edge"), ("Google Chrome.app", "Google Chrome")]
        roots = ["/Applications"]
        if env.get("HOME"):
            roots.append(os.path.join(env["HOME"], "Applications"))
        for root in roots:
            candidates += [os.path.join(root, app, "Contents", "MacOS", exe) for app, exe in apps]
    for path in candidates:
        if exists(path):
            return path
    for name in ("microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = which(name)
        if found:
            return found
    return None


def open_app_window(url: str, *, profile_dir: Path, executable: str, popen: Callable[..., object] = subprocess.Popen) -> bool:
    """Start Edge/Chrome as a window of its own (its own profile); False when it cannot be started."""
    argv = [
        executable,
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1440,900",
    ]
    kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        popen(argv, **kwargs)
    except OSError:
        return False
    return True


def open_gui(url: str, *, browser_tab: bool) -> bool:
    """The GUI in an app window of its own profile; the default browser's tab when there is none or it fails."""
    if not browser_tab:
        base = user_data_dir()
        profile: Path | None = None
        if base is not None:
            profile = base / "app-window"
            try:
                profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            except OSError:
                profile = None
        executable = find_app_browser() if profile is not None else None
        if profile is not None and executable is not None and open_app_window(url, profile_dir=profile, executable=executable):
            return True
    return open_browser(url)


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


def prepare_console() -> None:
    """Windows console: QuickEdit off (a click froze every thread that wrote to the console until Enter — microsoft/terminal#34)
    and a close-window handler that removes the session temp folder (Python signal handlers never run for CTRL_CLOSE_EVENT)."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            global _CONSOLE_MODE_BEFORE
            _CONSOLE_MODE_BEFORE = mode.value
            kernel32.SetConsoleMode(handle, (mode.value & ~0x40) | 0x80)  # -ENABLE_QUICK_EDIT_MODE +ENABLE_EXTENDED_FLAGS
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)  # type: ignore[attr-defined]

        def on_console_event(event: int) -> int:
            if event in (2, 5, 6):  # CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT
                from .native_path import shutdown_session

                try:
                    shutdown_session()
                except Exception:  # noqa: BLE001 - the process ends anyway
                    pass
            return 0  # the default handling continues (the process ends)

        global _CONSOLE_HANDLER
        _CONSOLE_HANDLER = handler_type(on_console_event)  # kept alive for the process lifetime
        kernel32.SetConsoleCtrlHandler(_CONSOLE_HANDLER, 1)
    except Exception:  # noqa: BLE001 - a console without these calls (pythonw, a pipe) is fine
        logger.info("console preparation skipped", exc_info=True)


_CONSOLE_HANDLER: object | None = None
_CONSOLE_MODE_BEFORE: int | None = None


def restore_console() -> None:
    """Put QuickEdit back the way it was (a user who started us from their own cmd window keeps their setting)."""
    if os.name != "nt" or _CONSOLE_MODE_BEFORE is None:
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-10), _CONSOLE_MODE_BEFORE)
    except Exception:  # noqa: BLE001
        pass


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
    browser_tab: bool = False,
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
        if not no_browser and not open_gui(url, browser_tab=browser_tab):
            print(f"브라우저를 열지 못했습니다 — 주소창에 붙여 넣으십시오: {url}", flush=True)
            return ALREADY_RUNNING_NO_BROWSER_EXIT  # the launcher pauses on this code so the address stays readable
        return 0
    prepare_console()
    from .pdf_backend import pymupdf_available

    pymupdf_available()  # the first /api/stats must answer within the probe timeout of a second instance
    state = DesktopState(idle_exit_minutes=idle_exit_minutes, key_source=key_source, key_file=key_file)
    server = None
    for attempt in range(PORT_TRIES if not port else 1):
        try:
            server = build_server(host, chosen, default_folder=folder, models_dir=models_dir, desktop=state)
            break
        except OSError as exc:  # the port was taken between the probe and the bind
            from .error_text import read_error_ko

            logger.info("port %s could not be bound (%s); trying the next", chosen, exc)
            # A second double-click during a slow first start: the other instance may be ours and still importing
            # (the first /api/stats answer can take longer than the probe) — ask it again for a while.
            for _ in range(10):
                if this_tool_listens(chosen, host):
                    print(f"Deepfake Lens가 이미 실행 중입니다: {url}", flush=True)
                    if not no_browser and not open_gui(url, browser_tab=browser_tab):
                        print(f"브라우저를 열지 못했습니다 — 주소창에 붙여 넣으십시오: {url}", flush=True)
                    return 0
                time.sleep(1.0)
            if port or attempt == PORT_TRIES - 1:
                print(f"오류: 포트 {chosen}을(를) 열 수 없습니다({read_error_ko(exc)}) — 다른 프로그램을 닫거나 --port를 지정하십시오", file=sys.stderr)
                return 2
            chosen += 1
            url = f"http://{host}:{chosen}/gui"
    assert server is not None
    state.shutdown = server.shutdown

    thread = threading.Thread(target=server.serve_forever, name="deepfake-lens-desktop", daemon=True)
    thread.start()
    lines = [
        "Deepfake Lens 데스크톱 모드",
        f"  화면 주소: {url}",
        "  이 창을 닫거나 Ctrl-C를 누르면 프로그램이 꺼집니다. 화면의 '프로그램 종료' 단추로도 끌 수 있습니다.",
    ]
    if folder is not None:
        lines.append(f"  검사할 폴더: {folder}")
    lines.append("  폴더는 화면의 '검사할 폴더 고르기'로 고릅니다.")
    if key_source == "created":
        lines.append(f"  보고서 서명 키를 새로 만들었습니다: {key_file} (이 키 파일을 가진 계정에서만 서명을 검증할 수 있습니다)")
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
    if not no_browser and not open_gui(url, browser_tab=browser_tab):
        print(f"브라우저를 열지 못했습니다 — Edge나 Chrome의 주소창에 이 주소를 붙여 넣으십시오:\n  {url}", flush=True)
    print("  화면이 비어 있거나 Internet Explorer가 열리면 위 주소를 Edge나 Chrome에 붙여 넣으십시오. 이 창 안을 클릭했다면 Enter를 누르십시오.", flush=True)

    from .scan_jobs import STORE

    last_tick = time.monotonic()
    try:
        while thread.is_alive():
            thread.join(1.0)
            now = time.monotonic()
            if now - last_tick > WAKE_GAP_SECONDS:
                state.touch()  # the machine slept (or the process was suspended): not the examiner's idleness
            last_tick = now
            if state.shutting_down:
                break
            if state.should_exit(scanning=bool(STORE.running_async_jobs())):
                print(f"{idle_exit_minutes}분 동안 사용이 없어 종료합니다.", flush=True)
                server.shutdown()
                break
    except KeyboardInterrupt:
        server.shutdown()
        thread.join(5.0)
        raise  # the CLI's interrupt path: cleanup, 중단됨 line, exit 130
    finally:
        thread.join(5.0)  # serve_forever has returned before the socket is closed (a select on a closed socket is WinError 10038)
        try:
            server.server_close()
        except OSError:
            pass
        restore_console()
    print("Deepfake Lens를 종료했습니다.", flush=True)
    return 0

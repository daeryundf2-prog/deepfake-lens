"""Build the double-click desktop bundles (DESK-8, docs/gui/plan_desktop_mode.md §5). Not a gate script.

``--platform windows`` makes ``DeepfakeLens-win64/`` and ``DeepfakeLens-win64.zip``:

1. python.org's embeddable CPython 3.12.10 for x64 (URL and sha256 pinned below; a mismatch stops the build),
   unpacked to ``python/``;
2. ``python312._pth`` rewritten to add ``Lib\\site-packages`` and ``import site`` (the embeddable build turns
   ``site`` off — without this nothing installed is importable);
3. this repository's wheel (``pip wheel . --no-deps``) and the pinned wheel set :data:`WINDOWS_WHEELS` installed
   with ``pip install --target python/Lib/site-packages --platform win_amd64 --python-version 3.12
   --only-binary=:all: --no-deps``. Because of ``--no-deps`` the set is explicit; :func:`missing_requirements`
   reads every installed dist's ``Requires-Dist`` (markers evaluated for win32 / CPython 3.12, extras off) and the
   build fails when a required dist is not installed; ``site-packages/bin/`` and ``direct_url.json`` are deleted
   (RISK B22); the C++ runtime ``msvcp140*.dll`` from the pinned msvc-runtime wheel goes into ``python/`` and every
   ``.pyd``/``.dll``'s import table is checked (RISK A1); the embeddable's ``vcruntime140*.dll`` (14.42) are replaced
   by the same wheel's copies so the C runtime set has one version (REV2-D7); site-packages is precompiled with the
   build machine's CPython 3.12 (``compileall --invalidation-mode checked-hash`` with the build path stripped,
   RISK A7, REV2-D5 D6);
4. ``Deepfake Lens.cmd``, ``문제 확인.cmd``, ``읽어보세요.txt``, ``VERSION.txt``;
5. a zip with ``/`` separators and UTF-8 names;
6. a check of the folder (python.exe, the ``._pth`` lines, ``deepfake_lens/gui.js``, ``pymupdf``) and its size.

``--platform macos`` makes ``DeepfakeLens-mac/``: ``Deepfake Lens.command`` (creates ``.venv`` with python3.12 or
python3.13 on first run, installs from the bundled ``wheels/`` with ``--no-index``, then runs
``python -m deepfake_lens desktop``) and ``wheels/`` with this repository's wheel plus the same pins for macOS arm64
(CPython 3.12 and 3.13 — numpy 2.5.4 has no 3.11 wheels).

Network: python.org (the embeddable zip) and PyPI (through pip) only. Downloads are cached under ``--cache``.
The Windows bundle cannot be run on the build machine; the first run is QA-SYS-4's bundle path.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from email.parser import HeaderParser
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[1]

EMBED_VERSION = "3.12.10"
EMBED_NAME = f"python-{EMBED_VERSION}-embed-amd64.zip"
EMBED_URL = f"https://www.python.org/ftp/python/{EMBED_VERSION}/{EMBED_NAME}"
EMBED_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
"""sha256 of the file downloaded from python.org over HTTPS on 2026-10-23 (11,133,606 bytes)."""
PTH_NAME = "python312._pth"
SITE_PACKAGES_PTH = "Lib\\site-packages"

TOP_LEVEL = ("pymupdf==1.28.2", "numpy==2.5.4", "Pillow==12.3.0", "c2pa-python==0.38.0", "py7zr==1.1.4", "syhwp==0.0.8", "olefile==0.47")
"""What the bundle is for: PDF reports, pixel checks, C2PA, .7z and .hwp. The rest of :data:`WINDOWS_WHEELS` is their
dependencies, resolved once with ``pip download`` for win_amd64 / cp312 on 2026-10-23 and pinned here."""
DEPENDENCIES = (
    # c2pa-python
    "toml==0.10.2", "cryptography==50.0.2", "cffi==2.1.1", "pycparser==3.12",
    "requests==2.34.2", "charset-normalizer==3.5.2", "idna==3.20", "urllib3==2.8.0", "certifi==2026.10.19",
    # py7zr
    "texttable==1.7.2", "pycryptodomex==3.24.1", "brotli==1.2.1", "psutil==7.2.2", "backports-zstd==1.8.0",
    "pyppmd==1.3.1", "pybcj==1.0.8", "multivolumefile==0.2.3", "inflate64==1.0.4",
)
WINDOWS_WHEELS = TOP_LEVEL + DEPENDENCIES
MAC_PYTHONS = ("3.12", "3.13")
MAC_PLATFORM = "macosx_14_0_arm64"

MSVC_RUNTIME_VERSION = "14.51.36231"
MSVC_RUNTIME_WHEEL = f"msvc_runtime-{MSVC_RUNTIME_VERSION}-cp312-cp312-win_amd64.whl"
MSVC_RUNTIME_SHA256 = "89f70d1a37bb64164cdb09d02dd82d6b1400e1310be2995b4ed5a9f23efe2222"
"""sha256 of the wheel from ``pip download msvc-runtime==14.51.36231 --platform win_amd64 --python-version 3.12``
on 2026-10-23 (2,052,033 bytes; the same digest PyPI's JSON API lists for that file)."""
MSVC_RUNTIME_DLLS = ("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll")
"""RISK A1: the C++ runtime that PyMuPDF's ``_mupdf.pyd``/``mupdfcpp64.dll``/``_extra.pyd`` import. The embeddable
Python ships only ``vcruntime140.dll``/``vcruntime140_1.dll``; these go into ``python\\`` (the application folder,
searched for every extension's imports) from the wheel's ``msvc_runtime-<v>.data/data/``."""
VCRUNTIME_DLLS = ("vcruntime140.dll", "vcruntime140_1.dll")
"""REV2-D7: the embeddable 3.12.10 ships these as 14.42; ``msvcp140.dll`` 14.51 from the wheel expects a vcruntime at
least as new as itself (Microsoft: the runtime DLLs of one redistributable version go together). The wheel carries
them only under ``msvc_runtime-<v>.data/data/Scripts/``; they overwrite the embeddable's copies in ``python\\``.
(``python.cat`` then no longer matches those two files — Windows does not check that catalog when loading DLLs.)"""

SYSTEM_DLLS = frozenset({
    "kernel32.dll", "kernelbase.dll", "ntdll.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll", "ole32.dll",
    "oleaut32.dll", "ws2_32.dll", "ucrtbase.dll", "msvcrt.dll", "crypt32.dll", "bcrypt.dll", "ncrypt.dll", "secur32.dll",
    "shlwapi.dll", "version.dll", "winmm.dll", "comdlg32.dll", "comctl32.dll", "iphlpapi.dll", "psapi.dll", "rpcrt4.dll",
    "setupapi.dll", "userenv.dll", "dbghelp.dll", "normaliz.dll", "wldap32.dll", "winhttp.dll", "wininet.dll",
    "imm32.dll", "powrprof.dll", "pdh.dll", "netapi32.dll", "mswsock.dll", "dnsapi.dll", "cfgmgr32.dll", "propsys.dll",
    "shcore.dll", "dwmapi.dll", "uxtheme.dll", "opengl32.dll", "dxgi.dll", "d3d11.dll", "d3d12.dll", "dwrite.dll",
    "d2d1.dll", "windowscodecs.dll", "cabinet.dll", "wtsapi32.dll", "wintrust.dll", "msimg32.dll", "usp10.dll",
    "bcryptprimitives.dll", "ntmarta.dll", "sspicli.dll", "mpr.dll", "mscoree.dll", "oleacc.dll", "winspool.drv", "msi.dll",
})
"""DLLs every supported Windows 10/11 x64 has in System32 (RISK A1's import check accepts them by name)."""
SYSTEM_DLL_PREFIXES = ("api-ms-win-",)
"""API sets every Windows 10/11 resolves. ``ext-ms-`` extension sets are optional per edition (REV2-D13) — a module
importing one is reported, not waved through."""

PYC_MAGIC_312 = (3531).to_bytes(2, "little") + b"\r\n"
"""``importlib.util.MAGIC_NUMBER`` of CPython 3.12.x (3531 for the whole 3.12 series): the bytecode of the build
machine's python3.12 is what the embeddable 3.12.10 reads (RISK A7)."""

WIN_DIR = "DeepfakeLens-win64"
MAC_DIR = "DeepfakeLens-mac"
WIN_LAUNCHER = "Deepfake Lens.cmd"
WIN_DOCTOR = "문제 확인.cmd"
WIN_README = "읽어보세요.txt"
MAC_LAUNCHER = "Deepfake Lens.command"

ALREADY_RUNNING_NO_BROWSER_EXIT = 3
"""``deepfake_lens.desktop.ALREADY_RUNNING_NO_BROWSER_EXIT`` (a test keeps them equal): an instance already runs but
the browser could not be opened — the console printed the address, so the launcher pauses without an error line
(REV2-D9)."""

DEFAULT_CACHE = Path(os.environ.get("TMPDIR", "/tmp")) / "deepfake-lens-bundle-cache"


class BuildError(RuntimeError):
    """A build step failed; the message is shown as is."""


# ── pure parts (unit-tested without network) ──────────────────────────────────────────────────────────────────


def rewrite_pth(text: str) -> str:
    """The embeddable ``._pth`` with ``Lib\\site-packages`` after ``.`` and ``import site`` turned on (idempotent)."""
    lines = [line.rstrip("\r") for line in text.splitlines()]
    if not any(line.strip().lower() == SITE_PACKAGES_PTH.lower() for line in lines):
        at = next((i + 1 for i, line in enumerate(lines) if line.strip() == "."), None)
        if at is None:
            lines.append(".")
            at = len(lines)
        lines.insert(at, SITE_PACKAGES_PTH)
    out: list[str] = []
    has_site = False
    for line in lines:
        if line.strip().lstrip("#").strip() == "import site":
            if not has_site:
                out.append("import site")
                has_site = True
            continue
        out.append(line)
    if not has_site:
        out.append("import site")
    return "\r\n".join(out) + "\r\n"


def _crlf(lines: Iterable[str]) -> str:
    return "\r\n".join(lines) + "\r\n"


TEMP_MARKERS = ("\\AppData\\Local\\Temp\\", "\\Windows\\Temp\\", "Rar$", "\\7zO", "\\7zE")
"""Path parts that mean "run from an archive tool's temp copy" (REV2-D1). Explorer runs a .cmd opened inside a zip
from ``%LOCALAPPDATA%\\Temp\\Temp1_<zip>\\`` (``C:\\Windows\\Temp`` for a SYSTEM-profile TEMP); WinRAR uses ``Rar$…``
and 7-Zip ``7zO…``/``7zE…`` folders under TEMP (a bare ``7z`` would also match ``D:\\7zip``). A bare ``\\Temp\\`` was
too wide: it refused ``C:\\Temp\\DeepfakeLens-win64\\``, a normal place to unpack. A temp folder elsewhere is still
caught one line later: only the clicked file is extracted there, so ``python\\`` is missing (``no_python``)."""

CODEPAGE_SAVE = """for /f "tokens=2 delims=:." %%a in ('chcp') do set "DFL_CP=%%a\""""
"""REV2-D13: the console's code page before ``chcp 65001`` (``활성 코드 페이지: 949`` → `` 949``; ``.`` is also a
delimiter for locales that print ``850.``). Restored by :data:`CODEPAGE_RESTORE` on every exit path, so a cmd window
the launcher was started from keeps its code page. A Ctrl-C answered with Y ends the batch where it stands (cmd's own
rule) — the window started by a double-click closes then anyway."""
CODEPAGE_RESTORE = "if defined DFL_CP chcp %DFL_CP% >nul"

LOCATION_CHECKS = (
    'set "DFL_HERE=%~dp0"',
    *(f'if not "%DFL_HERE:{marker}=%"=="%DFL_HERE%" goto not_extracted' for marker in TEMP_MARKERS),
    'set "DFL_ARCH=%PROCESSOR_ARCHITECTURE%"',
    'if defined PROCESSOR_ARCHITEW6432 set "DFL_ARCH=%PROCESSOR_ARCHITEW6432%"',
    'if /i not "%DFL_ARCH%"=="AMD64" goto not_x64',
    'if not "%DFL_HERE:OneDrive=%"=="%DFL_HERE%" echo 주의: OneDrive 폴더에서 실행 중입니다. 느리거나 실행되지 않으면 C:\\DeepfakeLens 같은 로컬 폴더에 다시 푸십시오.',
    'if not exist "python\\python.exe" goto no_python',
)
"""Lines run right after ``pushd``: an unextracted zip (:data:`TEMP_MARKERS`), a non-x64 Windows
(``PROCESSOR_ARCHITEW6432`` is set when a 32-bit cmd runs on 64-bit Windows), an OneDrive folder (warning only), no
``python\\`` (an archive opened elsewhere, or a partial extraction). ``%var:x=%`` is case-insensitive. No parenthesised
blocks — a ``)`` in a folder name would end the block early."""

EXTRACT_FIRST = "echo 압축을 먼저 푸십시오(오른쪽 클릭 → 모두 압축 풀기). 그다음 압축을 푼 폴더의 「Deepfake Lens.cmd」를 실행하십시오."

LOCATION_LABELS = (
    ":not_extracted",
    "echo 압축 파일 안이나 임시 폴더에서 실행했습니다.",
    EXTRACT_FIRST,
    "pause",
    'set "DFL_CODE=2"',
    "goto end",
    ":not_x64",
    "echo 이 프로그램은 x64 Windows 전용입니다. 32비트·ARM Windows에서는 실행할 수 없습니다(감지된 종류: %DFL_ARCH%).",
    "pause",
    'set "DFL_CODE=2"',
    "goto end",
    ":no_python",
    "echo python 폴더가 없습니다. 압축 파일 안에서 실행했거나 압축을 일부만 풀었습니다.",
    EXTRACT_FIRST,
    "pause",
    'set "DFL_CODE=2"',
    "goto end",
    ":no_folder",
    'echo 이 폴더로 이동하지 못했습니다: "%~dp0"',
    "pause",
    CODEPAGE_RESTORE,
    "exit /b 2",
    ":end",
    "popd",
    CODEPAGE_RESTORE,
    "exit /b %DFL_CODE%",
)

LAUNCHER_HEAD = ("@echo off", "setlocal", CODEPAGE_SAVE, "chcp 65001 >nul")
"""ASCII only before ``chcp 65001`` (the file is UTF-8 without BOM; a Korean line before it would be read as CP949)."""


def windows_launcher_text() -> str:
    """``Deepfake Lens.cmd`` (plan §5; RISK A6 A7 B11 B12).

    UTF-8 without BOM (a BOM breaks ``@echo off``); ``chcp 65001`` comes before the first Korean line. ``pushd``
    instead of ``cd /d`` so a UNC folder gets a temporary drive letter (B12); every exit goes through ``popd``.
    ``%1`` is used only through ``DFL_FOLDER`` (``%*`` is never passed): a dropped folder as is, a dropped file as
    its folder ``%~dp1`` (B11), anything else as no argument. A trailing ``\\`` is removed — ``"C:\\x\\"`` makes the
    C runtime read ``\\"`` as a literal quote — and a bare drive (``C:``) becomes ``C:\\.``.
    The exit code is compared as a string (A6): ``if errorlevel 1`` means ``>= 1`` and never matches the negative
    codes of a native crash (0xC0000005) or a missing DLL (0xC0000135); 130, and -1073741510 (0xC000013A,
    STATUS_CONTROL_C_EXIT — how CPython on Windows exits on an uncaught KeyboardInterrupt), is a Ctrl-C stop.
    """
    return _crlf([
        *LAUNCHER_HEAD,
        "title Deepfake Lens — 이 창을 닫으면 프로그램이 꺼집니다",
        'set "DFL_CODE=0"',
        'pushd "%~dp0" || goto no_folder',
        *LOCATION_CHECKS,
        'set "DFL_FOLDER="',
        'if "%~1"=="" goto run',
        'if exist "%~1\\" set "DFL_FOLDER=%~1"',
        'if not defined DFL_FOLDER if exist "%~1" set "DFL_FOLDER=%~dp1"',
        "if not defined DFL_FOLDER goto run",
        'if "%DFL_FOLDER:~-1%"=="\\" set "DFL_FOLDER=%DFL_FOLDER:~0,-1%"',
        'if "%DFL_FOLDER:~-1%"==":" set "DFL_FOLDER=%DFL_FOLDER%\\."',
        ":run",
        "echo 시작하는 중입니다. 처음에는 1분 정도 걸릴 수 있으니 기다려 주십시오...",
        "if defined DFL_FOLDER goto run_folder",
        '"python\\python.exe" -X utf8 -m deepfake_lens desktop',
        "goto after_run",
        ":run_folder",
        '"python\\python.exe" -X utf8 -m deepfake_lens desktop --folder "%DFL_FOLDER%"',
        ":after_run",
        'set "DFL_CODE=%errorlevel%"',
        'if "%DFL_CODE%"=="0" goto end',
        f'if "%DFL_CODE%"=="{ALREADY_RUNNING_NO_BROWSER_EXIT}" pause',
        f'if "%DFL_CODE%"=="{ALREADY_RUNNING_NO_BROWSER_EXIT}" goto end',
        'if "%DFL_CODE%"=="-1073741510" set "DFL_CODE=130"',
        'if "%DFL_CODE%"=="130" echo 사용자가 중단했습니다.',
        'if "%DFL_CODE%"=="130" goto end',
        "echo.",
        "echo 프로그램이 오류로 끝났습니다(코드 %DFL_CODE%). 위 내용을 캡처해 보내 주십시오.",
        "pause",
        "goto end",
        *LOCATION_LABELS,
    ])


def doctor_launcher_text() -> str:
    """``문제 확인.cmd``: ``doctor``, then the log folder (as ``cli_logging.default_log_dir`` places it on Windows).

    ``DEEPFAKE_LENS_LOG_DIR`` wins; otherwise ``%USERPROFILE%\\.cache\\deepfake-lens\\logs``, or the relocated
    ``%LOCALAPPDATA%\\deepfake-lens\\logs`` when only that one exists (R21-10). The same ``pushd`` and location
    checks as the launcher (RISK B12).
    """
    return _crlf([
        *LAUNCHER_HEAD,
        "title Deepfake Lens — 문제 확인",
        'set "DFL_CODE=0"',
        'pushd "%~dp0" || goto no_folder',
        *LOCATION_CHECKS,
        '"python\\python.exe" -X utf8 -m deepfake_lens doctor',
        "echo.",
        'set "DFL_LOGS=%USERPROFILE%\\.cache\\deepfake-lens\\logs"',
        'if not exist "%DFL_LOGS%\\" if exist "%LOCALAPPDATA%\\deepfake-lens\\logs\\" set "DFL_LOGS=%LOCALAPPDATA%\\deepfake-lens\\logs"',
        'if defined DEEPFAKE_LENS_LOG_DIR set "DFL_LOGS=%DEEPFAKE_LENS_LOG_DIR%"',
        'echo 로그 폴더: "%DFL_LOGS%"',
        'if exist "%DFL_LOGS%\\" start "" explorer "%DFL_LOGS%"',
        'if not exist "%DFL_LOGS%\\" echo 아직 로그 폴더가 없습니다. 「Deepfake Lens.cmd」를 한 번 실행한 뒤 다시 확인하십시오.',
        "echo.",
        "echo 문제를 알릴 때는 이 창의 내용과 로그 폴더의 파일을 함께 보내 주십시오.",
        "pause",
        "goto end",
        *LOCATION_LABELS,
    ])


def readme_text() -> str:
    """``읽어보세요.txt`` (plan §1, §5, §6; RISK B3 B8 B13 B18 B19 B20 B21 C8)."""
    return _crlf([
        "Deepfake Lens — 설치 없이 쓰는 꾸러미",
        "",
        "■ 지원 사양",
        "  Windows 10/11 64비트(x64) 전용입니다. 32비트·ARM Windows에서는 실행되지 않습니다.",
        "  CPU는 인텔 2008년(Nehalem)·AMD 2011년(Bulldozer) 이후 x64 CPU(SSE4.2)여야 합니다.",
        "    더 오래된 CPU나 CPU 기능을 가린 가상 PC에서는 시작되지 않습니다.",
        "  관리자 권한·설치·PATH 변경은 필요 없습니다. 지우려면 폴더를 삭제하면 됩니다.",
        "",
        "■ 사용법",
        "  1. zip 파일을 오른쪽 클릭 → 속성 → (있으면) 「차단 해제」를 체크하고 확인을 누릅니다(풀기 전에).",
        "  2. 오른쪽 클릭 → 모두 압축 풀기로 C:\\DeepfakeLens 같은 로컬 폴더에 전부 풉니다. zip 안에서 바로 실행하지 마십시오.",
        "  3. 압축을 푼 폴더의 「Deepfake Lens.cmd」를 더블클릭합니다. 검은 창이 뜨고 브라우저에 검사 화면이 열립니다.",
        "     처음에는 시작까지 1분쯤 걸릴 수 있습니다. 기다리는 동안 다시 누르지 마십시오.",
        "  4. 화면의 「검사할 폴더 고르기」로 폴더를 고르면 검사가 시작됩니다. 결과 화면에서 감정서(PDF)를 받습니다.",
        "  폴더(또는 그 안의 파일)를 「Deepfake Lens.cmd」 위에 끌어다 놓으면 그 폴더를 연 채로 시작합니다.",
        "  끌어 놓기가 안 되면(경로에 & , ; = 가 있고 공백이 없으면) 화면에서 고르십시오.",
        "  폴더 고르기 창은 오래된 「폴더 찾아보기」 트리 창입니다. 정상입니다(주소를 붙여 넣는 칸은 없습니다).",
        "    창이 보이지 않으면 Alt+Tab으로 「폴더 찾아보기」 창을 찾으십시오.",
        "  네트워크 드라이브(예: Z:)의 폴더를 고르면 화면과 감정서에 \\\\서버\\공유 형태의 경로로 표시됩니다.",
        "",
        "■ 끄는 방법",
        "  화면 위쪽의 「프로그램 종료」 단추를 누르거나 검은 창을 닫습니다. 검은 창을 닫으면 진행 중인 검사도 멈춥니다.",
        "  검은 창에서 Ctrl+C를 누르면 「중단됨(사용자 요청)」이 나온 뒤 「일괄 작업을 끝내시겠습니까 (Y/N)?」가 나옵니다.",
        "    Y를 누르십시오(N을 누르면 「사용자가 중단했습니다.」가 나오고 창이 닫힙니다).",
        "  브라우저 탭만 닫으면 프로그램은 남아 있다가, 20분 동안 화면도 검사도 없으면 스스로 꺼집니다.",
        "  꺼진 뒤 다시 쓰려면 압축을 푼 폴더의 「Deepfake Lens.cmd」를 다시 실행하십시오.",
        "  두 번 실행하면 새로 켜지 않고 이미 실행 중인 화면을 브라우저로 엽니다.",
        "",
        "■ 브라우저",
        "  Edge나 Chrome을 쓰십시오. Edge는 오래 둔 탭을 절전시키므로 그사이 프로그램이 꺼질 수 있습니다.",
        "  Edge 설정 → 시스템 및 성능에서 절전하지 않을 사이트 목록에 127.0.0.1을 추가하십시오.",
        "  Chrome은 설정 → 성능 → 「항상 활성 상태로 유지할 사이트」에 127.0.0.1을 추가하십시오(메모리 절약 모드 예외).",
        "",
        "■ 실행이 막힐 때",
        "  「Windows의 PC 보호」 창: 「추가 정보」를 누른 뒤 「실행」을 누르십시오(이 .cmd 파일들은 코드 서명이 없습니다).",
        "  이미 풀었다면 PowerShell에서 다음을 실행해 차단을 풉니다.",
        "    Get-ChildItem -Recurse C:\\DeepfakeLens | Unblock-File",
        "  Windows 11 스마트 앱 컨트롤(SAC)이 켜진 PC는 풀기 전에 zip의 차단 해제가 꼭 필요합니다.",
        "    그래도 「실행」 단추 없이 막히면 이 PC에서는 쓸 수 없습니다. 담당자에게 알리십시오.",
        "  회사 PC에서 AppLocker·그룹 정책으로 막히면 IT 담당자에게 이 폴더의 실행 허용을 요청하십시오.",
        "  백신이 막거나 파일을 격리하면 이 폴더를 검사 예외로 등록하십시오.",
        "    V3: 환경 설정 → 검사 예외 설정에 이 폴더를 추가합니다.",
        "",
        "■ 풀 위치와 검사할 파일",
        "  OneDrive·네트워크 드라이브 아래에는 풀지 마십시오(느리고, 파일이 온라인 전용이면 실행되지 않습니다).",
        "  바탕 화면·문서 폴더가 OneDrive로 옮겨진 PC가 많습니다. 그 아래의 증거 폴더는 오른쪽 클릭 →",
        "    「항상 이 디바이스에 유지」로 모두 내려받은 뒤 검사하십시오.",
        "  문서보안(DRM)이 걸린 파일은 읽지 못해 손상된 파일처럼 보입니다. 보안을 해제한 사본을 검사하십시오.",
        "",
        "■ 기록과 서명 키",
        "  검토 기록·검사 작업·보고서 서명 키는 %APPDATA%\\deepfake-lens\\ 에 남습니다(꾸러미를 새로 받아도 유지).",
        "  서명 키 파일: %APPDATA%\\deepfake-lens\\report-key — 처음 실행할 때 자동으로 만들어집니다.",
        "  이 키로 서명한 감정서는 이 키 파일을 가진 계정에서만 검증됩니다",
        "    (%APPDATA%는 로밍 프로필에서는 다른 PC로 동기화될 수 있습니다).",
        "  검증: 이 폴더에서 명령 프롬프트(cmd)를 열고 다음을 실행합니다(<서명된 JSON>은 내려받은 서명된 JSON 감정서).",
        "    python\\python.exe -X utf8 -m deepfake_lens verify-report <서명된 JSON> --key-file %APPDATA%\\deepfake-lens\\report-key",
        "  PowerShell에서는 %APPDATA% 대신 $env:APPDATA, python\\python.exe 대신 .\\python\\python.exe 를 씁니다.",
        "  키 파일을 지우면 다음 실행 때 새 키가 만들어지고, 그 전의 감정서는 더 이상 검증되지 않습니다.",
        "",
        "■ 문제가 생기면",
        "  「문제 확인.cmd」를 더블클릭하면 점검 결과를 보여 주고 로그 폴더를 엽니다.",
        "  로그 폴더: %USERPROFILE%\\.cache\\deepfake-lens\\logs",
        "  검은 창의 내용(캡처)과 로그 폴더의 파일, VERSION.txt를 함께 보내 주십시오.",
    ])


def version_text(sha: str, built: datetime, platform_name: str) -> str:
    return _crlf([f"commit {sha}", f"built {built.strftime('%Y-%m-%dT%H:%M:%SZ')}", f"platform {platform_name}", f"python {EMBED_VERSION}" if platform_name == "windows" else "python 3.12/3.13 (venv)"])


def mac_launcher_text(wheel_name: str, requirements: Iterable[str], pythons: Iterable[str] = MAC_PYTHONS) -> str:
    """``Deepfake Lens.command``: a venv made once from the bundled ``wheels/`` (no network), then ``desktop``."""
    reqs = " ".join(f"'{r}'" for r in requirements)
    candidates = " ".join(f"python{v}" for v in sorted(pythons, reverse=True))
    accepted = " ".join(pythons)
    return "\n".join([
        "#!/bin/bash",
        "# Deepfake Lens (macOS) — 더블클릭하면 처음 한 번 .venv를 만들고 데스크톱 모드를 시작합니다.",
        "set -u",
        'cd "$(dirname "$0")" || exit 2',
        'stamp=".venv/.deepfake-lens-installed"',
        'want="$(cat VERSION.txt 2>/dev/null)"',
        'if [ ! -x .venv/bin/python ] || [ "$(cat "$stamp" 2>/dev/null)" != "$want" ]; then',
        '  py=""',
        f"  for cand in {candidates} python3; do",
        '    command -v "$cand" >/dev/null 2>&1 || continue',
        "    ver=\"$(\"$cand\" -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])' 2>/dev/null)\"",
        '    case " ' + accepted + ' " in *" $ver "*) py="$cand"; break;; esac',
        "  done",
        '  if [ -z "$py" ]; then',
        f'    echo "Python {" 또는 ".join(pythons)}이 필요합니다(Homebrew: brew install python@3.12)."',
        '    read -r -p "Enter를 누르면 창을 닫습니다." _; exit 2',
        "  fi",
        '  echo "처음 실행 준비 중입니다(.venv 만들기, 1~2분)…"',
        '  rm -rf .venv && "$py" -m venv .venv || { echo "가상환경을 만들지 못했습니다."; read -r -p "Enter" _; exit 2; }',
        f'  .venv/bin/python -m pip install --quiet --disable-pip-version-check --no-index --find-links wheels "wheels/{wheel_name}" {reqs} \\',
        '    || { echo "꾸러미의 wheels 폴더에서 설치하지 못했습니다."; read -r -p "Enter" _; exit 2; }',
        '  printf "%s" "$want" > "$stamp"',
        "fi",
        'exec .venv/bin/python -X utf8 -m deepfake_lens desktop "$@"',
        "",
    ])


def canonical(name: str) -> str:
    """PEP 503 normalised project name."""
    import re

    return re.sub(r"[-_.]+", "-", name).lower()


def marker_environment(platform_name: str, python_version: str = "3.12") -> dict[str, str]:
    """The PEP 508 marker values of the bundle's interpreter (extras off)."""
    full = EMBED_VERSION if python_version == "3.12" else python_version + ".0"
    if platform_name == "windows":
        osv = {"os_name": "nt", "sys_platform": "win32", "platform_system": "Windows", "platform_machine": "AMD64"}
    else:
        osv = {"os_name": "posix", "sys_platform": "darwin", "platform_system": "Darwin", "platform_machine": "arm64"}
    return {
        **osv,
        "python_version": python_version,
        "python_full_version": full,
        "implementation_name": "cpython",
        "implementation_version": full,
        "platform_python_implementation": "CPython",
        "platform_release": "",
        "platform_version": "",
        "extra": "",
    }


def _packaging() -> tuple[Any, Any]:
    """``(Requirement, Version)`` from ``packaging``, or from pip's vendored copy (the build venv always has pip)."""
    import importlib

    for base in ("packaging", "pip._vendor.packaging"):
        try:
            return importlib.import_module(base + ".requirements").Requirement, importlib.import_module(base + ".version").Version
        except ImportError:
            continue
    raise BuildError("packaging 모듈이 없습니다 — pip가 있는 Python으로 실행하십시오")


def metadata_fields(text: str) -> tuple[str, str, list[str]]:
    """``(Name, Version, [Requires-Dist …])`` of a METADATA file."""
    message = HeaderParser().parsestr(text)
    return message.get("Name", ""), message.get("Version", ""), list(message.get_all("Requires-Dist") or [])


def missing_requirements(dists: Mapping[str, tuple[str, list[str]]], environment: Mapping[str, str]) -> list[str]:
    """Problems with a set of dists ``{name: (version, requires_dist)}`` for one interpreter.

    A requirement whose marker is false in ``environment`` (with ``extra`` empty — no extras are installed) is
    ignored; any other requirement must name an installed dist whose version satisfies the specifier.
    """
    requirement_type, version_type = _packaging()
    installed = {canonical(name): version for name, (version, _reqs) in dists.items()}
    problems: list[str] = []
    for name, (_version, requires) in sorted(dists.items()):
        for line in requires:
            req = requirement_type(line)
            if req.marker is not None and not req.marker.evaluate(dict(environment)):
                continue
            have = installed.get(canonical(req.name))
            if have is None:
                problems.append(f"{name}: 필요한 {req} 이(가) 설치되지 않았습니다")
            elif req.specifier and not req.specifier.contains(version_type(have), prereleases=True):
                problems.append(f"{name}: 필요한 {req} — 설치된 판은 {have}")
    return problems


def site_packages_dists(site_packages: Path) -> dict[str, tuple[str, list[str]]]:
    """Every ``*.dist-info/METADATA`` under ``site_packages``."""
    dists: dict[str, tuple[str, list[str]]] = {}
    for meta in sorted(site_packages.glob("*.dist-info/METADATA")):
        name, version, requires = metadata_fields(meta.read_text(encoding="utf-8", errors="replace"))
        if name:
            dists[name] = (version, requires)
    return dists


def wheel_dists(wheels: Iterable[Path]) -> dict[str, tuple[str, list[str]]]:
    """The METADATA of each wheel file."""
    dists: dict[str, tuple[str, list[str]]] = {}
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            meta = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA") and n.count("/") == 1)
            name, version, requires = metadata_fields(archive.read(meta).decode("utf-8", "replace"))
        dists[name] = (version, requires)
    return dists


def zip_folder(folder: Path, zip_path: Path) -> int:
    """Zip ``folder`` (as the top directory) with ``/`` separators; non-ASCII names get the UTF-8 flag. Returns the count."""
    count = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob("*")):
            arcname = "/".join((folder.name, *path.relative_to(folder).parts))
            if path.is_dir():
                continue
            archive.write(path, arcname)
            count += 1
    return count


def verify_windows_bundle(folder: Path) -> list[str]:
    """What is wrong with a built Windows folder (empty when it is complete)."""
    problems: list[str] = []
    python = folder / "python"
    for need in ("python.exe", "pythonw.exe", "python312.dll", "python312.zip"):
        if not (python / need).is_file():
            problems.append(f"python/{need} 없음")
    pth = python / PTH_NAME
    if not pth.is_file():
        problems.append(f"python/{PTH_NAME} 없음")
    else:
        lines = [line.strip() for line in pth.read_text(encoding="utf-8").splitlines()]
        for need in ("python312.zip", ".", SITE_PACKAGES_PTH, "import site"):
            if need not in lines:
                problems.append(f"{PTH_NAME}에 {need!r} 줄이 없음")
    site = python / "Lib" / "site-packages"
    for need in ("deepfake_lens/gui.js", "deepfake_lens/gui.html", "deepfake_lens/__main__.py", "pymupdf/__init__.py", "fitz/__init__.py", "numpy/__init__.py", "PIL/__init__.py", "c2pa/__init__.py", "py7zr/__init__.py", "olefile/__init__.py"):
        if not (site / need).is_file():
            problems.append(f"site-packages/{need} 없음")
    for need in (WIN_LAUNCHER, WIN_DOCTOR, WIN_README, "VERSION.txt"):
        if not (folder / need).is_file():
            problems.append(f"{need} 없음")
    for need in MSVC_RUNTIME_DLLS:  # RISK A1
        if not (python / need).is_file():
            problems.append(f"python/{need} 없음")
    if python.is_dir():
        problems.extend(runtime_version_problems(python))  # REV2-D7
    if site.is_dir():
        problems.extend(missing_requirements(site_packages_dists(site), marker_environment("windows")))
        problems.extend(f"{trace} 남음" for trace in build_traces(site))  # RISK B22
        if not (site / "deepfake_lens" / "__pycache__").is_dir():  # RISK A7
            problems.append("site-packages/deepfake_lens/__pycache__ 없음(미리 컴파일 안 됨)")
        problems.extend(f"빌드 경로가 남은 .pyc: {leak}" for leak in pyc_build_path_leaks(site))  # REV2-D5
    if python.is_dir():
        problems.extend(unresolved_imports(python)[0])
    return problems


def tree_summary(folder: Path, expand: Iterable[str] = (), min_bytes: int = 1_000_000) -> tuple[list[str], int]:
    """Lines ``size  path`` for the top-level entries and, for each folder in ``expand`` (relative, ``/``), its
    children of at least ``min_bytes``; plus the total size in bytes."""
    def size_of(path: Path) -> int:
        if path.is_file():
            return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())

    def line(path: Path, size: int) -> str:
        return f"{size / 1e6:9.1f} MB  {path.relative_to(folder).as_posix()}{'/' if path.is_dir() else ''}"

    lines = [line(child, size_of(child)) for child in sorted(folder.iterdir(), key=lambda p: p.name.lower())]
    for rel in expand:
        base = folder / rel
        if not base.is_dir():
            continue
        sized = sorted(((size_of(child), child) for child in base.iterdir()), key=lambda pair: -pair[0])
        lines.extend(line(child, size) for size, child in sized if size >= min_bytes)
    return lines, size_of(folder)


PRUNE_SUFFIXES = (".lib", ".pdb", ".exp", ".d")
"""Linker and debugger files in Windows wheels that Python never loads. c2pa-python 0.38.0's win_amd64 wheel carries
``c2pa/libs/c2pa_c.lib`` (283 MB) and ``c2pa_c.pdb`` next to the ``c2pa_c.dll`` it loads; linker exports (``.exp``)
and compiler dep-info (``.d``) files are removed as well (REV2-D13); numpy ships ``.lib`` files for building
extensions. Removing them leaves those dists' RECORD files stale, which only ``pip uninstall`` reads."""


def prune_site_packages(site: Path) -> tuple[int, int]:
    """Delete :data:`PRUNE_SUFFIXES` files under ``site``; returns ``(count, bytes)``."""
    count = freed = 0
    for path in sorted(site.rglob("*")):
        if path.is_file() and path.suffix.lower() in PRUNE_SUFFIXES:
            freed += path.stat().st_size
            path.unlink()
            count += 1
    return count, freed


def remove_build_traces(site: Path) -> list[str]:
    """RISK B22: delete ``site-packages/bin/`` (console scripts whose shebang names the build machine's Python) and
    every ``*.dist-info/direct_url.json`` (the build machine's wheel paths). Returns what was removed (relative)."""
    removed: list[str] = []
    bin_dir = site / "bin"
    if bin_dir.is_dir():
        shutil.rmtree(bin_dir)
        removed.append("bin/")
    for path in sorted(site.glob("*.dist-info/direct_url.json")):
        path.unlink()
        removed.append(path.relative_to(site).as_posix())
    return removed


def build_traces(site: Path) -> list[str]:
    """What :func:`remove_build_traces` would remove (the verify step's view)."""
    found = ["site-packages/bin/"] if (site / "bin").exists() else []
    found += [f"site-packages/{p.relative_to(site).as_posix()}" for p in sorted(site.glob("*.dist-info/direct_url.json"))]
    return found


# ── RISK A1: a minimal PE import-table reader (names only) ───────────────────────────────────────────────────────


class PEFormatError(ValueError):
    """Not a PE image this reader understands."""


def _u16(data: bytes, at: int) -> int:
    if at < 0 or at + 2 > len(data):
        raise PEFormatError(f"offset {at} outside the file")
    return int.from_bytes(data[at:at + 2], "little")


def _u32(data: bytes, at: int) -> int:
    if at < 0 or at + 4 > len(data):
        raise PEFormatError(f"offset {at} outside the file")
    return int.from_bytes(data[at:at + 4], "little")


def pe_imported_dlls(data: bytes) -> list[str]:
    """The DLL names in a PE image's import table (data directory 1), in table order.

    DOS header ``e_lfanew`` (0x3C) → ``PE\\0\\0`` → COFF header (number of sections at +2, optional-header size at
    +16) → optional header (magic 0x10B PE32 or 0x20B PE32+; ``NumberOfRvaAndSizes`` at 92/108, data directories at
    96/112) → import directory RVA → file offset through the section table → 20-byte descriptors until an all-zero
    one, each with the name's RVA at +12. Delay-load imports (directory 13) are not read: Windows loads them on first
    use, not when the module is loaded.
    """
    if data[:2] != b"MZ":
        raise PEFormatError("no MZ header")
    pe = _u32(data, 0x3C)
    if data[pe:pe + 4] != b"PE\0\0":
        raise PEFormatError("no PE signature")
    coff = pe + 4
    sections_count = _u16(data, coff + 2)
    optional_size = _u16(data, coff + 16)
    optional = coff + 20
    magic = _u16(data, optional)
    if magic == 0x10B:
        count_at, dirs_at = 92, 96
    elif magic == 0x20B:
        count_at, dirs_at = 108, 112
    else:
        raise PEFormatError(f"unknown optional-header magic {magic:#x}")
    if _u32(data, optional + count_at) < 2:
        return []
    import_rva = _u32(data, optional + dirs_at + 8)
    if import_rva == 0:
        return []
    sections = []
    table = optional + optional_size
    for index in range(sections_count):
        entry = table + 40 * index
        virtual_size, virtual_address = _u32(data, entry + 8), _u32(data, entry + 12)
        raw_size, raw_pointer = _u32(data, entry + 16), _u32(data, entry + 20)
        sections.append((virtual_address, max(virtual_size, raw_size), raw_pointer))

    def offset_of(rva: int) -> int:
        for virtual_address, size, raw_pointer in sections:
            if virtual_address <= rva < virtual_address + size:
                return raw_pointer + rva - virtual_address
        raise PEFormatError(f"RVA {rva:#x} is in no section")

    names: list[str] = []
    descriptor = offset_of(import_rva)
    while True:
        fields = [_u32(data, descriptor + 4 * i) for i in range(5)]
        if not any(fields):
            return names
        start = offset_of(fields[3])
        end = data.find(b"\0", start)
        if end < 0:
            raise PEFormatError("unterminated DLL name")
        names.append(bytes(data[start:end]).decode("ascii", "replace"))
        descriptor += 20
        if len(names) > 4096:
            raise PEFormatError("import table without an end")


def is_system_dll(name: str) -> bool:
    lower = name.lower()
    return lower in SYSTEM_DLLS or lower.startswith(SYSTEM_DLL_PREFIXES)


def unresolved_imports(python_dir: Path) -> tuple[list[str], int]:
    """RISK A1: every ``.pyd``/``.dll`` under ``python_dir`` whose import table names a DLL that is not a system DLL,
    not in the importing file's own folder, not in ``python_dir`` (the application folder — Windows searches it for
    every module), and not in a ``site-packages/*.libs`` folder (delvewheel's folders, added with
    ``os.add_dll_directory`` by the package's ``__init__``). Returns ``(problems, files checked)``."""
    site = python_dir / "Lib" / "site-packages"
    shared = {p.name.lower() for p in python_dir.iterdir() if p.is_file()}
    for libs in site.glob("*.libs") if site.is_dir() else ():
        shared |= {p.name.lower() for p in libs.iterdir() if p.is_file()}
    problems: list[str] = []
    checked = 0
    for path in sorted(python_dir.rglob("*")):
        if path.suffix.lower() not in (".pyd", ".dll") or not path.is_file():
            continue
        try:
            imported = pe_imported_dlls(path.read_bytes())
        except PEFormatError as exc:
            problems.append(f"{path.relative_to(python_dir).as_posix()}: PE를 읽지 못함({exc})")
            continue
        checked += 1
        beside = {p.name.lower() for p in path.parent.iterdir() if p.is_file()}
        for name in imported:
            lower = name.lower()
            if not (is_system_dll(name) or lower in beside or lower in shared):
                problems.append(f"{path.relative_to(python_dir).as_posix()}: {name}을(를) 찾을 수 없음")
    return problems, checked


# ── network and process steps (not called by the unit tests) ───────────────────────────────────────────────────


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_embed(cache: Path) -> Path:
    """The embeddable zip from python.org (cached), refused when its sha256 is not :data:`EMBED_SHA256`."""
    target = cache / "python" / EMBED_NAME
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part")
        print(f"내려받는 중: {EMBED_URL}", flush=True)
        with urllib.request.urlopen(EMBED_URL, timeout=120) as answer, partial.open("wb") as out:
            shutil.copyfileobj(answer, out)
        partial.replace(target)
    actual = sha256_of(target)
    if actual != EMBED_SHA256:
        raise BuildError(f"{EMBED_NAME}의 sha256이 고정값과 다릅니다: {actual} (기대 {EMBED_SHA256}) — 파일을 지우고 다시 받으십시오: {target}")
    return target


def fetch_msvc_runtime(cache: Path) -> Path:
    """RISK A1: the msvc-runtime wheel (``pip download``, cached), refused when its sha256 is not pinned."""
    dest = cache / "msvc-runtime"
    target = dest / MSVC_RUNTIME_WHEEL
    if not target.is_file():
        _run([
            sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check", "--no-deps",
            "--only-binary=:all:", "--platform", "win_amd64", "--python-version", "3.12", "--dest", str(dest),
            f"msvc-runtime=={MSVC_RUNTIME_VERSION}",
        ])
    if not target.is_file():
        raise BuildError(f"{MSVC_RUNTIME_WHEEL}을(를) 받지 못했습니다")
    actual = sha256_of(target)
    if actual != MSVC_RUNTIME_SHA256:
        raise BuildError(f"{MSVC_RUNTIME_WHEEL}의 sha256이 고정값과 다릅니다: {actual} (기대 {MSVC_RUNTIME_SHA256}) — 파일을 지우고 다시 받으십시오: {target}")
    return target


def install_msvc_runtime(wheel: Path, python_dir: Path) -> list[str]:
    """Copy :data:`MSVC_RUNTIME_DLLS` from the wheel's ``msvc_runtime-<v>.data/data/`` and :data:`VCRUNTIME_DLLS`
    from its ``data/Scripts/`` (the only place the wheel has them) into ``python_dir``, overwriting the embeddable's
    vcruntime (REV2-D7)."""
    prefix = f"msvc_runtime-{MSVC_RUNTIME_VERSION}.data/data/"
    wanted = [(dll, prefix + dll) for dll in MSVC_RUNTIME_DLLS] + [(dll, prefix + "Scripts/" + dll) for dll in VCRUNTIME_DLLS]
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for dll, member in wanted:
            if member not in names:
                raise BuildError(f"{wheel.name}에 {member}이(가) 없습니다")
            (python_dir / dll).write_bytes(archive.read(member))
    return [dll for dll, _member in wanted]


def pe_file_version(data: bytes) -> str | None:
    """The file version in a PE image's ``VS_FIXEDFILEINFO`` (signature 0xFEEF04BD, the struct version, then
    ``dwFileVersionMS``/``LS``) as ``a.b.c.d``; None when the image has no version resource."""
    at = data.find((0xFEEF04BD).to_bytes(4, "little"))
    if at < 0 or at + 16 > len(data):
        return None
    high, low = int.from_bytes(data[at + 8:at + 12], "little"), int.from_bytes(data[at + 12:at + 16], "little")
    return f"{high >> 16}.{high & 0xFFFF}.{low >> 16}.{low & 0xFFFF}"


def runtime_versions(python_dir: Path) -> dict[str, str | None]:
    """REV2-D7: the file version of every C/C++ runtime DLL in ``python_dir`` (vcruntime and msvcp; None if absent)."""
    return {
        dll: pe_file_version((python_dir / dll).read_bytes()) if (python_dir / dll).is_file() else None
        for dll in (*VCRUNTIME_DLLS, *MSVC_RUNTIME_DLLS)
    }


def runtime_version_problems(python_dir: Path) -> list[str]:
    """REV2-D7: a runtime DLL without a version, or more than one version among them."""
    versions = runtime_versions(python_dir)
    problems = [f"python/{dll}: 파일 버전을 읽지 못함" for dll, version in versions.items() if version is None and (python_dir / dll).is_file()]
    if len({version for version in versions.values() if version is not None}) > 1:
        problems.append("C/C++ 런타임 DLL의 버전이 섞여 있음: " + ", ".join(f"{dll} {version}" for dll, version in versions.items() if version))
    return problems


PRECOMPILE_SKIP = r"[/\\]olefile[/\\]olefile2\.py$"
"""Files ``compileall`` skips: olefile 0.47 still ships ``olefile2.py``, its Python 2 module (``0xFFFFFFFAL``
literals — a SyntaxError on Python 3; nothing imports it). Any other file that does not compile stops the build."""


def python312_for_bytecode() -> str:
    """RISK A7: a CPython 3.12 on the build machine whose ``importlib.util.MAGIC_NUMBER`` is :data:`PYC_MAGIC_312`
    (the embeddable 3.12.10 reads that bytecode). ``sys.executable`` when it is 3.12, else ``python3.12`` on PATH."""
    candidates = [sys.executable] if sys.version_info[:2] == (3, 12) else []
    found = shutil.which("python3.12")
    if found:
        candidates.append(found)
    for candidate in candidates:
        done = subprocess.run(
            [candidate, "-I", "-c", "import importlib.util, sys; print(sys.version_info[0], sys.version_info[1], importlib.util.MAGIC_NUMBER.hex())"],
            capture_output=True, text=True, check=False,
        )
        if done.returncode == 0 and done.stdout.split() == ["3", "12", PYC_MAGIC_312.hex()]:
            return candidate
    raise BuildError("미리 컴파일에 쓸 Python 3.12가 없습니다(바이트코드 매직이 3.12와 같아야 합니다) — python3.12를 설치하십시오")


PYC_PREPEND = "Lib\\site-packages"
"""REV2-D5: what ``compileall -s <site-packages> -p`` puts in front of each module's path in the code objects'
``co_filename`` — the build folder (``/Users/<name>/…``) is stripped. At import CPython replaces ``co_filename`` with
the real source path anyway (``_imp._fix_co_filename``); this keeps the build machine's paths out of the files."""
PYC_CHECKED_HASH_FLAGS = 0b11
"""PEP 552 flags of a ``checked-hash`` .pyc: bit 0 hash-based, bit 1 check the source."""


def build_path_needles() -> list[bytes]:
    """REV2-D5: byte strings no shipped .pyc may contain — the build machine's home folder and the user name in it."""
    home = Path.home()
    needles = [str(home).encode("utf-8")]
    if len(home.name) >= 3:
        needles.append(home.name.encode("utf-8"))
    return needles


def pyc_build_path_leaks(site: Path, needles: Iterable[bytes] | None = None) -> list[str]:
    """REV2-D5: the .pyc files under ``site`` that contain one of ``needles`` (default :func:`build_path_needles`)."""
    wanted = list(build_path_needles() if needles is None else needles)
    leaks: list[str] = []
    for pyc in sorted(site.rglob("*.pyc")):
        data = pyc.read_bytes()
        found = [needle.decode("utf-8", "replace") for needle in wanted if needle in data]
        if found:
            leaks.append(f"{pyc.relative_to(site).as_posix()}: {', '.join(found)}")
    return leaks


def precompile(site: Path) -> None:
    """RISK A7: ``compileall`` over ``site`` with a 3.12 build interpreter, so the first start does not compile
    1,000+ modules while antivirus scans each new file.

    REV2-D6: ``checked-hash``, not ``unchecked-hash``. An unchecked .pyc is used even when its ``.py`` has changed,
    so a module patched by hand in the field would silently keep running the old code. A checked .pyc carries the
    source's hash; Python hashes the ``.py`` at import (cheap next to compiling it) and recompiles when it differs.
    Timestamp-based .pyc files are not used: unzipping and copying rewrite mtimes, which would invalidate them all.
    REV2-D5: ``-s``/``-p`` strip the build folder from the paths stored in the code objects.
    """
    python = python312_for_bytecode()
    done = subprocess.run(
        [
            python, "-I", "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "checked-hash",
            "-s", str(site), "-p", PYC_PREPEND, "-x", PRECOMPILE_SKIP, str(site),
        ],
        capture_output=True, text=True, check=False,
    )
    if done.returncode != 0:
        raise BuildError("미리 컴파일 실패:\n" + (done.stdout + done.stderr)[-4000:])
    pycs = sorted(site.rglob("__pycache__/*.pyc"))
    tags = {p.name.split(".")[-2] for p in pycs}
    if tags != {"cpython-312"}:
        raise BuildError(f"미리 컴파일된 .pyc의 태그가 cpython-312만이 아닙니다: {sorted(tags)}")
    # header: magic, then flags 0b11 (hash-based, source checked — PEP 552 checked-hash)
    wrong = [p for p in pycs if p.read_bytes()[:8] != PYC_MAGIC_312 + PYC_CHECKED_HASH_FLAGS.to_bytes(4, "little")]
    if wrong:
        raise BuildError(f".pyc {len(wrong)}개의 머리(매직·checked-hash 플래그)가 다릅니다: {wrong[0]}")
    leaks = pyc_build_path_leaks(site)
    if leaks:
        raise BuildError(f".pyc {len(leaks)}개에 빌드 머신의 경로가 남아 있습니다:\n  " + "\n  ".join(leaks[:20]))
    print(f"  .pyc {len(pycs)}개(cpython-312, checked-hash, 빌드 경로 없음, {python})", flush=True)


def _run(command: list[str], **kwargs: object) -> None:
    print("  $ " + " ".join(command[:8]) + (" …" if len(command) > 8 else ""), flush=True)
    done = subprocess.run(command, check=False, **kwargs)  # type: ignore[call-overload]
    if done.returncode != 0:
        raise BuildError(f"명령이 실패했습니다(종료 코드 {done.returncode}): {' '.join(command[:6])} …")


def build_repo_wheel(cache: Path) -> Path:
    """``pip wheel . --no-deps`` of this repository into a fresh folder; returns the wheel."""
    out = cache / "repo-wheel"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    _run([sys.executable, "-m", "pip", "wheel", "--quiet", "--no-deps", "--wheel-dir", str(out), str(REPO_ROOT)])
    wheels = sorted(out.glob("deepfake_lens-*.whl"))
    if len(wheels) != 1:
        raise BuildError(f"레포 휠이 하나가 아닙니다: {wheels}")
    return wheels[0]


def download_wheels(requirements: Iterable[str], dest: Path, platform_tag: str, python_version: str) -> list[Path]:
    """``pip download --no-deps --only-binary=:all:`` of each pinned requirement for one platform/interpreter."""
    dest.mkdir(parents=True, exist_ok=True)
    reqs = list(requirements)
    abi = "cp" + python_version.replace(".", "")
    _run([
        sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check", "--no-deps",
        "--only-binary=:all:", "--platform", platform_tag, "--python-version", python_version,
        "--implementation", "cp", "--abi", abi, "--abi", "abi3", "--abi", "none", "--dest", str(dest), *reqs,
    ])
    found: list[Path] = []
    for req in reqs:
        name, _, version = req.partition("==")
        matches = [w for w in dest.glob("*.whl") if canonical(w.name.split("-")[0]) == canonical(name) and w.name.split("-")[1] == version]
        compatible = [w for w in matches if _wheel_fits(w.name, platform_tag, abi)]
        if not compatible:
            raise BuildError(f"{req}의 휠을 찾지 못했습니다({platform_tag}, {abi})")
        found.append(sorted(compatible)[-1])
    return found


def _wheel_fits(filename: str, platform_tag: str, abi: str) -> bool:
    """True when a wheel's tags fit the target (win_amd64, or macOS arm64/universal2) and interpreter ABI."""
    parts = filename[:-4].split("-")
    abi_tag, plat_tag = parts[-2], parts[-1]
    if plat_tag != "any":
        if platform_tag.startswith("win"):
            fits = "win_amd64" in plat_tag.split(".")
        else:
            fits = any(p.startswith("macosx") and p.endswith(("arm64", "universal2")) for p in plat_tag.split("."))
        if not fits:
            return False
    return abi_tag in ("none", "abi3") or abi in abi_tag.split(".")


def git_sha() -> str:
    done = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    sha = done.stdout.strip() if done.returncode == 0 else "unknown"
    dirty = subprocess.run(["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, check=False)
    return sha + ("-dirty" if dirty.stdout.strip() else "")


def _fresh(folder: Path) -> None:
    """Empty the output folder; refuse one that holds something other than an earlier bundle."""
    if folder.exists():
        if any(folder.iterdir()) and not (folder / "VERSION.txt").is_file():
            raise BuildError(f"출력 폴더가 비어 있지 않고 이전 꾸러미도 아닙니다: {folder}")
        shutil.rmtree(folder)
    folder.mkdir(parents=True)


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def build_windows(out_parent: Path, cache: Path) -> Path:
    folder = out_parent / WIN_DIR
    _fresh(folder)
    print("[1/6] python.org 임베더블 Python", flush=True)
    embed = fetch_embed(cache)
    python = folder / "python"
    with zipfile.ZipFile(embed) as archive:
        archive.extractall(python)
    print(f"[2/6] {PTH_NAME} 고치기", flush=True)
    pth = python / PTH_NAME
    _write(pth, rewrite_pth(pth.read_text(encoding="utf-8")))
    print("[3/6] 휠 설치(win_amd64, cp312, --no-deps)", flush=True)
    repo_wheel = build_repo_wheel(cache)
    wheels = download_wheels(WINDOWS_WHEELS, cache / "wheels-win_amd64-cp312", "win_amd64", "3.12")
    site = python / "Lib" / "site-packages"
    site.mkdir(parents=True)
    _run([
        sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-index", "--no-deps",
        "--no-compile", "--target", str(site), "--platform", "win_amd64", "--python-version", "3.12",
        "--implementation", "cp", "--abi", "cp312", "--only-binary=:all:", str(repo_wheel), *map(str, wheels),
    ])
    pruned, freed = prune_site_packages(site)
    print(f"  링커·디버그 파일 {pruned}개 삭제({freed / 1e6:.1f} MB)", flush=True)
    removed = remove_build_traces(site)
    print(f"  빌드 흔적 {len(removed)}개 삭제(bin/, direct_url.json)", flush=True)
    problems = missing_requirements(site_packages_dists(site), marker_environment("windows"))
    if problems:
        raise BuildError("의존성 검사 실패:\n  " + "\n  ".join(problems))
    runtime = install_msvc_runtime(fetch_msvc_runtime(cache), python)
    print(f"  C/C++ 런타임(msvc-runtime {MSVC_RUNTIME_VERSION}): {', '.join(runtime)}", flush=True)
    for dll, version in runtime_versions(python).items():
        print(f"    {dll} {version}", flush=True)
    mixed = runtime_version_problems(python)
    if mixed:
        raise BuildError("런타임 DLL 검사 실패:\n  " + "\n  ".join(mixed))
    unresolved, checked = unresolved_imports(python)
    print(f"  DLL import 검사: .pyd/.dll {checked}개, 찾지 못한 DLL {len(unresolved)}건", flush=True)
    if unresolved:
        raise BuildError("DLL import 검사 실패:\n  " + "\n  ".join(unresolved))
    precompile(site)
    print("[4/6] 실행 파일·안내문", flush=True)
    _write(folder / WIN_LAUNCHER, windows_launcher_text())
    _write(folder / WIN_DOCTOR, doctor_launcher_text())
    _write(folder / WIN_README, readme_text())
    _write(folder / "VERSION.txt", version_text(git_sha(), datetime.now(timezone.utc), "windows"))
    print("[5/6] zip 묶기", flush=True)
    zip_path = out_parent / f"{WIN_DIR}.zip"
    zip_path.unlink(missing_ok=True)
    count = zip_folder(folder, zip_path)
    print(f"  {zip_path} — 파일 {count}개, {zip_path.stat().st_size / 1e6:.1f} MB", flush=True)
    print("[6/6] 검사", flush=True)
    report(folder, verify_windows_bundle(folder), ("python/Lib/site-packages",))
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        bad = [n for n in names if "\\" in n]
        korean = [i for i in archive.infolist() if not i.filename.isascii()]
        if bad or any(not i.flag_bits & 0x800 for i in korean) or f"{WIN_DIR}/python/python.exe" not in names:
            raise BuildError("zip 검사 실패(구분자·UTF-8 플래그·python.exe)")
    print("  Windows에서의 실행은 이 Mac에서 시험할 수 없습니다 — QA-SYS-4 꾸러미 경로가 첫 시험입니다.", flush=True)
    return folder


def build_macos(out_parent: Path, cache: Path) -> Path:
    folder = out_parent / MAC_DIR
    _fresh(folder)
    print("[1/3] 휠 모으기(macOS arm64, CPython " + ", ".join(MAC_PYTHONS) + ")", flush=True)
    wheels_dir = folder / "wheels"
    wheels_dir.mkdir()
    repo_wheel = build_repo_wheel(cache)
    shutil.copy2(repo_wheel, wheels_dir / repo_wheel.name)
    for version in MAC_PYTHONS:
        got = download_wheels(WINDOWS_WHEELS, cache / f"wheels-mac-cp{version.replace('.', '')}", MAC_PLATFORM, version)
        for wheel in got:
            shutil.copy2(wheel, wheels_dir / wheel.name)
        problems = missing_requirements(wheel_dists([repo_wheel, *got]), marker_environment("macos", version))
        if problems:
            raise BuildError(f"의존성 검사 실패(macOS, {version}):\n  " + "\n  ".join(problems))
    print("[2/3] 실행 파일", flush=True)
    launcher = folder / MAC_LAUNCHER
    _write(launcher, mac_launcher_text(repo_wheel.name, WINDOWS_WHEELS))
    launcher.chmod(0o755)
    _write(folder / "VERSION.txt", version_text(git_sha(), datetime.now(timezone.utc), "macos").replace("\r\n", "\n"))
    print("[3/3] 검사", flush=True)
    problems = [] if (wheels_dir / repo_wheel.name).is_file() else ["레포 휠 없음"]
    if not any(w.name.startswith("pymupdf-") for w in wheels_dir.iterdir()):
        problems.append("pymupdf 휠 없음")
    with zipfile.ZipFile(wheels_dir / repo_wheel.name) as archive:
        if "deepfake_lens/gui.js" not in archive.namelist():
            problems.append("레포 휠에 deepfake_lens/gui.js 없음")
    report(folder, problems, ("wheels",))
    return folder


def report(folder: Path, problems: list[str], expand: Iterable[str] = ()) -> None:
    lines, total = tree_summary(folder, expand)
    print(f"  {folder}", flush=True)
    for line in lines:
        print("  " + line)
    print(f"  합계 {total / 1e6:.1f} MB", flush=True)
    if problems:
        raise BuildError("꾸러미 검사 실패:\n  " + "\n  ".join(problems))
    print("  검사 통과", flush=True)


def main(argv: list[str] | None = None) -> int:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from deepfake_lens.cli_parser import KoreanArgumentParser

    parser = KoreanArgumentParser(
        description="설치 없이 더블클릭으로 쓰는 데스크톱 꾸러미를 만듭니다(비게이트). windows는 python.org 임베더블 Python과 "
        "고정된 휠을 담은 폴더와 zip을, macos는 처음 실행할 때 가상환경을 만드는 실행 파일과 휠 폴더를 만듭니다. "
        "내려받기는 python.org와 PyPI만 씁니다.",
    )
    parser.add_argument("--platform", choices=("windows", "macos"), required=True, help="만들 꾸러미의 운영체제")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "dist", metavar="<폴더>", help="꾸러미 폴더(와 zip)를 만들 상위 폴더(기본: 레포의 dist)")
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE, metavar="<폴더>", help="내려받은 파일을 보관할 폴더(다시 만들 때 재사용)")
    args = parser.parse_args(argv)
    out = args.out.expanduser().resolve()
    cache = args.cache.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        if args.platform == "windows":
            build_windows(out, cache)
        else:
            build_macos(out, cache)
    except BuildError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

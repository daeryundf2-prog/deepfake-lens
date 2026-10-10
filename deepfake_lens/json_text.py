"""Surrogate-safe JSON text (R11-1).

On POSIX a file name is bytes. Python hands a name that is not valid UTF-8
(a CP949 ``증거사진.png`` copied from a Korean Windows disk, say
``b"\\xc1\\xf5\\xb0\\xc5.png"``) to the program as a ``str`` with lone
surrogates U+DC80–U+DCFF in place of the undecodable bytes (PEP 383,
``os.fsdecode``). ``json.dumps(..., ensure_ascii=False)`` keeps those
surrogates as they are, and encoding the text as UTF-8 then fails
(``UnicodeEncodeError: surrogates not allowed``): before R11-1 one such
name made ``scan --json-out`` exit 2 with an empty file, the web server's
``/api/scan`` answer 400 with the English codec message and the API
server answer 500.

:func:`json_dumps` is ``json.dumps`` with ``ensure_ascii=False`` whose
result has every lone surrogate written as the JSON escape ``\\udcXX``. The
text is plain UTF-8, it is valid JSON, and ``json.loads`` gives back the
same surrogate-escaped ``str`` — so ``os.fsencode`` of a loaded path is the
file's original bytes and a signature over the canonical body verifies
after a round trip. Text without surrogates is byte-for-byte what
``json.dumps(..., ensure_ascii=False)`` gives (existing signatures and
pinned outputs are unchanged).

A surrogate can only occur inside a JSON string literal (the JSON syntax
itself is ASCII), so the escape is always written where an escape is
valid. Limitation: a high surrogate directly followed by a low surrogate
(two separate code points in a Python ``str``) is read back by any JSON
parser as the one astral character they encode; ``os.fsdecode`` never
yields such a pair (it only yields U+DC80–U+DCFF).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SURROGATE = re.compile("[\ud800-\udfff]")


def escape_surrogates(text: str) -> str:
    """``text`` with each lone surrogate written as the 6-character escape ``\\uXXXX``."""
    return _SURROGATE.sub(lambda match: f"\\u{ord(match.group()):04x}", text)


def json_dumps(obj: Any, **kwargs: Any) -> str:
    """``json.dumps(obj, ensure_ascii=False, **kwargs)`` with lone surrogates escaped (R11-1)."""
    kwargs.setdefault("ensure_ascii", False)
    return escape_surrogates(json.dumps(obj, **kwargs))


def json_bytes(obj: Any, **kwargs: Any) -> bytes:
    """:func:`json_dumps` as UTF-8 bytes — never raises on a surrogate-escaped name."""
    return json_dumps(obj, **kwargs).encode("utf-8")


def write_json(path: Path | str, obj: Any, **kwargs: Any) -> None:
    """Write :func:`json_dumps` of ``obj`` plus a newline to ``path`` as UTF-8."""
    Path(path).write_text(json_dumps(obj, **kwargs) + "\n", encoding="utf-8")


# R11-6 (round 11): JSON embedded in an HTML ``<script>`` element. Escaping
# only "</" left "<!--<script>" (a file name) in the element, which puts an
# HTML parser in the script "double-escaped" state, so the element's end is
# no longer where the report's own markup ends. The standard safe embedding
# writes every "<", ">", "&" and U+2028/U+2029 as a JSON escape: none of them
# can then appear raw in the element, and ``json.loads`` reads the same value.
_SCRIPT_UNSAFE = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", "\u2028": "\\u2028", "\u2029": "\\u2029"}
_SCRIPT_UNSAFE_RE = re.compile("[<>&\u2028\u2029]")


def script_safe_json(obj: Any, **kwargs: Any) -> str:
    """:func:`json_dumps` safe to place inside an HTML ``<script>`` element (R11-6)."""
    return _SCRIPT_UNSAFE_RE.sub(lambda match: _SCRIPT_UNSAFE[match.group()], json_dumps(obj, **kwargs))


# R12-4 (round 12): a path as URL-safe base64 of its file-system bytes.
# A browser cannot put a lone surrogate into a URL (encodeURIComponent throws
# "URI malformed") and a percent-encoded raw byte is decoded as U+FFFD by the
# server, so the GUI names files to /api/preview and /api/heatmap by these
# bytes — the exact os.fsencode of the path, whatever its encoding.
# R13-3 (round 13): /api/analyze-file and /api/scan take file_b64 / folder_b64
# the same way.
PATH_B64_INVALID = "경로 인코딩(path_b64·root_b64·file_b64·folder_b64)이 올바르지 않습니다 — 검사 결과의 값을 그대로 보내십시오"


def fs_b64encode(path: str) -> str:
    """URL-safe base64 (padded) of ``os.fsencode(path)``."""
    import base64
    import os

    return base64.urlsafe_b64encode(os.fsencode(path)).decode("ascii")


def fs_b64decode(value: str) -> str:
    """Inverse of :func:`fs_b64encode` (``os.fsdecode`` of the bytes); ValueError when malformed."""
    import base64
    import binascii
    import os

    text = value.strip()
    if not text or len(text) > 16384 or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", text):
        raise ValueError(PATH_B64_INVALID)
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValueError(PATH_B64_INVALID) from exc
    if not raw or b"\x00" in raw:
        raise ValueError(PATH_B64_INVALID)
    return os.fsdecode(raw)

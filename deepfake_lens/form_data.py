"""R30-1..R30-3, R30-5 (round 30): the one multipart/form-data parser of every upload path.

Uploads were parsed with :class:`email.parser.BytesParser`, a MIME *mail* parser. It interprets what a form-data
part declares about itself: a part sent as ``Content-Type: message/rfc822`` (a browser's ``.eml``) became a nested
message whose payload is ``None`` — the file vanished (R30-1); a part sent as ``multipart/related`` or
``multipart/mixed`` (a browser's ``.mht``) was parsed and re-serialized, so the analyzed bytes and the row's sha256
were not the file's (R30-2); and a quoted ``filename="…"`` was read as an RFC 822 quoted-string, so the backslash a
browser sends literally was removed (``a\\b.png`` became ``ab.png``, R30-3).

This parser does only what form-data needs (RFC 7578, with the HTML Standard's multipart/form-data encoding that
browsers and curl use):

- The body is split on delimiter lines ``--boundary`` at the start of a line (the first one may open the body,
  after an ignored preamble). A line ends in CRLF, as RFC 2046 writes it, or — R31-1 (round 31), as the e-mail
  parser this one replaced accepted — in a bare LF or a bare CR: the line end before a delimiter belongs to the
  delimiter, so a part's bytes end before it, and LF bytes inside a part are its own. A delimiter line ends in
  ``--`` (the close delimiter) or optional spaces/tabs and a line end; a boundary string followed by anything else
  is data. Everything after the close delimiter is ignored. A request has at most :data:`MAX_UPLOAD_PARTS` parts
  (round 31 — counted while parsing, nameless parts too, so a body of millions of tiny parts is refused before
  any row exists).
- Only part *headers* are read (header lines end like every line above; obsolete line folding unfolded). Of them
  only ``Content-Disposition`` is used: its ``filename*`` (RFC 5987 ``charset'lang'percent-encoded``, when well
  formed and not empty — R31-3) wins over ``filename``; a repeated parameter keeps its first value. An unquoted
  value ends at its line, so a bare LF in it ends the header (round 31: ``filename=a.png<LF>X-Evil: 1`` is
  ``a.png``, not a name holding a line break).
- A part's bytes are taken verbatim, whatever its ``Content-Type`` or ``Content-Transfer-Encoding`` says (RFC 7578
  §4.7: senders do not use a transfer encoding; a base64 part is the base64 text it is) — so a row's sha256 is the
  sha256 of the bytes that were sent.
- A quoted parameter value ends at the first ``"``; a backslash is an ordinary character (browsers do not escape
  it). The three percent sequences a browser writes for characters a quoted value cannot hold — ``%22`` (``"``),
  ``%0D`` (CR), ``%0A`` (LF), upper case as the HTML Standard writes them — are decoded; every other ``%`` is
  literal. A name that really contains ``%22`` therefore reads as ``"``: the encoding cannot tell them apart and
  this parser reads what browsers mean (BACKLOG BL-36).
- The name's bytes are decoded by :func:`decode_filename_bytes` (the declared ``filename*`` charset, UTF-8, CP949,
  else kept byte for byte as surrogate escapes — R12-10; a declared charset Python cannot decode with, such as
  ``undefined``, is skipped — R31-4).

Refusals are :class:`FormDataError` with a truthful Korean reason (R30-5): no ``boundary=`` (or an empty one), a
boundary that never delimits the body, a body that ends before its close delimiter (cut off — P8), a part whose
header block never ends, more than :data:`MAX_UPLOAD_PARTS` parts. :func:`is_form_data` reads the media type the
way RFC 9110 §8.3.1 defines it — the type before ``;``, case-insensitively (R31-2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote_to_bytes

# R30-5: the reasons a multipart body is refused, one per cause (all three used to be MULTIPART_INCOMPLETE).
MULTIPART_NO_BOUNDARY = "Content-Type에 multipart boundary가 없습니다 — boundary 매개변수를 넣어 다시 보내십시오"
MULTIPART_BOUNDARY_MISMATCH = "본문의 경계가 Content-Type의 boundary와 다릅니다 — 같은 boundary로 다시 보내십시오"
# P8 (round 8): a body cut off before its closing boundary was analyzed as if it were the whole file.
MULTIPART_INCOMPLETE = "업로드 본문이 잘렸습니다(닫는 경계 없음) — 파일을 다시 보내십시오"
MULTIPART_BAD_PART_HEADERS = "업로드 본문의 파트 헤더가 끝나지 않습니다(빈 줄 없음) — 파일을 다시 보내십시오"
# Round 31 (BACKLOG candidate): 280,000 tiny file parts gave 279,620 rows and an 81 MB response; 24 million nameless
# parts took 21 s and 1 GB to say 「업로드된 파일이 없습니다」. Every part counts — a file, a form field, a nameless one.
MAX_UPLOAD_PARTS = 1000
MULTIPART_TOO_MANY_PARTS = (
    "업로드 본문의 파트(파일·폼 필드)가 상한({cap}개)보다 많습니다 — 파일을 나누어 여러 요청으로 보내십시오"
)

# R12-10: encodings tried for a plain filename="…" (after a filename* charset).
FILENAME_ENCODINGS = ("utf-8", "cp949")

# The HTML Standard's multipart/form-data encoding of a name: '"' -> %22, CR -> %0D, LF -> %0A, nothing else.
_BROWSER_ESCAPES = re.compile(rb"%(22|0D|0A)")
_BROWSER_UNESCAPE = {b"22": b'"', b"0D": b"\r", b"0A": b"\n"}
_LWSP = b" \t"
# R31-1: a line end — CRLF, or a bare LF or CR (a CR followed by LF is one CRLF, never a CR and then an LF).
_EOL = rb"(?:\r\n|\n|\r(?!\n))"
_LINE_END = re.compile(_EOL)
_BLANK_LINE = re.compile(_EOL + _EOL)
_FOLD = re.compile(_EOL + rb"[ \t]")


class FormDataError(ValueError):
    """A multipart/form-data body this parser refuses; ``str()`` is the Korean reason (HTTP 400)."""


@dataclass(frozen=True)
class FormPart:
    """One form-data part: its file name (``None`` when the part names none) and its bytes as sent."""

    filename: str | None
    data: bytes


def decode_filename_bytes(raw: bytes, charset: str = "") -> str:
    """A file name's bytes as text: the declared charset, UTF-8, CP949, else surrogate escapes (R12-10)."""
    for encoding in ((charset,) if charset else ()) + FILENAME_ENCODINGS:
        try:
            return raw.decode(encoding)
        except (LookupError, ValueError):
            # R31-4 (round 31): ValueError, not only UnicodeDecodeError — the ``undefined`` codec raises UnicodeError
            # (its base class), which was a 500 on every upload path; a codec name with NUL is a ValueError too.
            continue
    return raw.decode("utf-8", "surrogateescape")


def _params(value: bytes) -> tuple[bytes, dict[bytes, bytes]]:
    """``(type, {lower-cased name: raw value})`` of a header value ``type; a=1; b="x"`` — first value wins.

    A quoted value ends at the first ``"`` and keeps backslashes (browsers send them literally, R30-3); anything
    between a closing quote and the next ``;`` is ignored.
    """
    head, _, rest = value.partition(b";")
    params: dict[bytes, bytes] = {}
    i, n = 0, len(rest)
    while i < n:
        while i < n and rest[i:i + 1] in (b" ", b"\t", b";"):
            i += 1
        eq = rest.find(b"=", i)
        semi = rest.find(b";", i)
        if eq < 0 or (0 <= semi < eq):
            i = n if semi < 0 else semi + 1  # a parameter without "=": skipped
            continue
        name = rest[i:eq].strip(_LWSP).lower()
        j = eq + 1
        while j < n and rest[j:j + 1] in (b" ", b"\t"):
            j += 1
        if rest[j:j + 1] == b'"':
            end = rest.find(b'"', j + 1)
            if end < 0:  # no closing quote: the value runs to the end of the header
                raw, j = rest[j + 1:], n
            else:
                raw, j = rest[j + 1:end], end + 1
            semi = rest.find(b";", j)
            i = n if semi < 0 else semi + 1
        else:
            semi = rest.find(b";", j)
            end = n if semi < 0 else semi
            raw, i = rest[j:end].strip(_LWSP), end + 1
        if name and name not in params:
            params[name] = raw
    return head.strip(_LWSP).lower(), params


def _filename(disposition: bytes) -> str | None:
    """The file name of a Content-Disposition value: ``filename*`` (RFC 5987) if well formed, else ``filename``."""
    _type, params = _params(disposition)
    star = params.get(b"filename*")
    if star is not None:
        charset, sep1, rest = star.partition(b"'")
        _lang, sep2, encoded = rest.partition(b"'")
        if sep1 and sep2:
            name = decode_filename_bytes(unquote_to_bytes(encoded), charset.strip(_LWSP).decode("ascii", "replace"))
            if name:
                return name
            # R31-3 (round 31): an empty filename* named no file and won over filename="a.png" — 「업로드된 파일이
            # 없습니다」 for a file that was sent. RFC 6266 §4.3: a filename* that cannot be used falls back to filename.
    plain = params.get(b"filename")
    if plain is None:
        return None
    return decode_filename_bytes(_BROWSER_ESCAPES.sub(lambda m: _BROWSER_UNESCAPE[m.group(1)], plain))


def _headers(block: bytes) -> dict[bytes, bytes]:
    """``{lower-cased name: value}`` of a part's header block (lines end as in the body — R31-1; obsolete folding
    unfolded; first wins)."""
    headers: dict[bytes, bytes] = {}
    for line in _LINE_END.split(_FOLD.sub(b" ", block)):
        name, colon, value = line.partition(b":")
        if colon:
            headers.setdefault(name.strip(_LWSP).lower(), value.strip(_LWSP))
    return headers


def is_form_data(content_type: str) -> bool:
    """R31-2 (round 31): whether a Content-Type is ``multipart/form-data`` — the media type before ``;``, compared
    case-insensitively (RFC 9110 §8.3.1). ``"multipart/form-data" in content_type`` refused ``Multipart/Form-Data``
    with "multipart/form-data 업로드가 필요합니다"."""
    return media_type(content_type) == "multipart/form-data"


def media_type(content_type: str) -> str:
    """The ``type/subtype`` of a Content-Type header, lower-cased, without parameters (R31-2)."""
    return content_type.split(";", 1)[0].strip(" \t").lower()


def boundary_of(content_type: str) -> bytes:
    """The ``boundary`` parameter of a Content-Type header, as bytes; :class:`FormDataError` when missing or empty."""
    _type, params = _params(content_type.encode("latin-1", "replace"))
    boundary = params.get(b"boundary", b"")
    if not boundary:
        raise FormDataError(MULTIPART_NO_BOUNDARY)
    return boundary


def _delimiter_end(body: bytes, at: int, dash: bytes) -> tuple[int, bool] | None:
    """If ``body[at:]`` starts a delimiter line ``dash`` (``--boundary``): ``(index after its line, is close)``.

    ``None`` when the boundary string is followed by anything but ``--`` or spaces/tabs and a line end (CRLF, LF or
    CR — R31-1; then it is data). :class:`FormDataError` (cut off) when the body ends inside the delimiter line.
    """
    i = at + len(dash)
    if body[i:i + 2] == b"--":
        return i + 2, True
    j = i
    while body[j:j + 1] in (b" ", b"\t"):
        j += 1
    if body[j:j + 2] == b"\r\n":
        return j + 2, False
    if body[j:j + 1] in (b"\n", b"\r"):
        return j + 1, False
    if j == len(body) or (j == i and body[j:j + 2] == b"-"):
        raise FormDataError(MULTIPART_INCOMPLETE)  # the body ends inside the delimiter line
    return None


def _at_line_start(body: bytes, at: int, start: int) -> int | None:
    """R31-1: where the line end before ``body[at:]`` begins if ``at`` starts a line, else ``None``.

    The line end (CRLF, else a bare LF or CR) must lie at or after ``start`` (inside the part being read); ``at ==
    start == 0`` is the very start of the body (an opening delimiter without a preamble).
    """
    if at == 0:
        return 0 if start == 0 else None
    if at - 2 >= start and body[at - 2:at] == b"\r\n":
        return at - 2
    if at - 1 >= start and body[at - 1:at] in (b"\n", b"\r"):
        return at - 1
    return None


def _next_delimiter(body: bytes, dash: bytes, start: int) -> tuple[int, int, int, bool] | None:
    """The first delimiter line at or after ``start``: ``(where the line end before it begins, where its ``--boundary``
    begins, index after its line, is close)``."""
    search = start
    while True:
        at = body.find(dash, search)
        if at < 0:
            return None
        line_end = _at_line_start(body, at, start)
        if line_end is not None:
            found = _delimiter_end(body, at, dash)
            if found is not None:
                return line_end, at, found[0], found[1]
        search = at + 1


def parse_form_data(content_type: str, body: bytes, *, max_parts: int = MAX_UPLOAD_PARTS) -> list[FormPart]:
    """Every part of a multipart/form-data ``body``, in order; :class:`FormDataError` with the reason it is refused."""
    dash = b"--" + boundary_of(content_type)
    # The opening delimiter: at the very start, or at the start of a line after a preamble (R31-1: a preamble that
    # ends in a bare LF counts — its file part was skipped and the close delimiter taken as the opening one).
    opening = _next_delimiter(body, dash, 0)
    if opening is None:
        last_line = body[max(body.rfind(b"\n"), body.rfind(b"\r")) + 1:]
        if dash.startswith(body.lstrip(b"\r\n")) or (last_line and dash.startswith(last_line)):
            raise FormDataError(MULTIPART_INCOMPLETE)  # the body ends before its first boundary is complete
        raise FormDataError(MULTIPART_BOUNDARY_MISMATCH)
    parts: list[FormPart] = []
    _line_end, _at, pos, closed = opening
    while not closed:
        if len(parts) >= max_parts:
            raise FormDataError(MULTIPART_TOO_MANY_PARTS.format(cap=max_parts))
        # One part: headers, a blank line, the bytes up to the line end before the next delimiter.
        found = _next_delimiter(body, dash, pos)
        if found is None:
            raise FormDataError(MULTIPART_INCOMPLETE)  # P8: no further delimiter — the body was cut off
        data_end, delimiter_at, next_pos, next_closed = found
        first = _LINE_END.match(body, pos, delimiter_at)
        if first is not None:  # no headers: the part starts with its blank line
            headers_block, data_start = b"", first.end()
        else:
            blank = _BLANK_LINE.search(body, pos, delimiter_at)
            if blank is None:
                raise FormDataError(MULTIPART_BAD_PART_HEADERS)
            headers_block, data_start = body[pos:blank.start()], blank.end()
        disposition = _headers(headers_block).get(b"content-disposition")
        filename = _filename(disposition) if disposition is not None else None
        parts.append(FormPart(filename, body[data_start:data_end] if data_start <= data_end else b""))
        pos, closed = next_pos, next_closed
    return parts


def file_parts(content_type: str, body: bytes) -> list[tuple[str, bytes]]:
    """``(client name, bytes)`` of every part that is a file — it names a non-empty file name; empty bytes or not."""
    return [(part.filename, part.data) for part in parse_form_data(content_type, body) if part.filename]

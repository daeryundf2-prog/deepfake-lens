"""R30-1..R30-3, R30-5 (round 30): the one multipart/form-data parser of every upload path.

Uploads were parsed with :class:`email.parser.BytesParser`, a MIME *mail* parser. It interprets what a form-data
part declares about itself: a part sent as ``Content-Type: message/rfc822`` (a browser's ``.eml``) became a nested
message whose payload is ``None`` — the file vanished (R30-1); a part sent as ``multipart/related`` or
``multipart/mixed`` (a browser's ``.mht``) was parsed and re-serialized, so the analyzed bytes and the row's sha256
were not the file's (R30-2); and a quoted ``filename="…"`` was read as an RFC 822 quoted-string, so the backslash a
browser sends literally was removed (``a\\b.png`` became ``ab.png``, R30-3).

This parser does only what form-data needs (RFC 7578, with the HTML Standard's multipart/form-data encoding that
browsers and curl use):

- The body is split on the delimiter ``CRLF--boundary`` (the first one may open the body, after an ignored
  preamble). A delimiter line ends in ``--`` (the close delimiter) or optional spaces/tabs and CRLF; a boundary
  string followed by anything else is data. Everything after the close delimiter is ignored.
- Only part *headers* are read (obsolete line folding unfolded). Of them only ``Content-Disposition`` is used:
  its ``filename*`` (RFC 5987 ``charset'lang'percent-encoded``, when well formed) wins over ``filename``; a
  repeated parameter keeps its first value.
- A part's bytes are taken verbatim, whatever its ``Content-Type`` or ``Content-Transfer-Encoding`` says (RFC 7578
  §4.7: senders do not use a transfer encoding; a base64 part is the base64 text it is) — so a row's sha256 is the
  sha256 of the bytes that were sent.
- A quoted parameter value ends at the first ``"``; a backslash is an ordinary character (browsers do not escape
  it). The three percent sequences a browser writes for characters a quoted value cannot hold — ``%22`` (``"``),
  ``%0D`` (CR), ``%0A`` (LF), upper case as the HTML Standard writes them — are decoded; every other ``%`` is
  literal. A name that really contains ``%22`` therefore reads as ``"``: the encoding cannot tell them apart and
  this parser reads what browsers mean (BACKLOG BL-36).
- The name's bytes are decoded by :func:`decode_filename_bytes` (the declared ``filename*`` charset, UTF-8, CP949,
  else kept byte for byte as surrogate escapes — R12-10).

Refusals are :class:`FormDataError` with a truthful Korean reason (R30-5): no ``boundary=`` (or an empty one), a
boundary that never delimits the body, a body that ends before its close delimiter (cut off — P8), a part whose
header block never ends.
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

# R12-10: encodings tried for a plain filename="…" (after a filename* charset).
FILENAME_ENCODINGS = ("utf-8", "cp949")

# The HTML Standard's multipart/form-data encoding of a name: '"' -> %22, CR -> %0D, LF -> %0A, nothing else.
_BROWSER_ESCAPES = re.compile(rb"%(22|0D|0A)")
_BROWSER_UNESCAPE = {b"22": b'"', b"0D": b"\r", b"0A": b"\n"}
_LWSP = b" \t"


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
        except (LookupError, UnicodeDecodeError):
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
            return decode_filename_bytes(unquote_to_bytes(encoded), charset.strip(_LWSP).decode("ascii", "replace"))
    plain = params.get(b"filename")
    if plain is None:
        return None
    return decode_filename_bytes(_BROWSER_ESCAPES.sub(lambda m: _BROWSER_UNESCAPE[m.group(1)], plain))


def _headers(block: bytes) -> dict[bytes, bytes]:
    """``{lower-cased name: value}`` of a part's header block (CRLF lines; obsolete folding unfolded; first wins)."""
    headers: dict[bytes, bytes] = {}
    for line in re.sub(rb"\r\n[ \t]", b" ", block).split(b"\r\n"):
        name, colon, value = line.partition(b":")
        if colon:
            headers.setdefault(name.strip(_LWSP).lower(), value.strip(_LWSP))
    return headers


def boundary_of(content_type: str) -> bytes:
    """The ``boundary`` parameter of a Content-Type header, as bytes; :class:`FormDataError` when missing or empty."""
    _type, params = _params(content_type.encode("latin-1", "replace"))
    boundary = params.get(b"boundary", b"")
    if not boundary:
        raise FormDataError(MULTIPART_NO_BOUNDARY)
    return boundary


def _delimiter_end(body: bytes, at: int, dash: bytes) -> tuple[int, bool] | None:
    """If ``body[at:]`` starts a delimiter line ``dash`` (``--boundary``): ``(index after its line, is close)``.

    ``None`` when the boundary string is followed by anything but ``--`` or spaces/tabs and CRLF (then it is data).
    :class:`FormDataError` (cut off) when the body ends inside the delimiter line.
    """
    i = at + len(dash)
    if body[i:i + 2] == b"--":
        return i + 2, True
    j = i
    while body[j:j + 1] in (b" ", b"\t"):
        j += 1
    if body[j:j + 2] == b"\r\n":
        return j + 2, False
    tail = body[j:j + 2]
    if len(tail) < 2 and (b"\r\n".startswith(tail) or (j == i and b"--".startswith(tail))):
        raise FormDataError(MULTIPART_INCOMPLETE)  # the body ends inside the delimiter line
    return None


def parse_form_data(content_type: str, body: bytes) -> list[FormPart]:
    """Every part of a multipart/form-data ``body``, in order; :class:`FormDataError` with the reason it is refused."""
    dash = b"--" + boundary_of(content_type)
    delimiter = b"\r\n" + dash
    # The opening delimiter: at the very start, or after a preamble line.
    found = None
    at = 0 if body.startswith(dash) else body.find(delimiter)
    while at >= 0:
        start = at if at == 0 and body.startswith(dash) else at + 2
        found = _delimiter_end(body, start, dash)
        if found is not None:
            break
        at = body.find(delimiter, start + 1)
    if found is None:
        if dash.startswith(body.lstrip(b"\r\n")):  # the body ends before its first boundary is complete
            raise FormDataError(MULTIPART_INCOMPLETE)
        raise FormDataError(MULTIPART_BOUNDARY_MISMATCH)
    parts: list[FormPart] = []
    pos, closed = found
    while not closed:
        # One part: headers, a blank line, the bytes up to the next delimiter.
        search = pos
        while True:
            at = body.find(delimiter, search)
            if at < 0:
                raise FormDataError(MULTIPART_INCOMPLETE)  # P8: no further delimiter — the body was cut off
            found = _delimiter_end(body, at + 2, dash)
            if found is not None:
                break
            search = at + 1
        if body.startswith(b"\r\n", pos):
            headers_block, data_start = b"", pos + 2
        else:
            blank = body.find(b"\r\n\r\n", pos, at + 2)
            if blank < 0:
                raise FormDataError(MULTIPART_BAD_PART_HEADERS)
            headers_block, data_start = body[pos:blank], blank + 4
        disposition = _headers(headers_block).get(b"content-disposition")
        filename = _filename(disposition) if disposition is not None else None
        parts.append(FormPart(filename, body[data_start:at] if data_start <= at else b""))
        pos, closed = found
    return parts


def file_parts(content_type: str, body: bytes) -> list[tuple[str, bytes]]:
    """``(client name, bytes)`` of every part that is a file — it names a non-empty file name; empty bytes or not."""
    return [(part.filename, part.data) for part in parse_form_data(content_type, body) if part.filename]

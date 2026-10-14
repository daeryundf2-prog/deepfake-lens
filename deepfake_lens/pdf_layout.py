"""Measured page layout for the Korean PDF reports (round 5, G2).

The forensic PDF and the evidence-statement PDF used to place text at fixed
coordinates with fixed character cuts (``band_str[:8]``, ``sig_str[:18]``,
``sha256[:32]``) and call ``insert_textbox`` without checking its result.
The verifier found 18 lines running past the 595 pt page edge, the 결론 column
painted over by the 등급 column, the 무결성/해석 고지 box empty (PyMuPDF writes
nothing when a textbox overflows) and a SHA-256 cut to 62 of 64 characters.

:class:`PdfLayout` replaces that with one rule: every string is measured
before it is drawn.

* Columns come from the content width (page width minus margins) —
  :meth:`PdfLayout.columns` — never from hand-placed x offsets.
* Text is wrapped by measured string width (:meth:`PdfLayout.wrap`): at
  spaces when possible, inside a token (a path, a hash) when it alone is
  wider than the column. Nothing is cut and no ellipsis is added.
* :meth:`PdfLayout.draw_lines` draws pre-wrapped lines with
  ``insert_textbox`` and checks its return value; a negative value (the
  text did not fit, nothing written) grows the box by the shortfall and
  draws again.
* Rows that do not fit on the page continue on a new page
  (:meth:`PdfLayout.draw_row`), splitting tall cells line by line and
  repeating the table header.

Every glyph therefore lies inside ``page.rect`` deflated by
:data:`PAGE_MARGINS` — ``tests/test_pdf_layout.py`` checks that with
``page.get_text("dict")`` span boxes on reports rendered from a hostile
folder.

Font: PyMuPDF's built-in CJK font (``Font("cjk")``, Droid Sans Fallback) is
embedded and subset, so Latin letters, Hangul, digits and hex all have real
metrics (the base-14 ``korea`` CID font drew every Latin character a full em
wide and gave no width information). When a PyMuPDF build lacks the CJK
font, the ``korea`` font is used and measured with ``get_text_length``.

R23-4 (round 23): that font has no glyph for Hebrew, Arabic, Thai,
Devanagari, emoji and many other scripts; PyMuPDF drew such a character as
an empty glyph (text extraction: U+0000), so ``note עברית.txt`` and
``note עבריה.txt`` — two evidence files — were printed identically in the
forensic PDF, the evidence statement and the legal report, while HTML and
JSON were right. Every string is now made drawable before it is measured or
drawn (:meth:`PdfLayout.drawable`): a code point the font has no glyph for
(``Font.has_glyph``) is written as ``\\xNN`` / ``\\uNNNN`` /
``\\UNNNNNNNN`` — the escapes ``result_text.display_name`` uses. File
names reach the PDF through ``display_name`` (every literal backslash
doubled, every escape a single backslash followed by its code), so the
printed text still decodes to exactly one name: two different names never
print alike (R10-1/R11-4/R18-11). No broader font is bundled (none ships
with the pinned PyMuPDF; adding one would need an offline copy with a
compatible license) — the escape is the rule.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .result_text import _escape_code

logger = logging.getLogger(__name__)

# Page geometry (pt). A4 portrait; every glyph stays inside the margins.
PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0
MARGIN_X = 40.0
MARGIN_TOP = 26.0
MARGIN_BOTTOM = 26.0
PAGE_MARGINS = (MARGIN_X, MARGIN_TOP, MARGIN_X, MARGIN_BOTTOM)  # left, top, right, bottom
# Space reserved at the bottom of every page for the page number line.
FOOTER_HEIGHT = 16.0
# Inner padding of a table cell and the gap between cells.
CELL_PAD_X = 3.0
CELL_PAD_Y = 3.0
COLUMN_GUTTER = 4.0
# Retries of insert_textbox when PyMuPDF wraps a line differently from the
# measurement (each retry grows the box by the reported shortfall).
TEXTBOX_RETRIES = 4

# Line metrics of the "korea" CID fallback font as insert_textbox applies
# them (measured: 1.309 em per line, 0.266 em descent below the last line).
FALLBACK_LINE_FACTOR = 1.309
FALLBACK_DESCENT_FACTOR = 0.266

EMBEDDED_FONT_NAME = "dflcjk"
FALLBACK_FONT_NAME = "korea"

Color = tuple[float, float, float]


@dataclass
class Cell:
    """One table cell: column index, text and style."""

    column: int
    text: str
    size: float = 7.5
    color: Color = (0.15, 0.15, 0.15)
    lines: list[str] = field(default_factory=list)


class PdfLayout:
    """A document being laid out page by page with measured text."""

    def __init__(
        self,
        pymupdf: Any,
        *,
        header: Callable[["PdfLayout"], None] | None = None,
        page_width: float = PAGE_WIDTH,
        page_height: float = PAGE_HEIGHT,
    ) -> None:
        self.fitz = pymupdf
        self.doc = pymupdf.open()
        self.page_width = page_width
        self.page_height = page_height
        self.left = MARGIN_X
        self.right = page_width - MARGIN_X
        self.top = MARGIN_TOP
        self.bottom = page_height - MARGIN_BOTTOM - FOOTER_HEIGHT
        self._header = header
        self.page: Any = None
        self.y = self.top
        self._font: Any = None
        self._font_buffer: bytes | None = None
        # R23-4: the font whose glyphs are checked (the embedded one; the korea fallback is checked with
        # PyMuPDF's own copy of it when it has one) and the per-character answers.
        self._glyph_font: Any = None
        self._glyph_cache: dict[str, bool] = {}
        try:
            font = pymupdf.Font("cjk")
            self._font, self._font_buffer = font, font.buffer
            self._glyph_font = font
            self.fontname = EMBEDDED_FONT_NAME
        except Exception:  # noqa: BLE001 - a PyMuPDF build without the CJK font: measured fallback
            logger.info("PyMuPDF CJK font unavailable; using the korea CID font", exc_info=True)
            self.fontname = FALLBACK_FONT_NAME
            try:
                self._glyph_font = pymupdf.Font(FALLBACK_FONT_NAME)
            except Exception:  # noqa: BLE001 - no glyph table: ASCII and Hangul syllables only (below)
                self._glyph_font = None

    # -- glyphs (R23-4) ---------------------------------------------------

    def _has_glyph(self, char: str) -> bool:
        known = self._glyph_cache.get(char)
        if known is None:
            code = ord(char)
            if self._glyph_font is not None:
                try:
                    known = bool(self._glyph_font.has_glyph(code))
                except Exception:  # noqa: BLE001 - treat as missing: the escape is always drawable
                    known = False
            else:
                known = 0x20 <= code < 0x7F or 0xAC00 <= code <= 0xD7A3
            self._glyph_cache[char] = known
        return known

    def drawable(self, text: str) -> str:
        """R23-4: ``text`` with every code point the font cannot draw written as its escape (``\\uNNNN`` …).

        Line breaks and spaces stay; the escapes are printable ASCII.
        """
        text = str(text)
        if text.isascii() and all(char in "\n " or 0x20 <= ord(char) < 0x7F for char in text):
            return text
        return "".join(char if char in "\n " or self._has_glyph(char) else _escape_code(ord(char)) for char in text)

    # -- geometry ---------------------------------------------------------

    @property
    def content_width(self) -> float:
        return self.right - self.left

    def columns(self, weights: Sequence[float], *, left: float | None = None, right: float | None = None) -> list[tuple[float, float]]:
        """Column ``(x0, x1)`` pairs splitting [left, right] by ``weights`` (gutters between)."""
        x0 = self.left if left is None else left
        x1 = self.right if right is None else right
        usable = (x1 - x0) - COLUMN_GUTTER * (len(weights) - 1)
        total = float(sum(weights))
        out: list[tuple[float, float]] = []
        cursor = x0
        for index, weight in enumerate(weights):
            width = usable * weight / total
            end = x1 if index == len(weights) - 1 else cursor + width
            out.append((cursor, end))
            cursor = end + COLUMN_GUTTER
        return out

    # -- measurement ------------------------------------------------------

    def text_width(self, text: str, size: float) -> float:
        if self._font is not None:
            return float(self._font.text_length(text, fontsize=size))
        return float(self.fitz.get_text_length(text, fontname=self.fontname, fontsize=size))

    def line_height(self, size: float) -> float:
        """Baseline-to-baseline distance ``insert_textbox`` uses (ascender - descender)."""
        if self._font is not None:
            return size * (float(self._font.ascender) - float(self._font.descender))
        return size * FALLBACK_LINE_FACTOR

    def _descent(self, size: float) -> float:
        if self._font is not None:
            return size * -float(self._font.descender)
        return size * FALLBACK_DESCENT_FACTOR

    def wrap(self, text: str, width: float, size: float) -> list[str]:
        """``text`` broken into lines no wider than ``width`` (measured), keeping every character.

        R23-4: measured and returned in its drawable form (:meth:`drawable`).
        """
        lines: list[str] = []
        for paragraph in self.drawable(str(text)).split("\n"):
            lines.extend(self._wrap_paragraph(paragraph, width, size))
        return lines or [""]

    def _wrap_paragraph(self, paragraph: str, width: float, size: float) -> list[str]:
        if self.text_width(paragraph, size) <= width:
            return [paragraph]
        lines: list[str] = []
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if self.text_width(candidate, size) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            # The word alone may be wider than the column (a path, a hash):
            # break it between characters.
            while self.text_width(word, size) > width:
                cut = self._fit_prefix(word, width, size)
                lines.append(word[:cut])
                word = word[cut:]
            current = word
        if current or not lines:
            lines.append(current)
        return lines

    def _fit_prefix(self, word: str, width: float, size: float) -> int:
        lo, hi = 1, len(word)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.text_width(word[:mid], size) <= width:
                lo = mid
            else:
                hi = mid - 1
        return max(1, lo)

    def block_height(self, lines: Sequence[str], size: float) -> float:
        """Height ``insert_textbox`` needs for ``lines``: n line heights plus the last line's descent."""
        if not lines:
            return 0.0
        return len(lines) * self.line_height(size) + self._descent(size)

    def lines_that_fit(self, room: float, size: float) -> int:
        return max(0, int((room - self._descent(size)) // self.line_height(size)))

    # -- pages ------------------------------------------------------------

    def new_page(self) -> Any:
        self.page = self.doc.new_page(width=self.page_width, height=self.page_height)
        if self._font_buffer is not None:
            self.page.insert_font(fontname=self.fontname, fontbuffer=self._font_buffer)
        self.y = self.top
        if self._header is not None:
            self._header(self)
        return self.page

    def ensure_space(self, height: float) -> bool:
        """Start a new page unless ``height`` fits below the cursor; True when a page was added."""
        if self.page is None or self.y + height > self.bottom:
            self.new_page()
            return True
        return False

    # -- drawing ----------------------------------------------------------

    def draw_lines(self, x0: float, x1: float, y: float, lines: Sequence[str], size: float, color: Color, *, align: int = 0) -> float:
        """Draw pre-wrapped ``lines`` in [x0, x1] from ``y``; returns the height used.

        ``insert_textbox`` returns the unused height, negative when the
        text did not fit — and then writes nothing. The box is grown by the
        shortfall and the call repeated, so no line is ever dropped.
        """
        if not lines or not any(line.strip() for line in lines):
            return 0.0
        lines = [self.drawable(line) for line in lines]  # R23-4: lines not made by wrap() too
        text = "\n".join(lines)
        height = self.block_height(lines, size) + 0.5
        for _ in range(TEXTBOX_RETRIES):
            rect = self.fitz.Rect(x0, y, x1, y + height)
            spare = self.page.insert_textbox(rect, text, fontname=self.fontname, fontsize=size, color=color, align=align)
            if spare >= 0:
                return height - spare
            height += -spare + 0.5
        raise RuntimeError(f"PDF 텍스트 배치 실패: {len(lines)}줄을 {x1 - x0:.0f}pt 폭에 배치하지 못했습니다")

    def text(self, x0: float, x1: float, text: str, size: float, color: Color = (0.15, 0.15, 0.15), *, align: int = 0, gap: float = 1.5) -> float:
        """Wrap ``text`` to [x0, x1] and draw it at the cursor (new page if needed); advances the cursor."""
        lines = self.wrap(text, x1 - x0, size)
        return self.flow_lines(x0, x1, lines, size, color, align=align, gap=gap)

    def flow_lines(self, x0: float, x1: float, lines: Sequence[str], size: float, color: Color, *, align: int = 0, gap: float = 1.5) -> float:
        """Draw lines at the cursor, continuing on new pages as needed; returns the height used on the last page."""
        remaining = list(lines)
        used = 0.0
        while remaining:
            fit = self.lines_that_fit(self.bottom - self.y, size)
            if fit == 0:
                self.new_page()
                continue
            chunk, remaining = remaining[:fit], remaining[fit:]
            used = self.draw_lines(x0, x1, self.y, chunk, size, color, align=align)
            self.y += used
            if remaining:
                self.new_page()
        self.y += gap
        return used

    def draw_row(
        self,
        columns: Sequence[tuple[float, float]],
        cells: Sequence[Cell],
        *,
        fill: Color | None = None,
        rule: Color | None = (0.9, 0.92, 0.94),
        min_height: float = 0.0,
        on_new_page: Callable[["PdfLayout"], None] | None = None,
    ) -> None:
        """Draw one table row; a row taller than the page continues on the next one.

        Each cell's text is wrapped to its column; the row is as tall as
        its tallest cell. When the row does not fit below the cursor it
        moves to a new page (``on_new_page`` redraws the table header);
        when it is taller than a whole page its cells are split line by
        line across pages.
        """
        for cell in cells:
            x0, x1 = columns[cell.column]
            cell.lines = self.wrap(cell.text, (x1 - x0) - 2 * CELL_PAD_X, cell.size)
        heights = [self.block_height(cell.lines, cell.size) for cell in cells]
        full = max([min_height, *heights]) + 2 * CELL_PAD_Y + 1.0
        page_room = self.bottom - self.top - 60.0
        if self.page is None or (self.y + full > self.bottom and full <= page_room):
            self.new_page()
            if on_new_page is not None:
                on_new_page(self)
        pending = [list(cell.lines) for cell in cells]
        while any(pending):
            room = self.bottom - self.y - 2 * CELL_PAD_Y - 1.0
            chunks: list[list[str]] = []
            for cell, lines in zip(cells, pending):
                fit = self.lines_that_fit(room, cell.size)
                chunks.append(lines[:fit])
            if not any(chunks):
                self.new_page()
                if on_new_page is not None:
                    on_new_page(self)
                continue
            height = max(
                [min_height if all(len(c) == len(p) for c, p in zip(chunks, pending)) else 0.0]
                + [self.block_height(chunk, cell.size) for cell, chunk in zip(cells, chunks)]
            ) + 2 * CELL_PAD_Y + 1.0
            top = self.y
            if fill is not None:
                self.page.draw_rect(self.fitz.Rect(self.left, top, self.right, top + height), color=fill, fill=fill)
            for cell, chunk in zip(cells, chunks):
                x0, x1 = columns[cell.column]
                self.draw_lines(x0 + CELL_PAD_X, x1 - CELL_PAD_X, top + CELL_PAD_Y, chunk, cell.size, cell.color)
            if rule is not None:
                self.page.draw_line(self.fitz.Point(self.left, top + height), self.fitz.Point(self.right, top + height), color=rule, width=0.5)
            self.y = top + height
            pending = [lines[len(chunk):] for lines, chunk in zip(pending, chunks)]
            if any(pending):
                self.new_page()
                if on_new_page is not None:
                    on_new_page(self)

    def boxed_text(
        self,
        title: str,
        body_lines: Sequence[tuple[str, float, Color]],
        *,
        title_size: float = 8.0,
        title_color: Color = (0.35, 0.35, 0.35),
        border: Color = (0.85, 0.88, 0.92),
        fill: Color = (0.98, 0.98, 0.99),
        pad: float = 6.0,
    ) -> None:
        """A bordered box whose height is its measured content (moved to a new page whole when it fits one)."""
        inner_x0, inner_x1 = self.left + pad, self.right - pad
        title_lines = self.wrap(title, inner_x1 - inner_x0, title_size) if title else []
        wrapped = [(self.wrap(text, inner_x1 - inner_x0, size), size, color) for text, size, color in body_lines]
        content = self.block_height(title_lines, title_size) + sum(self.block_height(lines, size) + 1.5 for lines, size, _ in wrapped)
        height = content + 2 * pad
        if height <= self.bottom - self.top:
            self.ensure_space(height)
            self.page.draw_rect(self.fitz.Rect(self.left, self.y, self.right, self.y + height), color=border, fill=fill)
            y = self.y + pad
            if title_lines:
                y += self.draw_lines(inner_x0, inner_x1, y, title_lines, title_size, title_color)
            for lines, size, color in wrapped:
                y += self.draw_lines(inner_x0, inner_x1, y, lines, size, color) + 1.5
            self.y += height + 4.0
            return
        # Taller than a page: no frame, the text flows across pages.
        if title_lines:
            self.flow_lines(inner_x0, inner_x1, title_lines, title_size, title_color)
        for lines, size, color in wrapped:
            self.flow_lines(inner_x0, inner_x1, lines, size, color)

    def footer(self, text_for: Callable[[int, int], str], *, size: float = 8.0, color: Color = (0.5, 0.5, 0.5)) -> None:
        """Page numbers on every page, centered inside the bottom margin."""
        total = self.doc.page_count
        for index in range(total):
            page = self.doc[index]
            self.page = page
            y = self.page_height - MARGIN_BOTTOM - FOOTER_HEIGHT + 3.0
            self.draw_lines(self.left, self.right, y, [text_for(index + 1, total)], size, color, align=1)

    def to_bytes(self) -> bytes:
        """The finished PDF: embedded font subset to the glyphs used, compressed."""
        if self._font_buffer is not None:
            try:
                self.doc.subset_fonts()
            except Exception:  # noqa: BLE001 - subsetting is a size optimization only
                logger.info("PDF font subsetting skipped", exc_info=True)
        data: bytes = self.doc.tobytes(garbage=3, deflate=True)
        self.doc.close()
        return data


__all__ = [
    "Cell",
    "FOOTER_HEIGHT",
    "MARGIN_BOTTOM",
    "MARGIN_TOP",
    "MARGIN_X",
    "PAGE_HEIGHT",
    "PAGE_MARGINS",
    "PAGE_WIDTH",
    "PdfLayout",
]

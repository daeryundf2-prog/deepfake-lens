"""R10-1/R10-2 (round 10): a file name can never rewrite a report row.

A file name is attacker-controlled text. Printed raw, a CR in a name
overwrote the CLI table row (an A1111 image whose JSON verdict is 조작·생성
근거 있음 read "원본성 근거 있음 … family_photo.png"), an ESC sequence
reached the terminal, and a LF or "|" in a name forged a Markdown
evidence-statement row ("| **갑 제9호증** | … 원본성 근거 있음 |") or shifted
its columns. Every rendering (CLI table, Markdown, CSV, HTML, PDF) shows
names through ``result_text.display_name``; CSV cells that start with a
formula character are prefixed with "'" (OWASP CSV injection).
"""

from __future__ import annotations

import contextlib
import csv
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from deepfake_lens import cli
from deepfake_lens.cli_render import CSV_VERDICT_COLUMN, TABLE_RULE
from deepfake_lens.result_text import csv_cell, display_name, markdown_cell
from deepfake_lens.result_types import VERDICT_LABELS, Verdict

HAVE_PYMUPDF = importlib.util.find_spec("pymupdf") is not None or importlib.util.find_spec("fitz") is not None
A1111 = Path(__file__).resolve().parents[2] / "fixtures" / "benchmark" / "a1111-metadata-marker.png"
MANIPULATION = VERDICT_LABELS[Verdict.MANIPULATION_EVIDENCE]
AUTHENTICITY = VERDICT_LABELS[Verdict.AUTHENTICITY_EVIDENCE]
# Hostile names, every one an A1111 PNG (조작·생성 근거 있음 by its metadata).
# R11-9 (round 11): invisible characters are written as escape sequences
# (\u200b, \u202e …), never raw in the source (ruff PLE2502/PLE2515).
HOSTILE_NAMES = (
    f"x\r{AUTHENTICITY}    근거   결정 1·통계 0·어휘 0     실행 3·미실행 5·실패 0      이미지    family_photo.png",
    f"a\n| **갑 제9호증** | 위조 행 | {AUTHENTICITY} |.png",
    "e\x1b[2K\x1b[1Aesc.png",
    f"p | {AUTHENTICITY} | q.png",
    "z\u200bw\u202eflip.png",
    "t\tab.png",
    "b\\|slash.png",
    '=HYPERLINK("http:evil","x").png',
    "@SUM(1+1).png",
    "+cmd.png",
    "-2+3.png",
)
RAW_CONTROLS = ("\r", "\x1b", "\u200b", "\u202e", "\t")


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def _markdown_cells(row: str) -> list[str]:
    """Cells of one Markdown table row, split on unescaped "|" only (GFM)."""
    cells: list[str] = []
    current: list[str] = []
    backslashes = 0
    for char in row:
        if char == "|" and backslashes % 2 == 0:
            cells.append("".join(current))
            current = []
        else:
            current.append(char)
        backslashes = backslashes + 1 if char == "\\" else 0
    cells.append("".join(current))
    return [cell.strip() for cell in cells[1:-1]]


class _ResultRows(HTMLParser):
    """Rows of the HTML report's result table (those with a SHA-256 cell)."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._has_hash = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row, self._has_hash = [], False
        elif tag == "td" and self._row is not None:
            self._cell = []
            if ("class", "sha256") in attrs:
                self._has_hash = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._row is not None and self._cell is not None:
            self._row.append("".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._has_hash:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


class DisplayNameUnitTest(unittest.TestCase):
    def test_controls_pipes_and_invisible_characters_are_escaped(self) -> None:
        self.assertEqual(display_name("a\r\n\t\x1bb"), "a\\r\\n\\t\\x1bb")
        self.assertEqual(display_name("z\u200bw\u202e\u2028"), "z\\u200bw\\u202e\\u2028")
        self.assertEqual(display_name("p | q"), "p \\| q")
        # A literal backslash before "|" is doubled so the "|" stays escaped.
        self.assertEqual(display_name("b\\|s"), "b\\\\\\|s")
        self.assertEqual(display_name("plain 한글.png"), "plain 한글.png")
        self.assertEqual(display_name("\udcff"), "\\udcff")  # an undecodable POSIX byte

    def test_markdown_cell_keeps_escaped_pipes_and_breaks_lines(self) -> None:
        self.assertEqual(markdown_cell("a\nb | c"), "a<br>b \\| c")
        # R12-5 (round 12): display_name's backslash-pipe was kept as is, so it
        # rendered as a bare "|" (not as shown); its backslash is escaped too now.
        self.assertEqual(markdown_cell(display_name("p | q")), "p \\\\\\| q")
        for raw in ("b\\|s", "b\\\\|s", "a\n| **갑 제9호증** | 위조 |"):
            cell = markdown_cell(display_name(raw))
            self.assertEqual(len(_markdown_cells(f"| {cell} | x |")), 2, raw)

    def test_csv_cell_guards_formulas(self) -> None:
        for raw in ("=1+1", "+1", "-1", "@SUM(1)", "\t=1", "\r=1"):
            self.assertTrue(str(csv_cell(raw)).startswith("'"), raw)
        # R11-13 (round 11): fullwidth "＝＋－＠" (and other compatibility forms
        # NFKC maps onto a formula character) were not guarded.
        for raw in ("\uff1d1+1", "\uff0b1", "\uff0d1", "\uff20SUM(1)", "\ufe661", "\ufe621", "\ufe631", "\ufe6bx"):
            self.assertEqual(csv_cell(raw), "'" + raw, raw)
        self.assertEqual(csv_cell("a\uff1d1"), "a\uff1d1")  # not at the start
        self.assertEqual(csv_cell("a.png"), "a.png")
        self.assertEqual(csv_cell(-3), -3)  # a number stays a number
        self.assertEqual(csv_cell(None), None)


# R11-4 (round 11): an alphabet of every character display_name treats
# specially, the letters its escapes use, and plain text.
PROPERTY_ALPHABET = (
    "\\", "|", "n", "r", "t", "x", "u", "U", "0", "1", "a", "f", ":", " ",
    "\n", "\r", "\t", "\x1b", "\x00", "\x85", "\u200b", "\u202e", "\u2028", "\udcc1", "\udcff", "한", "\U0001f600",
)
_SIMPLE = {"\\": "\\", "|": "|", "n": "\n", "r": "\r", "t": "\t"}
_HEX_WIDTH = {"x": 2, "u": 4, "U": 8}


def undisplay_name(shown: str) -> str:
    """The inverse of display_name (R11-4): raises ValueError on a malformed escape."""
    out: list[str] = []
    index = 0
    while index < len(shown):
        char = shown[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        code = shown[index + 1] if index + 1 < len(shown) else ""
        if code in _SIMPLE:
            out.append(_SIMPLE[code])
            index += 2
        elif code in _HEX_WIDTH:
            width = _HEX_WIDTH[code]
            digits = shown[index + 2:index + 2 + width]
            if len(digits) != width:
                raise ValueError(shown)
            out.append(chr(int(digits, 16)))
            index += 2 + width
        else:
            raise ValueError(shown)
    return "".join(out)


def random_names(count: int, seed: int) -> list[str]:
    import random

    rng = random.Random(seed)
    return ["".join(rng.choice(PROPERTY_ALPHABET) for _ in range(rng.randint(0, 10))) for _ in range(count)]


class DisplayNameInjectiveTest(unittest.TestCase):
    """R11-4 (round 11): display_name is injective — "bs\\|p" and "bs\\\\|p" were both
    shown "bs\\\\\\|p", and a literal "\\n" looked like a real LF."""

    def test_reported_collisions_are_distinct(self) -> None:
        self.assertNotEqual(display_name("bs\\|p"), display_name("bs\\\\|p"))
        self.assertNotEqual(display_name("a\\nb"), display_name("a\nb"))
        self.assertEqual(display_name("a\\nb"), "a\\\\nb")
        self.assertEqual(display_name("a\nb"), "a\\nb")
        self.assertEqual(display_name("C:\\Users\\x"), "C:\\\\Users\\\\x")
        self.assertEqual(display_name("\\x1b"), "\\\\x1b")
        self.assertEqual(display_name("\x1b"), "\\x1b")

    def test_random_strings_round_trip(self) -> None:
        names = random_names(20000, seed=1104)
        shown = {}
        for name in names:
            text = display_name(name)
            self.assertEqual(undisplay_name(text), name, repr(name))
            self.assertNotIn("\n", text)
            self.assertEqual(len(_markdown_cells(f"| {markdown_cell(text)} | x |")), 2, repr(name))
            other = shown.setdefault(text, name)
            self.assertEqual(other, name, f"collision: {other!r} and {name!r} both shown {text!r}")

    def test_no_collision_among_all_short_strings(self) -> None:
        import itertools

        alphabet = ("\\", "|", "n", "x", "1", "\n", "\x1b")
        seen: dict[str, str] = {}
        for length in range(5):
            for chars in itertools.product(alphabet, repeat=length):
                name = "".join(chars)
                other = seen.setdefault(display_name(name), name)
                self.assertEqual(other, name)
        self.assertEqual(len(seen), sum(len(alphabet) ** n for n in range(5)))


HAVE_MARKDOWN_IT = importlib.util.find_spec("markdown_it") is not None
HAVE_PYTHON_MARKDOWN = importlib.util.find_spec("markdown") is not None
# R12-5: names that rendered alike before (a real LF vs a literal "\\n",
# "&amp;" vs "&", "&lt;" vs "<", a ZWSP vs a literal "\\u200b") and friends.
RENDER_PAIRS = [
    ("a\nb", "a\\nb"), ("&amp;", "&"), ("&lt;", "<"), ("\u200b", "\\u200b"), ("&#124;", "|"),
    ("p | q", "p \\| q"), ("x\\\\y", "x\\y"), ("&copy;", "\u00a9"), ("\\&", "&"), ("a\\", "a"),
]
RENDER_ALPHABET = (*PROPERTY_ALPHABET, "&", ";", "#", "l", "t", "g", "m", "p", "o", "[", "]", "(", ")", "!", "<", ">", "`", "*", "_", "~")


def _cell_html_markdown_it(cells: list[str]) -> list[str]:
    import re

    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark").enable("table")
    source = "| h |\n| --- |\n" + "".join(f"| {cell} |\n" for cell in cells)
    html = md.render(source)
    return re.findall(r"<td>(.*?)</td>", html, re.S)


def _cell_html_python_markdown(cells: list[str]) -> list[str]:
    import re

    import markdown

    out = []
    for cell in cells:
        html = markdown.markdown(f"| h |\n| --- |\n| {cell} |\n", extensions=["tables"])
        found = re.findall(r"<td>(.*?)</td>", html, re.S)
        out.append(found[0] if len(found) == 1 else f"<!-- {len(found)} cells -->{html}")
    return out


class MarkdownRenderInjectiveTest(unittest.TestCase):
    """R12-5 (round 12): the rendered evidence-statement cell of a name is injective.

    Before R12-5 a real LF and a literal backslash + "n", "&amp;" and "&",
    "&lt;" and "<", a ZWSP and a literal "\\u200b" rendered the same text: a
    backslash display_name had written was read as an escape and an "&" as a
    character reference. markdown_cell now escapes both.
    """

    def _names(self) -> list[str]:
        import itertools
        import random

        rng = random.Random(1205)
        names = [name for pair in RENDER_PAIRS for name in pair]
        names += ["".join(rng.choice(RENDER_ALPHABET) for _ in range(rng.randint(0, 9))) for _ in range(6000)]
        names += ["".join(chars) for length in range(4) for chars in itertools.product(("\\", "&", "|", "n", ";", "\n", "<"), repeat=length)]
        return list(dict.fromkeys(names))

    def _check(self, render: object, exact: bool) -> None:
        import html as html_module

        names = self._names()
        cells = [markdown_cell(display_name(name)) for name in names]
        rendered = render(cells)  # type: ignore[operator]
        self.assertEqual(len(rendered), len(names))
        seen: dict[str, str] = {}
        for name, cell_html in zip(names, rendered):
            other = seen.setdefault(cell_html, name)
            self.assertEqual(other, name, f"{other!r} and {name!r} both render {cell_html!r}")
            if exact:
                # The rendered text is exactly what display_name shows.
                self.assertNotIn("<", cell_html.replace("&lt;", ""), (name, cell_html))
                self.assertEqual(html_module.unescape(cell_html), display_name(name), repr(name))
        for left, right in RENDER_PAIRS:
            self.assertNotEqual(rendered[names.index(left)], rendered[names.index(right)], (left, right))

    @unittest.skipUnless(HAVE_MARKDOWN_IT, "markdown-it-py not installed (QA side venv)")
    def test_markdown_it_render_is_injective_and_exact(self) -> None:
        self._check(_cell_html_markdown_it, exact=True)

    @unittest.skipUnless(HAVE_PYTHON_MARKDOWN, "python-markdown not installed (QA side venv)")
    def test_python_markdown_render_is_injective(self) -> None:
        self._check(_cell_html_python_markdown, exact=False)

    def test_reported_pairs_differ_in_source(self) -> None:
        for left, right in RENDER_PAIRS:
            self.assertNotEqual(markdown_cell(display_name(left)), markdown_cell(display_name(right)))
        self.assertEqual(markdown_cell("&lt;"), "&amp;lt;")
        self.assertEqual(markdown_cell("a\\nb"), "a\\\\nb")


@unittest.skipUnless(shutil.which("node"), "node required to run gui.js helpers")
class GuiDisplayNameTest(unittest.TestCase):
    """The GUI's displayName/csvCell (gui.js) match result_text exactly."""

    def test_gui_helpers_match_python(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "gui.js").read_text(encoding="utf-8")
        start, end = source.index("function displayName(value)"), source.index("function escapeHtml(value)")
        # R11-4: plus random strings over the special characters (injective rule).
        samples = [*HOSTILE_NAMES, "\udcff.png", "plain.png", "bs\\|p", "bs\\\\|p", "a\\nb", *random_names(2000, seed=1104),
                   "\uff1d1+1", "\uff0b1", "\uff0d1", "\uff20x", "\ufe661", "a\uff1d1"]  # R11-13
        script = (
            source[start:end]
            + "const samples = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
            + "process.stdout.write(JSON.stringify(samples.map(s => [displayName(s), csvCell(s)])));\n"
        )
        result = subprocess.run(
            ["node", "-e", script], input=json.dumps(samples), capture_output=True, text=True, check=True, timeout=60,
        )
        expected = []
        for sample in samples:
            cell = str(csv_cell(sample))
            quoted = '"' + cell.replace('"', '""') + '"' if any(char in cell for char in '",\r\n') else cell
            expected.append([display_name(sample), quoted])
        self.assertEqual(json.loads(result.stdout), expected)


class HostileNamesInEveryRenderingTest(unittest.TestCase):
    """R10-1: hostile names cannot change the conclusion column of any rendering."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        self.folder = root / "case"
        self.folder.mkdir()
        data = A1111.read_bytes()
        for name in HOSTILE_NAMES:
            (self.folder / name).write_bytes(data)
        self.out = root / "out"
        self.out.mkdir()
        home = root / "home"
        home.mkdir()
        saved = {key: os.environ.get(key) for key in ("HOME", "DEEPFAKE_LENS_LOG_DIR", "DEEPFAKE_LENS_REPORT_KEY")}
        os.environ["HOME"] = str(home)
        os.environ["DEEPFAKE_LENS_LOG_DIR"] = str(home / "logs")
        os.environ.pop("DEEPFAKE_LENS_REPORT_KEY", None)

        def restore() -> None:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)

    def _assert_no_raw_controls(self, text: str, label: str) -> None:
        for char in RAW_CONTROLS:
            self.assertNotIn(char, text, f"{label}: raw {char!r}")

    def test_cli_table_csv_and_html(self) -> None:
        csv_out, html_out = self.out / "r.csv", self.out / "r.html"
        code, stdout, _ = _run(["scan", str(self.folder), "--include-low", "--csv-out", str(csv_out), "--html-out", str(html_out)])
        self.assertEqual(code, 0)
        # CLI table: one line per file, each concluding 조작·생성 근거 있음.
        self._assert_no_raw_controls(stdout, "table")
        table = stdout.split(TABLE_RULE + "\n", 1)[1].splitlines()
        rows = [line for line in table if line.strip()]
        self.assertEqual(len(rows), len(HOSTILE_NAMES), rows)
        for line in rows:
            self.assertTrue(line.startswith(MANIPULATION), line)
        for name in HOSTILE_NAMES:
            self.assertEqual(sum(display_name(name) in line for line in rows), 1, name)
        # CSV: one row per file, the 결론 column unchanged, formulas guarded.
        lines = [line for line in csv_out.read_text(encoding="utf-8").splitlines(keepends=True) if not line.startswith("#")]
        records = list(csv.reader(lines))
        header, body = records[0], records[1:]
        self.assertEqual(len(body), len(HOSTILE_NAMES))
        verdict_col, path_col = header.index(CSV_VERDICT_COLUMN), header.index("path")
        for record in body:
            self.assertEqual(record[verdict_col], MANIPULATION, record[path_col])
            self._assert_no_raw_controls(record[path_col], "csv")
            for cell in record:
                self.assertFalse(cell.startswith(("=", "+", "-", "@")), cell)
        paths = sorted(record[path_col] for record in body)
        self.assertIn("'" + display_name('=HYPERLINK("http:evil","x").png'), paths)
        self.assertIn("'@SUM(1+1).png", paths)
        # HTML: one result row per file, the conclusion cell unchanged.
        parser = _ResultRows()
        parser.feed(html_out.read_text(encoding="utf-8"))
        self.assertEqual(len(parser.rows), len(HOSTILE_NAMES))
        for row in parser.rows:
            self.assertTrue(row[0].startswith(MANIPULATION), row[0])
            self._assert_no_raw_controls(row[3], "html")
        self.assertEqual(sorted(row[3] for row in parser.rows), sorted(display_name(name) for name in HOSTILE_NAMES))

    def test_evidence_statement_markdown(self) -> None:
        md_out = self.out / "s.md"
        code, stdout, _ = _run(["evidence-statement", str(self.folder), "--md-out", str(md_out)])
        self.assertEqual(code, 0)
        self._assert_no_raw_controls(stdout, "statement text")
        text = md_out.read_text(encoding="utf-8")
        self._assert_no_raw_controls(text, "markdown")
        rows = [line for line in text.splitlines() if line.startswith("| **")]
        self.assertEqual(len(rows), len(HOSTILE_NAMES), rows)
        exhibits = []
        for row in rows:
            cells = _markdown_cells(row)
            self.assertEqual(len(cells), 4, row)  # 호증 | 명칭 | 작성자 및 일자 | 입증취지
            exhibits.append(cells[0])
            self.assertIn(f"[자동 분석 결론: {MANIPULATION} /", cells[3])
            self.assertNotIn(AUTHENTICITY, cells[3])
        self.assertEqual(exhibits, [f"**갑 제{index}호증**" for index in range(1, len(HOSTILE_NAMES) + 1)])
        self.assertEqual(sum("갑 제9호증" in line for line in text.splitlines() if line.startswith("| **갑 제9호증**")), 1)

    @unittest.skipUnless(HAVE_PYMUPDF, "pymupdf required for PDF generation")
    def test_pdf_conclusion_columns(self) -> None:
        from deepfake_lens.pdf_backend import import_pymupdf

        pymupdf = import_pymupdf()
        forensic, statement = self.out / "f.pdf", self.out / "s.pdf"
        code, _, _ = _run(["scan", str(self.folder), "--include-low", "--forensic-pdf-out", str(forensic)])
        self.assertEqual(code, 0)
        code, _, _ = _run(["evidence-statement", str(self.folder), "--pdf-out", str(statement)])
        self.assertEqual(code, 0)

        def column_words(path: Path, header: str, next_header: str | None) -> list[str]:
            """Words under ``header`` (left of ``next_header``) on every page, in reading order."""
            words: list[str] = []
            with pymupdf.open(str(path)) as document:
                for page in document:
                    page_words = page.get_text("words")
                    heads = [w for w in page_words if w[4] == header]
                    if not heads:
                        continue
                    nexts = [w for w in page_words if next_header is not None and w[4] == next_header]
                    left, top = heads[0][0] - 2.0, heads[0][3]
                    right = nexts[0][0] - 2.0 if nexts else page.rect.width
                    words.extend(w[4] for w in page_words if left <= w[0] < right and w[1] > top)
            return words

        # Forensic PDF: the 결론 column holds one 조작·생성 verdict per file.
        verdicts = column_words(forensic, "결론", "등급")
        self.assertEqual(verdicts.count(MANIPULATION.split()[0]), len(HOSTILE_NAMES), verdicts)
        self.assertNotIn(AUTHENTICITY.split()[0], verdicts)
        with pymupdf.open(str(forensic)) as document:
            self._assert_no_raw_controls("".join(page.get_text() for page in document), "forensic pdf")
        # Evidence-statement PDF: one exhibit per file, each purpose cell
        # opening with "[자동 분석 결론: 조작·생성 근거 있음".
        exhibits = [word for word in column_words(statement, "호증", "서증(증거)의") if word.endswith("호증")]
        self.assertEqual(exhibits, [f"제{index}호증" for index in range(1, len(HOSTILE_NAMES) + 1)])
        purposes = column_words(statement, "입증취지", None)
        conclusions = [purposes[index + 1] for index, word in enumerate(purposes[:-1]) if word == "결론:"]
        self.assertEqual(conclusions, [MANIPULATION.split()[0]] * len(HOSTILE_NAMES))
        self.assertNotIn(AUTHENTICITY.split()[0], purposes)
        with pymupdf.open(str(statement)) as document:
            self._assert_no_raw_controls("".join(page.get_text() for page in document), "statement pdf")


class MarkdownSyntaxInNamesTest(unittest.TestCase):
    """R11-7 (round 11): an archive member "![t](https:/evil.example/t.png)/[click](javascript:
    alert(1)).png" became a live image and link in the evidence statement's Markdown.
    Every Markdown-active character of shown text is backslash-escaped."""

    MEMBER = "![t](https:/evil.example/t.png)/[click](javascript:alert(1)).png"
    SUBFOLDER = "[x](javascript:y) <b>`code`</b> *em* _u_ #h ~s~"

    @staticmethod
    def _unescaped(text: str) -> list[str]:
        """Markdown-active characters in ``text`` not preceded by an odd run of backslashes."""
        from deepfake_lens.result_text import MARKDOWN_SPECIALS

        found: list[str] = []
        backslashes = 0
        for char in text:
            if char in MARKDOWN_SPECIALS and backslashes % 2 == 0:
                found.append(char)
            backslashes = backslashes + 1 if char == "\\" else 0
        return found

    def test_unit(self) -> None:
        for raw in (self.MEMBER, self.SUBFOLDER, "a\\[b", "a\\\\[b", "plain 한글"):
            with self.subTest(raw=raw):
                self.assertEqual(self._unescaped(markdown_cell(display_name(raw))), [])
                self.assertEqual(self._unescaped(markdown_cell(raw).replace("<br>", "")), [])
        self.assertEqual(markdown_cell("![t](u)"), "\\!\\[t\\]\\(u\\)")
        # R12-5: display_name's backslash-pipe is written as an escaped backslash
        # plus an escaped pipe — it renders as the shown backslash-pipe.
        self.assertEqual(markdown_cell(display_name("p | q")), "p \\\\\\| q")

    def test_evidence_statement_markdown_holds_no_live_markdown(self) -> None:
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            folder = root / "case"
            (folder / self.SUBFOLDER).mkdir(parents=True)
            (folder / self.SUBFOLDER / "inner.txt").write_text("x", encoding="utf-8")
            with zipfile.ZipFile(folder / "e.zip", "w") as archive:
                archive.writestr(self.MEMBER, A1111.read_bytes())
            md_out = root / "s.md"
            code, stdout, stderr = _run(["evidence-statement", str(folder), "--md-out", str(md_out), "--format", "markdown"])
            self.assertEqual(code, 0, stderr)
            text = md_out.read_text(encoding="utf-8")
        for rendered in (text, stdout):
            self.assertNotIn("](", rendered)
            self.assertNotIn("![", rendered)
        rows = [line for line in text.splitlines() if line.startswith("| **")]
        self.assertTrue(rows)
        for row in rows:
            for cell in _markdown_cells(row)[1:]:  # every cell but the bold exhibit number
                self.assertEqual(self._unescaped(cell.replace("<br>", " ")), [], cell)
        # The member name (refused by the extractor: ":" in it) is named in the container's row, inert.
        self.assertTrue(any("\\!\\[t\\]\\(https:/evil.example/t.png\\)" in row for row in rows), rows)
        unrecorded = [line for line in text.splitlines() if "javascript:y" in line and not line.startswith("|")]
        self.assertTrue(unrecorded, "the skipped subfolder is named in the unrecorded-files section")
        for line in unrecorded:
            self.assertEqual(self._unescaped(line.removeprefix("- ")), [], line)


class GuiResultStringsTest(unittest.TestCase):
    """R11-10 (round 11): the GUI showed evidence titles and details (gui.js evidenceHtml)
    and other result strings with escapeHtml only — a bidi override or zero-width
    character in them reached the page. Every result string goes through shown() =
    escapeHtml(displayName(…)), as the HTML report shows them through display_name."""

    GUI = Path(__file__).resolve().parents[1] / "gui.js"
    # Result fields an escapeHtml(...) call must never take directly.
    FIELDS = r"(title|detail|reason|verdict|notice|reference_note|summary|message|error|label|display_name|method|name|path|model|calibration_id)"

    def test_no_result_string_bypasses_display_name(self) -> None:
        import re

        source = self.GUI.read_text(encoding="utf-8")
        self.assertIn("function shown(value) {\n            return escapeHtml(displayName(value));", source)
        direct = re.findall(r"escapeHtml\(\s*[^()]*?\b[a-z]+\." + self.FIELDS + r"\b[^)]*\)", source)
        self.assertEqual(direct, [], "pass result strings through shown(), not escapeHtml()")
        for needle in ("shown(e.title)", "shown(e.detail || '')", "shown(coverageText(c))", "shown(r.verdict)", "return `<li>${shown(text)}</li>`;",
                       "map(shown)", "shown(sg.label)", "shown(item.error)", "el.textContent = displayName(msg);"):
            with self.subTest(needle=needle):
                self.assertIn(needle, source)

    @unittest.skipUnless(shutil.which("node"), "node required to run gui.js helpers")
    def test_shown_escapes_like_display_name(self) -> None:
        source = self.GUI.read_text(encoding="utf-8")
        start, end = source.index("function displayName(value)"), source.index("/* ── onboarding guide")
        samples = ["t\u202eitle", "zw\u200bdetail <b>&</b>", "a|b\\c", "\udcc1"]
        script = (
            source[start:end]
            + "const samples = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
            + "process.stdout.write(JSON.stringify(samples.map(s => shown(s))));\n"
        )
        result = subprocess.run(["node", "-e", script], input=json.dumps(samples), capture_output=True, text=True, check=True, timeout=60)
        from html import escape

        self.assertEqual(json.loads(result.stdout), [escape(display_name(sample)) for sample in samples])

    def test_html_report_evidence_lines_use_display_name(self) -> None:
        from deepfake_lens.reports import _html_row
        from deepfake_lens.result_types import (
            ClassificationResult, EvidenceDirection, EvidenceItem, EvidenceKind, EvidenceStrength, RiskBand, ScanItem, SourceConfidence,
            SourceGuess,
        )

        result = ClassificationResult(
            score=0, band=RiskBand.UNKNOWN, band_label="판단 불가", verdict="판단 불가 \u202e|x", signals=[], limitations=[],
            source_guess=SourceGuess("출처 단서 없음", SourceConfidence.UNKNOWN, []), next_checks=[],
            evidence=[EvidenceItem("제목\u202ex|y", "상세\u200b\\z", EvidenceKind.DETERMINISTIC, EvidenceDirection.NEUTRAL, EvidenceStrength.WEAK, "metadata")],
        )
        row = _html_row(ScanItem("a.png", "a.png", "image", "analyzed", 1, result), redact_paths=False)
        for raw in ("\u202e", "\u200b"):
            self.assertNotIn(raw, row)
        self.assertIn("제목\\u202ex\\|y", row)
        self.assertIn("상세\\u200b\\\\z", row)
        self.assertIn("판단 불가 \\u202e\\|x", row)


class EmbeddedReportJsonTest(unittest.TestCase):
    """R11-6 (round 11): only "</" was escaped in the report JSON embedded in the HTML
    report's <script> element — a name "<!--<script>.png" stayed raw in it (an HTML parser
    then enters the script "double-escaped" state). Every "<", ">", "&", U+2028 and
    U+2029 is now a JSON escape."""

    NAMES = ("<!--<script>.png", "a&b\u2028c>.png", "x<\u2029y.png")

    def test_script_safe_json_unit(self) -> None:
        from deepfake_lens.json_text import script_safe_json

        value = {"<k>": "</script><!--<script>&\u2028\u2029", "n": [1, "a\udcc1"]}
        text = script_safe_json(value, sort_keys=True)
        for char in "<>&\u2028\u2029":
            self.assertNotIn(char, text)
        self.assertEqual(json.loads(text), value)

    def test_html_report_embeds_inert_json(self) -> None:
        from deepfake_lens.reports import SIGNED_REPORT_SCRIPT_ID, extract_signed_report

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            folder = root / "case"
            folder.mkdir()
            for name in self.NAMES:
                (folder / name).write_bytes(A1111.read_bytes())
            key = root / "k.key"
            key.write_text("r11-6-key-0123456789abcdef", encoding="utf-8")
            html_out = root / "r.html"
            saved = os.environ.pop("DEEPFAKE_LENS_REPORT_KEY", None)
            try:
                code, _, stderr = _run(["scan", str(folder), "--include-low", "--html-out", str(html_out), "--key-file", str(key)])
            finally:
                if saved is not None:
                    os.environ["DEEPFAKE_LENS_REPORT_KEY"] = saved
            self.assertEqual(code, 0, stderr)
            html = html_out.read_text(encoding="utf-8")
        marker = f'<script type="application/json" id="{SIGNED_REPORT_SCRIPT_ID}">'
        start = html.index(marker) + len(marker)
        element = html[start:html.index("</script>", start)]
        for char in "<>&\u2028\u2029":
            self.assertNotIn(char, element)
        body = extract_signed_report(html)
        assert body is not None
        self.assertEqual(sorted(item["path"] for item in body["items"]), sorted(self.NAMES))

        class Scripts(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.scripts: list[str] = []
                self._open = False

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                if tag == "script":
                    self._open = True
                    self.scripts.append("")

            def handle_endtag(self, tag: str) -> None:
                if tag == "script":
                    self._open = False

            def handle_data(self, data: str) -> None:
                if self._open:
                    self.scripts[-1] += data

        parser = Scripts()
        parser.feed(html)
        self.assertEqual(len(parser.scripts), 1)
        self.assertEqual(json.loads(parser.scripts[0]), body)


if __name__ == "__main__":
    unittest.main()

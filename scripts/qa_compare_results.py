"""Compare two ``scan --format json`` outputs item by item (docs/QA-MANUAL.md, GUI-REV7 W-4).

QA-SYS-4 checks that a Windows scan of ``qa-manual/formats`` gives the same rows
as the reference made on the build machine from the same install state. The old
heredoc snippet did not run in PowerShell, compared ``path_b64`` (which encodes
the native separator) and did not say which row differed.

Rows are keyed by ``path`` with ``\\`` replaced by ``/``; ``cached`` and
``path_b64`` are left out (``duplicate_of`` gets the same separator
replacement). ``result.model_analysis.models[].profile`` is the absolute path of
the installed profile file (``…/site-packages/deepfake_lens/models/x-runtime.json``),
which differs between any two installs, so only its file name is compared.

Prints ``동일`` (exit 0) or ``다름 N건`` followed by, for each differing row,
the differing keys (dotted inside nested objects) and both values, truncated
(exit 1). An unreadable file is exit 2.

Standard library only, so it runs as ``python -I compare.py a.json b.json``
outside the repository (the Windows handover copies it as ``compare.py``).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, NoReturn

IGNORED_KEYS = frozenset({"cached", "path_b64"})
PATH_KEYS = ("path", "duplicate_of")
DEFAULT_WIDTH = 160
MISSING = object()


_ARGPARSE_ERRORS = (
    ("the following arguments are required:", "다음 인수가 필요합니다:"),
    ("unrecognized arguments:", "알 수 없는 인수:"),
    ("invalid int value:", "정수가 아닙니다:"),
    ("expected one argument", "값이 하나 필요합니다"),
)


class _KoreanFormatter(argparse.RawDescriptionHelpFormatter):
    def add_usage(self, usage: Any, actions: Any, groups: Any, prefix: str | None = None) -> None:
        super().add_usage(usage, actions, groups, "사용법: " if prefix is None else prefix)


class _KoreanParser(argparse.ArgumentParser):
    """Standalone copy of ``cli_parser.KoreanArgumentParser``'s texts (no package import: the file is copied out)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("formatter_class", _KoreanFormatter)
        kwargs["add_help"] = False
        super().__init__(*args, **kwargs)
        self._positionals.title = "위치 인수"
        self._optionals.title = "옵션"
        self.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help="이 도움말을 보여 주고 끝냄")

    def error(self, message: str) -> NoReturn:
        for english, korean in _ARGPARSE_ERRORS:
            message = message.replace(english, korean)
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: 오류: {message}\n")


def _norm_path(value: Any) -> Any:
    return value.replace("\\", "/") if isinstance(value, str) else value


def _profile_names(row: dict[str, Any]) -> None:
    """``result.model_analysis.models[].profile`` reduced to its file name (the install folder differs per machine)."""
    result = row.get("result")
    analysis = result.get("model_analysis") if isinstance(result, dict) else None
    models = analysis.get("models") if isinstance(analysis, dict) else None
    for model in models if isinstance(models, list) else []:
        if isinstance(model, dict) and isinstance(model.get("profile"), str):
            model["profile"] = model["profile"].replace("\\", "/").rsplit("/", 1)[-1]


def load_items(path: str) -> dict[str, dict[str, Any]]:
    """``path``'s rows keyed by the separator-normalized row path, ``cached``/``path_b64`` removed."""
    # utf-8-sig: a file saved by Windows PowerShell 5.1 may start with a BOM.
    with open(path, encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("`items` 목록이 없습니다 — scan --format json 출력이 아닙니다")
    rows: dict[str, dict[str, Any]] = {}
    for item in data["items"]:
        if not isinstance(item, dict):
            raise ValueError("`items`에 객체가 아닌 항목이 있습니다")
        row = {key: value for key, value in item.items() if key not in IGNORED_KEYS}
        for key in PATH_KEYS:
            if key in row:
                row[key] = _norm_path(row[key])
        _profile_names(row)
        base = str(row.get("path", ""))
        name, n = base, 2
        while name in rows:  # a repeated path keeps every row (shown as "<path> #2")
            name = f"{base} #{n}"
            n += 1
        rows[name] = row
    return rows


def differences(a: Any, b: Any, prefix: str = "") -> list[tuple[str, Any, Any]]:
    """Leaf-level differences between ``a`` and ``b`` as (dotted key, value in a, value in b)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[tuple[str, Any, Any]] = []
        for key in sorted(a.keys() | b.keys(), key=str):
            sub = f"{prefix}.{key}" if prefix else str(key)
            out.extend(differences(a.get(key, MISSING), b.get(key, MISSING), sub))
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for index, (left, right) in enumerate(zip(a, b)):
            out.extend(differences(left, right, f"{prefix}[{index}]"))
        return out
    return [] if a == b else [(prefix or "(행 전체)", a, b)]


def shown(value: Any, width: int) -> str:
    if value is MISSING:
        return "(없음)"
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= width else text[: max(width - 1, 1)] + "…"


def compare(a_path: str, b_path: str, width: int) -> tuple[int, list[str]]:
    a, b = load_items(a_path), load_items(b_path)
    lines: list[str] = []
    differing = sorted(p for p in a.keys() | b.keys() if a.get(p) != b.get(p))
    for path in differing:
        if path not in b:
            lines.append(f"- {path}: 기준({a_path})에만 있음")
            continue
        if path not in a:
            lines.append(f"- {path}: 비교 대상({b_path})에만 있음")
            continue
        diffs = differences(a[path], b[path])
        lines.append(f"- {path}: 다른 키 {len(diffs)}개")
        for key, left, right in diffs:
            lines.append(f"    {key}")
            lines.append(f"      기준: {shown(left, width)}")
            lines.append(f"      비교: {shown(right, width)}")
    return len(differing), lines


def main(argv: list[str] | None = None) -> int:
    parser = _KoreanParser(
        prog="qa_compare_results.py",
        description=(
            "두 검사 결과 JSON 파일(검사 명령의 JSON 출력)의 항목을 행 경로별로 비교합니다(QA-MANUAL QA-SYS-4).\n"
            "경로의 역슬래시는 슬래시로 바꾸어 맞추고, 캐시 표시와 경로 바이트 인코딩 필드는 빼고 비교합니다.\n"
            "모델 프로필 파일은 설치 폴더가 기계마다 다르므로 파일 이름만 비교합니다.\n"
            "같으면 「동일」(종료 코드 0), 다르면 「다름 N건」과 행마다 다른 키·두 값을 출력합니다(종료 코드 1).\n"
            "파일을 읽을 수 없으면 종료 코드 2입니다."
        ),
    )
    parser.add_argument("reference", metavar="<기준.json>", help="기준 결과 파일(빌드 머신에서 같은 설치 상태로 만든 것)")
    parser.add_argument("candidate", metavar="<비교.json>", help="비교할 결과 파일(시험 머신에서 만든 것)")
    parser.add_argument(
        "--width", type=int, default=DEFAULT_WIDTH, metavar="<글자 수>",
        help=f"값을 보여 줄 최대 글자 수, 넘으면 잘라 냄(기본 {DEFAULT_WIDTH})",
    )
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="backslashreplace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    try:
        count, lines = compare(args.reference, args.candidate, args.width)
    except (OSError, ValueError) as exc:  # json.JSONDecodeError is a ValueError
        print(f"오류: 결과 파일을 읽을 수 없습니다 — {exc}", file=sys.stderr)
        return 2
    if count == 0:
        print("동일")
        return 0
    print(f"다름 {count}건")
    for line in lines:
        print(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())

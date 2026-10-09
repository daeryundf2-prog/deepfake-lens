"""Argument-parser construction for the CLI.

Extracted from cli.main() — ``main`` owns dispatch; this module owns the
argparse shape so each stays readable as commands are added.

B8: every help string (top-level description/epilog, every subcommand and
every argument) is Korean; flag names, metavars and defaults stay as they
are. argparse's own words ("usage:", "options", "-h" help, error prefix and
the common error messages) are given in Korean by
:class:`KoreanArgumentParser`, which every subparser inherits.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

from .core import DEFAULT_MAX_FILES
from .corpus_manifest import add_corpus_parser
from .pixel import DEFAULT_PIXEL_MAX_SIDE, SUPPORTED_PIXEL_MODES

# Layer-diagnostic commands share one help suffix: their numbers are
# unmeasured reference values and never a conclusion.
LAYER_DIAGNOSTIC = "계층 진단 — 측정되지 않은 참고 수치이며 결론이 아님(결론은 scan 사용)"
OUTPUT_FORMAT_HELP = "출력 형식"
JSON_OUT_HELP = "JSON 보고서를 이 파일에 저장"
SIGN_HELP = "--json-out 보고서에 HMAC-SHA256 서명(키 보유자 대상 무결성 확인이며 법적 부인방지는 아님; 키는 --key-file 또는 DEEPFAKE_LENS_REPORT_KEY)"
KEY_FILE_HELP = "보고서 서명 키 파일(기본: DEEPFAKE_LENS_REPORT_KEY 환경 변수)"
NO_DEFAULT_ENGINE_HELP = "패키지에 포함된 기본 엔진 프로필(models/*-runtime.json)을 쓰지 않음"
THRESHOLDS_HELP = "계층 임계값 프로필 JSON(layer-thresholds-v1) — 휴리스틱 기준값을 대체"

USAGE_PREFIX = "사용법: "
# (argparse message pattern, Korean) for the errors an examiner can trigger.
_ARGPARSE_ERRORS_KO: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern), replacement)
    for pattern, replacement in (
        (r"^the following arguments are required: (.+)$", r"다음 인수가 필요합니다: \1"),
        (r"^argument (.+?): invalid choice: (.+?) \(choose from (.+)\)$", r"인수 \1: 허용되지 않은 값 \2 (선택 가능: \3)"),
        (r"^argument (.+?): invalid (\w+) value: (.+)$", r"인수 \1: 올바르지 않은 \2 값 \3"),
        (r"^argument (.+?): expected one argument$", r"인수 \1: 값 하나가 필요합니다"),
        (r"^argument (.+?): expected at least one argument$", r"인수 \1: 값이 하나 이상 필요합니다"),
        (r"^argument (.+?): not allowed with argument (.+)$", r"인수 \1: \2 와(과) 함께 쓸 수 없습니다"),
        (r"^argument (.+?): ignored explicit argument (.+)$", r"인수 \1: 값 \2 을(를) 받지 않는 옵션입니다"),
        (r"^unrecognized arguments: (.+)$", r"알 수 없는 인수: \1"),
        (r"^ambiguous option: (.+?) could match (.+)$", r"모호한 옵션 \1 — 후보: \2"),
        (r"^one of the arguments (.+) is required$", r"다음 인수 중 하나가 필요합니다: \1"),
        (r"^argument (.+?): (경로가 비어 있습니다.*)$", r"인수 \1: \2"),
        # R10-5: every Korean ArgumentTypeError (port range, ratios …).
        (r"^argument (.+?): ([\uac00-\ud7a3].*)$", r"인수 \1: \2"),
    )
)

# Y3 (round 7): `scan ""` scanned the current folder — Path("") is ".". Every
# Path-typed argument goes through :func:`cli_path`, which refuses an empty
# (or blank) value; the error is "오류: 인수 <name>: 경로가 비어 있습니다 …",
# exit 2, before any work.
EMPTY_PATH_MESSAGE = '경로가 비어 있습니다 — 빈 문자열("")은 경로로 쓸 수 없습니다(현재 폴더는 . 으로 지정)'


if sys.platform == "win32":  # pragma: no cover - the concrete Path class of the platform
    from pathlib import WindowsPath as _ConcretePath
else:
    from pathlib import PosixPath as _ConcretePath


class CliPath(_ConcretePath):
    """A Path that keeps the command-line text it was parsed from (Z3).

    ``Path("photo.png/")`` is ``Path("photo.png")``: the trailing separator
    — "this names a folder" — is gone before ``cli_inputs.require_input_path``
    sees the argument. ``cli_text`` keeps it; paths derived from this one
    (``/``, ``.parent``, unpickled copies) have none and fall back to ``str()``.
    """

    cli_text: str


def cli_path(value: str) -> Path:
    """argparse ``type`` of every path argument: a Path, never from an empty string (Y3)."""
    if not str(value).strip():
        raise argparse.ArgumentTypeError(EMPTY_PATH_MESSAGE)
    path = CliPath(value)
    path.cli_text = str(value)
    return path


cli_path.__name__ = "path"


def _refuse_empty_paths(parser: argparse.ArgumentParser) -> None:
    """Replace ``type=Path`` by :func:`cli_path` on ``parser`` and every subparser (Y3)."""
    for action in parser._actions:
        if action.type is Path:
            action.type = cli_path
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                _refuse_empty_paths(sub)


# R9-7 (round 9): user input echoed in an error ("폴더를 찾을 수 없습니다: a<LF>b",
# argparse's "invalid choice: 'tur<LF>bo'") printed its newlines and control
# characters raw, so one error spanned several lines. Echoed text shows them
# as escapes: "\n", "\r", "\t", other C0/C1 controls and U+2028/U+2029 as
# "\xNN"/"\uNNNN". R10-1: the one implementation is
# result_text.escape_controls (shared with display_name, which every report
# uses for file names); it also shows zero-width/bidi format characters.


# R10-5 (round 10): --port -1 / 99999 reached the socket call (OverflowError,
# exit 1). A TCP port to listen on is 1-65535 (RFC 6335 §6; 0 would ask the
# OS for a random port, which a browser URL cannot name).
PORT_MIN, PORT_MAX = 1, 65535
PORT_RANGE_MESSAGE = f"포트는 {PORT_MIN}–{PORT_MAX} 범위의 정수여야 합니다: {{value}}"


def port_number(text: str) -> int:
    """argparse type of ``--port``: an integer in 1–65535 (R10-5), else exit 2."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(PORT_RANGE_MESSAGE.format(value=escape_echo(repr(text)))) from None
    if not PORT_MIN <= value <= PORT_MAX:
        raise argparse.ArgumentTypeError(PORT_RANGE_MESSAGE.format(value=value))
    return value


def escape_echo(text: object) -> str:
    """``text`` with newlines and other control characters written as escapes (R9-7, R10-1)."""
    from .result_text import escape_controls

    return escape_controls(text)


def korean_argparse_error(message: str) -> str:
    """argparse's English error message in Korean (unknown messages unchanged)."""
    for pattern, replacement in _ARGPARSE_ERRORS_KO:
        if pattern.search(message):
            return pattern.sub(replacement, message)
    return message


class KoreanHelpFormatter(argparse.HelpFormatter):
    """``사용법:`` instead of ``usage:``."""

    def add_usage(self, usage: Any, actions: Any, groups: Any, prefix: str | None = None) -> None:
        super().add_usage(usage, actions, groups, USAGE_PREFIX if prefix is None else prefix)


class KoreanArgumentParser(argparse.ArgumentParser):
    """ArgumentParser whose built-in texts are Korean (B8); subparsers inherit it."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("formatter_class", KoreanHelpFormatter)
        add_help = bool(kwargs.pop("add_help", True))
        kwargs["add_help"] = False  # the Korean -h/--help is added below
        super().__init__(*args, **kwargs)
        self._positionals.title = "위치 인수"
        self._optionals.title = "옵션"
        if add_help:
            self.add_argument("-h", "--help", action="help", default=argparse.SUPPRESS, help="이 도움말을 보여 주고 끝냄")

    def error(self, message: str) -> NoReturn:
        message = escape_echo(message)  # R9-7: an echoed value never spans lines
        if EMPTY_PATH_MESSAGE in message:
            # Y3: the same one-line shape as the input-path checks (cli_inputs).
            self.exit(2, f"오류: {korean_argparse_error(message)}\n")
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: 오류: {korean_argparse_error(message)}\n")


# N17: no built-in office identity — the flag, else ~/.deepfake-lens/config.json
# ("law_firm"/"contact"), else blank.
LAW_FIRM_HELP = "보고서 머리글·서명란의 법무법인(소송대리인) 이름(기본: ~/.deepfake-lens/config.json의 law_firm, 없으면 빈칸)"
CONTACT_HELP = "보고서 머리글의 대표전화(기본: ~/.deepfake-lens/config.json의 contact, 없으면 빈칸)"

# R9-7 (round 9): positional arguments were shown by their English dest
# ("folder", "file", "file_a", "report") in usage lines and in "다음 인수가
# 필요합니다: …". Each positional dest without a Korean metavar gets one.
POSITIONAL_METAVARS: dict[str, str] = {
    "folder": "<폴더>",
    "file": "<파일>",
    "files": "<파일>",
    "file_a": "<파일A>",
    "file_b": "<파일B>",
    "report": "<보고서>",
    "labels": "<라벨 파일>",
    "target": "<대상>",
    "profile": "<프로필>",
}


def _korean_metavars(parser: argparse.ArgumentParser) -> None:
    """Usage lines show Korean placeholders, never English dest names (P-round-8 leftover, rule 3).

    argparse prints ``--plaintiff PLAINTIFF`` by default; every value-taking
    option without an explicit metavar gets ``<경로>``/``<정수>``/``<실수>``/``<값>``.
    R9-7: a positional argument without a metavar gets its
    :data:`POSITIONAL_METAVARS` placeholder (a choice set stays as is).
    """
    from pathlib import Path as _Path

    def _walk(target: argparse.ArgumentParser) -> None:
        for action in target._actions:  # noqa: SLF001 — argparse offers no public walk
            if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
                for sub in action.choices.values():
                    _walk(sub)
                continue
            if not action.option_strings:
                if action.metavar is None and not action.choices and action.dest in POSITIONAL_METAVARS:
                    action.metavar = POSITIONAL_METAVARS[action.dest]
                continue
            if action.nargs == 0 or action.metavar is not None:
                continue
            if action.choices:
                continue  # "{a,b}" choice sets are identifiers
            typ = action.type
            if typ is int:
                action.metavar = "<정수>"
            elif typ is float:
                action.metavar = "<실수>"
            elif typ is _Path or typ is cli_path or getattr(typ, "__name__", "") in {"cli_path", "Path"}:
                action.metavar = "<경로>"
            else:
                action.metavar = "<값>"

    _walk(parser)


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = KoreanArgumentParser(
        prog="deepfake-lens",
        description="로컬 AI 생성·조작 미디어/문서 폴더 검사기 — 결론은 조작·생성 근거 있음 / 원본성 근거 있음 / 판단 불가 세 가지뿐입니다.",
        epilog="명령 이름 없이 폴더를 주면 scan 으로 처리합니다. 명령별 도움말: deepfake-lens <명령> --help",
    )
    subparsers = parser.add_subparsers(dest="command", title="명령", metavar="<명령>")
    scan_parser = subparsers.add_parser("scan", help="폴더 검사(세 가지 결론·근거·검사 범위 기록)")
    scan_parser.add_argument("folder", type=Path, help="검사할 폴더")
    scan_parser.add_argument("--recursive", action="store_true", help="하위 폴더까지 검사(기본은 바로 아래 파일만 — 건너뛴 하위 폴더 수는 결과에 표시됨)")
    scan_parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help=f"검사할 최대 파일 수(기본: {DEFAULT_MAX_FILES})")
    scan_parser.add_argument(
        "--include-low",
        "--include-authentic",
        dest="include_low",
        action="store_true",
        help="표에 '원본성 근거 있음' 행도 포함합니다(기본은 생략하고 생략 건수만 표시). "
        "--include-low는 호환을 위해 남긴 이름이며 '낮은 위험' 행이라는 뜻이 아닙니다.",
    )
    scan_parser.add_argument("--format", choices=["table", "json"], default="table", help="표준 출력 형식(table 또는 json)")
    scan_parser.add_argument("--json-out", type=Path, help="전체 JSON 보고서를 이 파일에 저장")
    scan_parser.add_argument("--csv-out", type=Path, help="요약 CSV 보고서를 이 파일에 저장")
    scan_parser.add_argument("--text-bytes", type=int, default=64 * 1024, help="텍스트 파일마다 읽을 최대 바이트 수")
    scan_parser.add_argument("--metadata-bytes", type=int, default=4 * 1024 * 1024, help="이미지 파일마다 메타데이터를 찾으려고 읽을 앞부분 최대 바이트 수")
    scan_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="off", help="이미지 픽셀 휴리스틱 실행(참고 신호 전용 — 결론에 참여하지 않음)")
    scan_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE, help=f"픽셀 분석 표본의 최대 변 길이(기본: {DEFAULT_PIXEL_MAX_SIDE})")
    scan_parser.add_argument("--heatmaps", action="store_true", help="--pixel deep 위치 추정 히트맵 PNG 저장")
    scan_parser.add_argument("--heatmap-dir", type=Path, help="히트맵 저장 폴더(기본: $DEEPFAKE_LENS_HEATMAP_DIR 또는 ~/.cache/deepfake-lens/heatmaps/<폴더 키> — 검사 대상 증거 폴더 안에는 쓰지 않음)")
    scan_parser.add_argument("--model-path", type=Path, help="외부 모델 프로필·체크포인트·프로필 폴더(기본: 모델 폴더의 프로필 자동 탐색)")
    scan_parser.add_argument("--no-default-engine", action="store_true", help="기본 엔진 프로필(models/*-runtime.json)을 쓰지 않음")
    scan_parser.add_argument("--fusion-profile", type=Path, help="점수 융합 프로필(참고 점수 전용)")
    scan_parser.add_argument("--cache", type=Path, help="대용량 폴더 재개용 JSON 캐시(내용 해시 기반)")
    scan_parser.add_argument("--workers", type=int, default=1, help="병렬로 분석할 파일 작업자 수")
    scan_parser.add_argument("--dedupe", action="store_true", help="파일 해시로 내용이 같은 파일을 중복으로 표시")
    scan_parser.add_argument("--deep-signals", action="store_true", help="선택형 심층 검사 실행: 이미지 얼굴 조작·인페인팅, 영상 rPPG·아바타·입모양 동기")
    scan_parser.add_argument("--thresholds", type=Path, help=THRESHOLDS_HELP)
    scan_parser.add_argument("--models-dir", type=Path, help="모델 폴더(프로필+가중치). 기본: 패키지 models/ 또는 $DEEPFAKE_LENS_MODELS_DIR")
    scan_parser.add_argument("--law-firm", type=str, default=None, help=LAW_FIRM_HELP)
    scan_parser.add_argument("--contact", type=str, default=None, help=CONTACT_HELP)
    scan_parser.add_argument("--center", type=str, default="디지털포렌식 감정센터", help="법원 제출 문서에 적을 감정센터 이름")
    scan_parser.add_argument("--hash-db", type=Path, help="반복 검사 사이에 중복 판정용 해시를 보관할 파일")
    scan_parser.add_argument("--max-file-bytes", type=int, help="이 크기(바이트)보다 큰 파일은 건너뜀(건너뛴 행으로 기록)")
    scan_parser.add_argument("--allow-symlinks", action="store_true", help="심볼릭 링크를 따라가 분석 — 링크된 폴더는 --recursive일 때 들어감; 깨진·순환 링크는 사유와 함께 건너뜀 행(기본은 따라가지 않고 건너뜀 행으로 기록)")
    scan_parser.add_argument("--progress", action="store_true", help="진행 상황을 표준 오류로 간단히 출력")
    scan_parser.add_argument("--html-out", type=Path, help="HTML 보고서 저장")
    scan_parser.add_argument("--pdf-out", type=Path, help="한국어 PDF 보고서 저장(감정 PDF와 같은 렌더러, pymupdf 필요 — 없으면 검사 전에 종료 코드 2로 중단)")
    scan_parser.add_argument("--forensic-pdf-out", type=Path, help="ECFS 호증 표지와 SHA-256 해시가 들어간 감정 PDF 보고서 저장(pymupdf 필요)")
    scan_parser.add_argument("--evidence-statement-out", type=Path, help="ECFS 증거설명서 저장(.pdf → PDF, .json → 서명된 JSON, 그 외 → Markdown; --key-file 또는 DEEPFAKE_LENS_REPORT_KEY로 서명)")
    scan_parser.add_argument("--evidence-statement-pdf-out", type=Path, help="ECFS 증거설명서를 PDF로 저장")
    scan_parser.add_argument("--case-no", type=str, default="(사건번호 입력)", help="증거설명서의 사건번호")
    scan_parser.add_argument("--case-name", type=str, default="성폭력처벌법위반(허위영상물편집등) 및 정보통신망법위반", help="증거설명서의 사건명")
    scan_parser.add_argument("--plaintiff", type=str, default="(의뢰사 상호명 입력) 귀하", help="증거설명서의 원고(고소인)")
    scan_parser.add_argument("--defendant", type=str, default="(피고/피의자 성명 입력)", help="증거설명서의 피고(피의자)")
    scan_parser.add_argument("--court", type=str, default="○○지방법원 귀중", help="증거설명서의 제출처(법원/수사기관)")
    scan_parser.add_argument("--exhibit-no", type=str, default="갑 제        호증", help="감정 PDF 보고서의 호증 번호(기본: '갑 제        호증')")
    scan_parser.add_argument("--redact-paths", action="store_true", help="HTML/PDF 보고서의 경로를 파일 이름만 남기고 가림(압축 구성 파일은 '압축파일::내부경로')")
    scan_parser.add_argument("--sign", action="store_true", help=SIGN_HELP)
    scan_parser.add_argument("--key-file", type=Path, help="보고서 서명 키 파일(기본: DEEPFAKE_LENS_REPORT_KEY 환경 변수; 빈 파일이면 오류)")

    collect_parser = subparsers.add_parser("collect", help="데이터셋 수집 계획 JSON 작성")
    collect_parser.add_argument("folder", type=Path, help="수집 대상 폴더")
    collect_parser.add_argument("--out", type=Path, required=True, help="수집 계획 JSON 저장 경로")
    collect_parser.add_argument("--minimum-per-source", type=int, help="출처마다 필요한 최소 표본 수")

    dataset_parser = subparsers.add_parser("dataset", help="라벨 데이터셋을 찾아 매니페스트 작성")
    dataset_parser.add_argument("folder", type=Path, help="데이터셋 최상위 폴더")
    dataset_parser.add_argument("--manifest-out", type=Path, required=True, help="매니페스트 JSON 저장 경로")
    dataset_parser.add_argument("--fingerprints", action="store_true", help="매니페스트에 SHA-256 지문 포함")
    dataset_parser.add_argument("--audit-out", type=Path, help="데이터셋 감사 JSON 저장")
    dataset_parser.add_argument("--split-out", type=Path, help="결정적 train/val/test 분할 계획 저장")
    dataset_parser.add_argument("--split-ratios", default="0.8,0.1,0.1", help="train/val/test 분할 비율(쉼표로 구분)")
    dataset_parser.add_argument("--split-seed", default="deepfake-lens-v1", help="분할 시드 문자열")
    dataset_parser.add_argument("--robustness-out", type=Path, help="강건성 변환 계획 저장")
    dataset_parser.add_argument("--no-recursive", action="store_true", help="하위 폴더를 탐색하지 않음")

    eval_parser = subparsers.add_parser("eval", help="라벨 데이터셋 평가(측정값에 95%% 신뢰구간 동반)")
    eval_parser.add_argument("folder", type=Path, help="라벨 데이터셋 폴더")
    eval_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="deep", help="픽셀 휴리스틱 모드")
    eval_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE, help="픽셀 분석 표본의 최대 변 길이")
    eval_parser.add_argument("--calibration", type=Path, help="점수 보정 프로필 JSON")
    eval_parser.add_argument("--model-path", type=Path, help="외부 모델 프로필(기본: models/aide-runtime.json 자동 탐색)")
    eval_parser.add_argument("--no-default-engine", action="store_true", help=NO_DEFAULT_ENGINE_HELP)
    eval_parser.add_argument("--fusion-profile", type=Path, help="점수 융합 프로필(참고 점수 전용)")
    eval_parser.add_argument("--thresholds", type=Path, help=THRESHOLDS_HELP)
    eval_parser.add_argument("--max-files", type=int, help="평가할 최대 파일 수")
    eval_parser.add_argument("--json-out", type=Path, help="평가 결과 JSON 저장")
    eval_parser.add_argument("--html-out", type=Path, help="평가 결과 HTML 저장")
    eval_parser.add_argument("--false-positive-out", type=Path, help="오탐 목록 저장")
    eval_parser.add_argument("--false-negative-out", type=Path, help="미탐 목록 저장")
    eval_parser.add_argument("--robustness", action="store_true", help="변환 이름이 붙은 강건성 폴더도 요약")
    eval_parser.add_argument("--redact-paths", action="store_true", help="보고서의 경로를 파일 이름만 남기고 가림")
    eval_parser.add_argument("--sign", action="store_true", help=SIGN_HELP)
    eval_parser.add_argument("--key-file", type=Path, help=KEY_FILE_HELP)

    benchmark_parser = subparsers.add_parser("benchmark", help="픽셀 모드·모델 프로필 조합 매트릭스 벤치마크")
    benchmark_parser.add_argument("folder", type=Path, help="라벨 데이터셋 폴더")
    benchmark_parser.add_argument("--pixel-modes", default="off,deep", help="쉼표로 구분한 픽셀 모드 목록")
    benchmark_parser.add_argument("--model-path", type=Path, action="append", default=[], help="비교할 모델 프로필(반복 지정 가능)")
    benchmark_parser.add_argument("--fusion-profile", type=Path, help="점수 융합 프로필(참고 점수 전용)")
    benchmark_parser.add_argument("--robustness", action="store_true", help="강건성 폴더도 요약")
    benchmark_parser.add_argument("--max-files", type=int, help="평가할 최대 파일 수")
    benchmark_parser.add_argument("--json-out", type=Path, required=True, help="벤치마크 JSON 저장 경로")
    benchmark_parser.add_argument("--md-out", type=Path, help="벤치마크 Markdown 저장")
    benchmark_parser.add_argument("--sign", action="store_true", help=SIGN_HELP)
    benchmark_parser.add_argument("--key-file", type=Path, help=KEY_FILE_HELP)

    fusion_parser = subparsers.add_parser("fusion", help="메타데이터·픽셀·모델·출처 점수 융합 프로필 보정(참고 점수 전용)")
    fusion_parser.add_argument("folder", type=Path, help="라벨 데이터셋 폴더")
    fusion_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="deep", help="픽셀 휴리스틱 모드")
    fusion_parser.add_argument("--model-path", type=Path, help="외부 모델 프로필(기본: models/aide-runtime.json 자동 탐색)")
    fusion_parser.add_argument("--no-default-engine", action="store_true", help=NO_DEFAULT_ENGINE_HELP)
    fusion_parser.add_argument("--target-fpr", type=float, default=0.05, help="목표 오탐률(FPR)")
    fusion_parser.add_argument("--max-files", type=int, help="사용할 최대 파일 수")
    fusion_parser.add_argument("--out", type=Path, required=True, help="융합 프로필 저장 경로")

    calibrate_parser = subparsers.add_parser("calibrate", help="라벨 데이터셋으로 점수 임계값 맞추기")
    calibrate_parser.add_argument("folder", type=Path, help="라벨 데이터셋 폴더")
    calibrate_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="deep", help="픽셀 휴리스틱 모드")
    calibrate_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE, help="픽셀 분석 표본의 최대 변 길이")
    calibrate_parser.add_argument("--target-fpr", type=float, default=0.05, help="목표 오탐률(FPR)")
    calibrate_parser.add_argument("--max-files", type=int, help="사용할 최대 파일 수")
    calibrate_parser.add_argument("--out", type=Path, required=True, help="보정 결과 JSON 저장 경로")
    calibrate_parser.add_argument("--mapping-out", type=Path, help="등위(isotonic) 점수 보정 프로필(매핑표·방법·데이터셋 지문)도 저장 — 값은 데이터셋 의존 신뢰도이며 진실 확률이 아님")
    calibrate_parser.add_argument("--thresholds", type=Path, help=THRESHOLDS_HELP)

    feedback_parser = subparsers.add_parser("feedback", help="감정인 라벨과 검사 점수를 비교해 융합 가중치 제안")
    feedback_parser.add_argument("labels", type=Path, help="감정인 판정 JSONL/JSON: 행마다 `path`, `expected_label`, `notes`(선택) 필드")
    feedback_parser.add_argument("--scan-json", type=Path, help="비교할 이전 scan --json-out 결과(기본: 라벨의 경로를 다시 분석)")
    feedback_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="off", help="픽셀 휴리스틱 모드")
    feedback_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE, help="픽셀 분석 표본의 최대 변 길이")
    feedback_parser.add_argument("--model-path", type=Path, help="재분석에 쓸 외부 모델 프로필(기본: models/aide-runtime.json 자동 탐색)")
    feedback_parser.add_argument("--no-default-engine", action="store_true", help=NO_DEFAULT_ENGINE_HELP)
    feedback_parser.add_argument("--fusion-profile", type=Path, help="조정할 기준 융합 프로필(기본: 내장값)")
    feedback_parser.add_argument("--json-out", type=Path, help="피드백 보고서 JSON 저장")
    feedback_parser.add_argument("--profile-out", type=Path, help="제안된 융합 프로필 저장(적용은 --fusion-profile로 명시)")

    train_parser = subparsers.add_parser("train", help="라벨 데이터셋으로 이식 가능한 임계값 기준선 학습")
    train_parser.add_argument("folder", type=Path, help="라벨 데이터셋 폴더")
    train_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="deep", help="픽셀 휴리스틱 모드")
    train_parser.add_argument("--pixel-max-side", type=int, default=DEFAULT_PIXEL_MAX_SIDE, help="픽셀 분석 표본의 최대 변 길이")
    train_parser.add_argument("--target-fpr", type=float, default=0.05, help="목표 오탐률(FPR)")
    train_parser.add_argument("--max-files", type=int, help="사용할 최대 파일 수")
    train_parser.add_argument("--out", type=Path, required=True, help="기준선 프로필 저장 경로")

    models_parser = subparsers.add_parser("models", help="조사한 탐지기 통합 후보 목록")
    models_parser.add_argument("--focus", help="작업·키·이름·어댑터 대상으로 거르기")
    models_parser.add_argument("--json-out", type=Path, help="후보 목록 JSON 저장")
    models_parser.add_argument("--profile-out", type=Path, help="후보 체크포인트용 런타임 프로필 저장")
    models_parser.add_argument("--candidate", default="aide-iclr-2025", help="프로필을 만들 후보 키")
    models_parser.add_argument("--checkpoint", type=Path, help="후보 체크포인트 파일")
    models_parser.add_argument("--runtime", choices=["onnx", "torchscript"], help="체크포인트 런타임")
    models_parser.add_argument("--input-size", type=int, default=224, help="모델 입력 크기(픽셀)")
    models_parser.add_argument("--score-index", type=int, default=1, help="출력에서 생성 점수로 쓸 인덱스")

    neural_parser = subparsers.add_parser("train-neural-plan", help="신경망 학습·ONNX 내보내기 계획 작성")
    neural_parser.add_argument("folder", type=Path, help="학습 데이터셋 폴더")
    neural_parser.add_argument("--out", type=Path, required=True, help="계획 JSON 저장 경로")
    neural_parser.add_argument("--output-dir", type=Path, required=True, help="학습 산출물 폴더")
    neural_parser.add_argument("--architecture", default="convnext_tiny", help="모델 구조 이름")
    neural_parser.add_argument("--image-size", type=int, default=224, help="입력 이미지 크기(픽셀)")
    neural_parser.add_argument("--epochs", type=int, default=10, help="학습 에폭 수")

    video_parser = subparsers.add_parser("video", help="이미지 검사용 영상 프레임 추출 계획 작성 또는 실행")
    video_parser.add_argument("folder", type=Path, help="영상 폴더")
    video_parser.add_argument("--out", type=Path, required=True, help="추출 계획 JSON 저장 경로")
    video_parser.add_argument("--frame-root", type=Path, required=True, help="추출 프레임 저장 폴더")
    video_parser.add_argument("--sample-every", type=float, default=2.0, help="프레임 추출 간격(초)")
    video_parser.add_argument("--no-recursive", action="store_true", help="하위 폴더를 탐색하지 않음")
    video_parser.add_argument("--extract", action="store_true", help="계획 작성 후 ffmpeg 명령 실행")
    video_parser.add_argument("--extract-limit", type=int, help="추출할 최대 영상 수")

    audio_parser = subparsers.add_parser("audio", help=f"오디오 음향 계층 — {LAYER_DIAGNOSTIC}")
    audio_parser.add_argument("file", type=Path, help="분석할 오디오 파일")
    audio_parser.add_argument("--segment-seconds", type=int, default=30, help="분석할 최대 길이(초, 기본: 30)")
    audio_parser.add_argument("--model-path", type=Path, help="외부 오디오 모델 프로필(기본: models/aasist-runtime.json 자동 탐색)")
    audio_parser.add_argument("--no-default-engine", action="store_true", help=NO_DEFAULT_ENGINE_HELP)
    audio_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    audio_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    face_parser = subparsers.add_parser("face", help=f"얼굴 조작 계층 — {LAYER_DIAGNOSTIC}")
    face_parser.add_argument("file", type=Path, help="분석할 이미지 파일")
    face_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    face_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    video_analysis_parser = subparsers.add_parser("video-analysis", help=f"영상 시간 일관성 계층 — {LAYER_DIAGNOSTIC}")
    video_analysis_parser.add_argument("file", type=Path, help="분석할 영상 파일")
    video_analysis_parser.add_argument("--frame-rate", type=float, default=1.0, help="프레임 표본 추출 속도(기본: 초당 1.0)")
    video_analysis_parser.add_argument("--max-frames", type=int, default=100, help="분석할 최대 프레임 수(기본: 100)")
    video_analysis_parser.add_argument("--model-path", type=Path, nargs="*", help="영상 모달리티 모델 프로필 — 예: models/community-forensics-frames-runtime.json 은 추출 프레임을 이미지 탐지기로 채점")
    video_analysis_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    video_analysis_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    inpaint_parser = subparsers.add_parser("inpaint", help=f"인페인팅·부분 편집 계층 — {LAYER_DIAGNOSTIC}")
    inpaint_parser.add_argument("file", type=Path, help="분석할 이미지 파일")
    inpaint_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    inpaint_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    text_advanced_parser = subparsers.add_parser("text-advanced", help=f"텍스트 통계 계층 — {LAYER_DIAGNOSTIC}")
    text_advanced_parser.add_argument("file", type=Path, help="분석할 텍스트 파일")
    text_advanced_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    text_advanced_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    compare_parser = subparsers.add_parser("compare", help=f"동일 화자·동일 작성자 유사도 — {LAYER_DIAGNOSTIC}")
    compare_parser.add_argument("file_a", type=Path, help="첫 번째 파일(오디오 쌍 또는 텍스트/문서 쌍)")
    compare_parser.add_argument("file_b", type=Path, help="두 번째 파일")
    compare_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    compare_parser.add_argument("--ecapa-revision", help="SpeechBrain ECAPA 화자 모델의 고정 허브 커밋 SHA(16진 40자리); 기본 $DEEPFAKE_LENS_ECAPA_REVISION, 없으면 ECAPA를 불러오지 않음(MFCC 대체)")

    watermark_parser = subparsers.add_parser("watermark", help="알고 있는 비밀값으로 텍스트의 KGW 또는 SynthID 워터마크 검사")
    watermark_parser.add_argument("file", type=Path, help="검사할 텍스트/문서 파일")
    watermark_parser.add_argument("--secret", help="생성 시 쓴 KGW 녹색 목록 비밀값")
    watermark_parser.add_argument("--synthid-keys", help="생성 시 쓴 SynthID-Text 정수 키(쉼표 구분; 지정하면 KGW 대신 SynthID 평균-g 검출)")
    watermark_parser.add_argument("--tokenizer", default="Qwen/Qwen2.5-0.5B", help="HF 토크나이저 모델 또는 로컬 경로")
    watermark_parser.add_argument("--tokenizer-revision", default="", help="--tokenizer 의 고정 허브 커밋(16진 40자리, 필수 — 고정되지 않은 토크나이저는 거부)")
    watermark_parser.add_argument("--gamma", type=float, default=0.25, help="생성 시 쓴 녹색 목록 비율")
    watermark_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)

    forensic_parser = subparsers.add_parser("forensic", help="세 가지 결론(scan과 같은 경로)과 출처 메타데이터 계층 진단")
    forensic_parser.add_argument("file", type=Path, help="분석할 파일")
    forensic_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    forensic_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    classify_parser = subparsers.add_parser("classify", help="세 가지 결론(scan과 같은 경로)과 참고용 AI 도구 표식 후보")
    classify_parser.add_argument("file", type=Path, help="분석할 파일")
    classify_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    classify_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    multimodal_parser = subparsers.add_parser("multimodal", help="여러 파일의 종합 결론(점수 입력은 참고용 계층 진단)")
    multimodal_parser.add_argument("files", type=Path, nargs="*", help="scan 경로로 분석할 파일들 — 종합 결론은 결정 규칙 순서를 따름")
    multimodal_parser.add_argument("--image-score", type=int, help="이미지 원점수(참고용, 결론 아님)")
    multimodal_parser.add_argument("--text-score", type=int, help="텍스트 분석 점수(참고용)")
    multimodal_parser.add_argument("--audio-score", type=int, help="오디오 분석 점수(참고용)")
    multimodal_parser.add_argument("--video-score", type=int, help="영상 분석 점수(참고용)")
    multimodal_parser.add_argument("--image-source", type=str, help="이미지 출처 추정")
    multimodal_parser.add_argument("--text-source", type=str, help="텍스트 출처 추정")
    multimodal_parser.add_argument("--audio-source", type=str, help="오디오 출처 추정")
    multimodal_parser.add_argument("--video-source", type=str, help="영상 출처 추정")
    multimodal_parser.add_argument("--av-sync", type=Path, help="음성/영상 동기 검사용 영상 파일(opencv+librosa 필요)")
    multimodal_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    multimodal_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    realtime_parser = subparsers.add_parser("realtime", help="보정 전 프레임 점수의 이동 평균(계층 진단, 결론 없음)")
    realtime_parser.add_argument("--window-size", type=int, default=30, help="이동 평균 창 크기(기본: 30)")
    realtime_parser.add_argument("--alert-threshold", type=int, default=None, help="넘으면 기록할 운영자 임계값(선택; 기본값 없음 — 예전 67 기준은 측정된 적이 없음)")
    realtime_parser.add_argument("--warning-threshold", type=int, default=None, help="더 이상 쓰지 않으며 무시됨(예전 '주의' 단계는 없어짐)")
    realtime_parser.add_argument("--scores", type=str, help="처리할 점수 목록(쉼표 구분, 시험용)")
    realtime_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    realtime_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    rppg_parser = subparsers.add_parser("rppg", help=f"rPPG 맥박 계층(CHROM) — {LAYER_DIAGNOSTIC}")
    rppg_parser.add_argument("file", type=Path, help="분석할 영상 파일")
    rppg_parser.add_argument("--max-frames", type=int, default=600, help="수집할 최대 얼굴 표본 수(기본: 600)")
    rppg_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    rppg_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    prnu_parser = subparsers.add_parser("prnu", help=f"PRNU 센서 지문 상관 계층 — {LAYER_DIAGNOSTIC}")
    prnu_parser.add_argument("file", type=Path, help="확인할 대상 이미지")
    prnu_parser.add_argument("--reference", type=Path, action="append", required=True, help="같은 기기의 기준 이미지(3번 이상 반복 지정)")
    prnu_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    prnu_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    evidence_parser = subparsers.add_parser("evidence", help="포렌식 증거 연속성(체인) 기록 작성")
    evidence_parser.add_argument("file", type=Path, help="증거 기록을 만들 파일")
    evidence_parser.add_argument("--analyst-id", type=str, default="system", help="분석자 식별자")
    evidence_parser.add_argument("--output", type=Path, help="증거 기록 저장 파일")

    api_parser = subparsers.add_parser("api-serve", help="REST API 서버 시작(fastapi/uvicorn 필요)")
    api_parser.add_argument("--host", type=str, default="127.0.0.1", help="바인딩할 호스트")
    api_parser.add_argument("--port", type=port_number, default=8765, metavar="<포트>", help=f"수신 포트({PORT_MIN}–{PORT_MAX})")
    api_parser.add_argument("--token", type=str, help="/api 요청에 X-API-Token 헤더를 요구(localhost 외 호스트에서는 필수)")
    api_parser.add_argument("--allow-root", type=Path, action="append", default=[], help="API가 읽을 수 있는 폴더(반복 지정 가능); 모든 --allow-root 밖의 경로 요청은 403")

    batch_parser = subparsers.add_parser("batch", help="파일 일괄 처리")
    batch_parser.add_argument("folder", type=Path, help="처리할 폴더")
    batch_parser.add_argument("--workers", type=int, default=4, help="작업자 수")
    batch_parser.add_argument("--output", type=Path, help="결과 저장 파일")

    explain_parser = subparsers.add_parser("explain", help="파일의 결론을 만든 결정 규칙 설명")
    explain_parser.add_argument("file", type=Path, nargs="?", help="scan 경로로 분석하고 설명할 파일")
    explain_parser.add_argument("--score", type=int, help="더 이상 쓰지 않음: 원점수만으로는 설명할 수 없음(계층 진단)")
    explain_parser.add_argument("--signals", type=str, help="더 이상 쓰지 않음: --score 진단에 그대로 싣는 JSON 신호 배열")
    explain_parser.add_argument("--format", choices=["text", "json"], default="text", help=OUTPUT_FORMAT_HELP)

    agent_parser = subparsers.add_parser("agent", help="세 가지 결론의 텍스트 결과(참고 등급)와 AI 에이전트 표식 계층 진단")
    agent_parser.add_argument("--text", type=str, help="분석할 텍스트")
    agent_parser.add_argument("--file", type=Path, help="분석할 파일")
    agent_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    agent_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    threed_parser = subparsers.add_parser("3d", help=f"3D 생성 표식 계층 — {LAYER_DIAGNOSTIC}")
    threed_parser.add_argument("--file", type=Path, help="분석할 파일")
    threed_parser.add_argument("--text", type=str, help="분석할 텍스트")
    threed_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    threed_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    avatar_parser = subparsers.add_parser("avatar", help=f"AI 아바타 표식 계층 — {LAYER_DIAGNOSTIC}")
    avatar_parser.add_argument("--file", type=Path, help="분석할 파일")
    avatar_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    avatar_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    pixel_parser = subparsers.add_parser("pixel-analysis", help=f"사진 판별 뒤의 픽셀 사전 선별 계층 — {LAYER_DIAGNOSTIC}")
    pixel_parser.add_argument("file", type=Path, help="분석할 이미지 파일")
    pixel_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    pixel_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    ml_parser = subparsers.add_parser("ml-classify", help=f"특징 임계값 규칙 계층 — {LAYER_DIAGNOSTIC}")
    ml_parser.add_argument("file", type=Path, help="분석할 이미지 파일")
    ml_parser.add_argument("--format", choices=["table", "json"], default="json", help=OUTPUT_FORMAT_HELP)
    ml_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    legal_parser = subparsers.add_parser("legal-report", help="검사 결과(결론·근거·검사 범위)로 만든 법률 포렌식 보고서")
    legal_parser.add_argument("file", type=Path, help="분석할 파일")
    legal_parser.add_argument("--output", type=Path, help="한국어 보고서 텍스트를 이 파일에 저장")
    legal_parser.add_argument("--json-out", type=Path, help="서명된 보고서 JSON 저장(verify-report로 검증)")
    legal_parser.add_argument("--key-file", type=Path, help="보고서 서명 키 파일(기본: DEEPFAKE_LENS_REPORT_KEY 환경 변수); 키가 없으면 보고서에 서명 없음 표시")
    legal_parser.add_argument("--analyst-id", type=str, default="system", help="보고서에 기록할 분석자 식별자")
    legal_parser.add_argument("--format", choices=["text", "json"], default="text", help="표준 출력 형식")

    verify_parser = subparsers.add_parser("verify-report", help="서명된 보고서 JSON 검증(종료 코드 0 검증됨, 1 변조됨, 2 키 ID 불일치, 3 서명 없음, 4 사용 오류)")
    verify_parser.add_argument("report", type=Path, help="서명된 보고서 JSON(scan --json-out --sign, legal-report --json-out, evidence-statement --json-out 등)")
    verify_parser.add_argument("--key-file", type=Path, help="검증 키 파일(기본: DEEPFAKE_LENS_REPORT_KEY 환경 변수; 빈 파일이면 오류)")
    verify_parser.add_argument("--format", choices=["text", "json"], default="text", help="표준 출력 형식")

    perf_parser = subparsers.add_parser("perf", help="검사 처리량과 캐시·해시 동작 측정")
    perf_parser.add_argument("folder", type=Path, help="측정할 폴더")
    perf_parser.add_argument("--pixel", choices=sorted(SUPPORTED_PIXEL_MODES), default="off", help="픽셀 휴리스틱 모드")
    perf_parser.add_argument("--workers", type=int, default=1, help="작업자 수")
    perf_parser.add_argument("--cache", type=Path, help="검사 캐시 파일")
    perf_parser.add_argument("--hash-db", type=Path, help="중복 판정용 해시 파일")
    perf_parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help=f"측정할 최대 파일 수(기본: {DEFAULT_MAX_FILES})")
    perf_parser.add_argument("--no-recursive", action="store_true", help="하위 폴더를 탐색하지 않음")
    perf_parser.add_argument("--out", type=Path, required=True, help="측정 결과 JSON 저장 경로")

    release_parser = subparsers.add_parser("release", help="배포 준비 점검표 작성")
    release_parser.add_argument("--out", type=Path, required=True, help="점검표 JSON 저장 경로")

    security_parser = subparsers.add_parser("security", help="로컬 전용 보안 가드레일 보고서 작성")
    security_parser.add_argument("--out", type=Path, required=True, help="보고서 JSON 저장 경로")

    web_parser = subparsers.add_parser("web", help="로컬 웹 앱(GUI) 시작")
    web_parser.add_argument("--folder", type=Path, help="GUI가 처음 열 증거 폴더(읽기 허용 폴더로 등록됨)")
    web_parser.add_argument("--host", default="127.0.0.1", help="바인딩할 호스트")
    web_parser.add_argument("--port", type=port_number, default=8765, metavar="<포트>", help=f"수신 포트({PORT_MIN}–{PORT_MAX})")
    web_parser.add_argument("--allow-lan", action="store_true", help="같은 네트워크의 다른 기기 접속 허용(--token 필수)")
    web_parser.add_argument("--token", default=None, help="--allow-lan 때 요구할 API 토큰")
    web_parser.add_argument("--models-dir", type=Path, help="vendor-weights --install로 준비한 모델 폴더")
    web_parser.add_argument("--allow-root", type=Path, action="append", default=[], help="추가로 읽기를 허용할 폴더(반복 지정 가능); --folder/--allow-root 밖의 경로 요청은 403")

    doctor_parser = subparsers.add_parser("doctor", help="모델 가중치·가속기·의존성 진단")
    doctor_parser.add_argument("--format", choices=["table", "json"], default="table", help="표준 출력 형식")
    doctor_parser.add_argument("--json-out", type=Path, help="진단 결과를 JSON으로 저장")
    doctor_parser.add_argument("--models-dir", type=Path, help="진단할 모델 폴더(기본: 패키지 models/ 또는 $DEEPFAKE_LENS_MODELS_DIR)")

    faceswap_parser = subparsers.add_parser("faceswap-seam", help=f"페이스스왑 경계면 계층 — {LAYER_DIAGNOSTIC}")
    faceswap_parser.add_argument("--thresholds", type=Path, help=THRESHOLDS_HELP)
    faceswap_parser.add_argument("file", type=Path, help="분석할 이미지 파일")
    faceswap_parser.add_argument("--format", choices=["table", "json"], default="table", help=OUTPUT_FORMAT_HELP)
    faceswap_parser.add_argument("--json-out", type=Path, help=JSON_OUT_HELP)

    evidence_stmt_parser = subparsers.add_parser("evidence-statement", help="ECFS 전자소송 증거설명서 작성")
    evidence_stmt_parser.add_argument("target", type=Path, help="검사할 폴더, 검사 결과 JSON 또는 파일 하나")
    evidence_stmt_parser.add_argument("--case-no", type=str, default="(사건번호 입력)", help="사건번호")
    evidence_stmt_parser.add_argument("--case-name", type=str, default="성폭력처벌법위반(허위영상물편집등) 및 정보통신망법위반", help="사건명")
    evidence_stmt_parser.add_argument("--plaintiff", type=str, default="(의뢰사 상호명 입력) 귀하", help="원고(고소인)")
    evidence_stmt_parser.add_argument("--defendant", type=str, default="(피고/피의자 성명 입력)", help="피고(피고소인)")
    evidence_stmt_parser.add_argument("--court", type=str, default="○○지방법원 귀중", help="제출처(관할법원/수사관서)")
    evidence_stmt_parser.add_argument("--law-firm", type=str, default=None, help=LAW_FIRM_HELP)
    evidence_stmt_parser.add_argument("--contact", type=str, default=None, help=CONTACT_HELP)
    evidence_stmt_parser.add_argument("--center", type=str, default="디지털포렌식 감정센터", help="머리글의 감정센터 이름")
    evidence_stmt_parser.add_argument("--pdf-out", type=Path, help="증거설명서 PDF 저장")
    evidence_stmt_parser.add_argument("--md-out", type=Path, help="증거설명서 Markdown 저장")
    evidence_stmt_parser.add_argument("--json-out", type=Path, help="서명된 증거설명서 JSON 저장(verify-report로 검증)")
    evidence_stmt_parser.add_argument("--key-file", type=Path, help="서명 키 파일(기본: DEEPFAKE_LENS_REPORT_KEY 환경 변수; 빈 파일이면 오류); 키가 없으면 모든 출력에 서명 없음 표시")
    evidence_stmt_parser.add_argument("--format", choices=["table", "json", "markdown"], default="table", help="표준 출력 형식")
    # X1: a folder target is scanned with scan's options and defaults.
    evidence_stmt_parser.add_argument("--recursive", action="store_true", help="폴더 입력: 하위 폴더까지 검사(scan과 같음; 기본은 바로 아래 파일만 — 건너뛴 하위 폴더 수는 '기록되지 않은 파일'에 표시)")
    evidence_stmt_parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help=f"폴더 입력: 검사할 최대 파일 수(scan과 같은 기본값 {DEFAULT_MAX_FILES}; 초과 파일 수는 '기록되지 않은 파일'에 표시)")
    evidence_stmt_parser.add_argument("--allow-symlinks", action="store_true", help="폴더 입력: 심볼릭 링크를 따라가 분석(scan과 같음)")

    vendor_parser = subparsers.add_parser("vendor-weights", help="망분리 감정실용 모델 가중치 검증·고정·오프라인 묶음")
    vendor_parser.add_argument("action", nargs="?", choices=["pin"], help="'pin <프로필>': 체크포인트 sha256 또는 허브 커밋 revision을 프로필 pin에 기록")
    vendor_parser.add_argument("profile", nargs="?", help="'pin' 대상 프로필 이름(예: aasist) 또는 경로")
    vendor_parser.add_argument("--revision", help="허브 모델 'pin'에서 허브 조회 대신 쓸 40자리 16진 커밋")
    vendor_parser.add_argument("--models-dir", type=Path, help="모델 폴더 경로(기본: 패키지 models/)")
    vendor_parser.add_argument("--verify", action="store_true", help="오프라인 가중치의 SHA-256 무결성 검증")
    vendor_parser.add_argument("--fetch", action="store_true", help="선언된 checkpoint_url 가중치를 내려받아 SHA-256 검증 후 저장")
    vendor_parser.add_argument("--offline", action="store_true", help="모든 네트워크 접근 거부(망분리 모드)")
    vendor_parser.add_argument("--manifest-out", type=Path, help="오프라인 모델 목록 JSON 저장")
    vendor_parser.add_argument("--bundle-to", type=Path, help="오프라인 가중치 묶음을 이 폴더로 내보내기")
    vendor_parser.add_argument("--copy-weights", action="store_true", help="큰 가중치 파일도 묶음 폴더에 복사")
    vendor_parser.add_argument("--force", action="store_true", help="비어 있지 않은 대상 폴더에도 묶음 생성 허용")
    vendor_parser.add_argument("--install", type=Path, metavar="<묶음 폴더>", help="내려받은 묶음을 --models-dir(또는 --to 대상)에 설치")
    vendor_parser.add_argument("--to", type=Path, help="--install 대상 모델 폴더(기본: --models-dir / DEEPFAKE_LENS_MODELS_DIR)")
    vendor_parser.add_argument("--format", choices=["table", "json", "markdown"], default="table", help="표준 출력 형식")

    # corpus build|split|verify — reproducible corpus manifests (WP-I, G27).
    corpus_parser = add_corpus_parser(subparsers)

    # N8: every command logs per-file failures (with tracebacks) to the log
    # file only; --verbose also prints them on stderr.
    for sub in subparsers.choices.values():
        sub.add_argument("--verbose", action="store_true", help="처리 오류의 상세 로그(트레이스백)를 표준 오류에도 출력(기본: 로그 파일에만 기록)")

    _refuse_empty_paths(parser)
    _korean_metavars(parser)
    return parser, {
        "corpus": corpus_parser,
        "scan": scan_parser, "collect": collect_parser, "dataset": dataset_parser,
        "eval": eval_parser, "benchmark": benchmark_parser, "fusion": fusion_parser,
        "calibrate": calibrate_parser, "feedback": feedback_parser, "train": train_parser,
        "models": models_parser, "train-neural-plan": neural_parser, "video": video_parser,
        "audio": audio_parser, "face": face_parser, "video-analysis": video_analysis_parser,
        "inpaint": inpaint_parser, "text-advanced": text_advanced_parser, "compare": compare_parser,
        "watermark": watermark_parser, "forensic": forensic_parser, "classify": classify_parser,
        "multimodal": multimodal_parser, "realtime": realtime_parser, "rppg": rppg_parser,
        "prnu": prnu_parser, "evidence": evidence_parser, "api-serve": api_parser,
        "batch": batch_parser, "explain": explain_parser, "agent": agent_parser,
        "3d": threed_parser, "avatar": avatar_parser, "pixel-analysis": pixel_parser,
        "ml-classify": ml_parser, "legal-report": legal_parser, "verify-report": verify_parser, "perf": perf_parser,
        "release": release_parser, "security": security_parser, "web": web_parser,
        "doctor": doctor_parser, "faceswap-seam": faceswap_parser,
        "evidence-statement": evidence_stmt_parser, "vendor-weights": vendor_parser,
    }

"""Golden scan output for the output-format-generation meta-test (R15-7, R16-5).

``scan_cache.OUTPUT_FORMAT_GENERATION`` is part of every cache key; it must
be bumped in every commit that changes what a row says for the same input,
or an older commit's cached row is replayed (R14-3). This module scans a
fixed fixture folder and hashes the normalised scan JSON;
``tests/golden_output.json`` records the hashes with the generation they
belong to, and ``tests/test_output_generation.py`` fails when a hash changed
but the generation did not.

R16-5 (round 16): the stdlib-only golden alone did not see output that only
exists when an optional dependency is installed (a changed
``core.NON_PHOTO_NOTICE`` passed — without numpy/opencv no image is ever
classified as a non-photo). Each generation now records three hashes:

- ``stdlib`` — the scan in a child interpreter started with ``-I -S`` (only
  the standard library and this package importable), the same in every
  environment; always compared;
- ``full-extras`` — the scan in a child started with ``-I`` (site packages
  importable) in an environment that has every module of
  :data:`FULL_EXTRAS_REQUIRED` (the ``full`` + ``dev`` extras). The modules
  of :data:`ENVIRONMENT_MODULES` that are installed, with their versions,
  are recorded with it (``full_extras_environment``); the set is compared
  where the current environment has exactly that description — elsewhere
  the comparison is reported as skipped, with the difference;
- ``constants`` — every upper-case module constant of ``result_text`` and
  ``core`` whose value holds Hangul (the user-facing wording, e.g.
  ``NON_PHOTO_NOTICE``), hashed in a stdlib-only child; always compared.

A generation may also be raised without any hash changing (a cache
invalidation for a reason the fixture does not show): the new entry then
carries the reason string, which every new entry must have.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import textwrap
import wave
import zipfile
import zlib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
GOLDEN_PATH = Path(__file__).resolve().parent / "golden_output.json"
CHILD_TIMEOUT_SECONDS = 600
# Keys whose values depend on the clock or on where the folder is.
VOLATILE_KEYS = frozenset({"measured_at", "generated_at", "created_at", "timestamp", "scanned_at", "fitted_at", "scan_root_b64", "tool_version"})

# Member timestamp of the fixture archive (any fixed value).
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
# The scan of the fixture folder, stdlib + deepfake_lens only.
SCAN_SCRIPT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from deepfake_lens.cli import main
    raise SystemExit(main(["scan", sys.argv[2], "--recursive", "--include-low", "--no-default-engine", "--format", "json"]))
    """
)


def _wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 330 * i / 8000))) for i in range(8000)))
    return buffer.getvalue()


# R16-5: side of the gradient fixture — above the 128 px measurement floor
# (image_class.MEASURABLE_MIN_SIDE_PX) so a full-extras scan classifies it (a pattern) and
# prints the non-photo notice.
GRADIENT_SIDE = 192


def _gradient_png() -> bytes:
    """R16-5: a horizontal RGB gradient PNG written with zlib only (identical bytes everywhere)."""
    rows = b"".join(b"\x00" + bytes(channel for x in range(GRADIENT_SIDE) for channel in (x, (x + y) // 2, 255 - y)) for y in range(GRADIENT_SIDE))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", GRADIENT_SIDE, GRADIENT_SIDE, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def build_fixture(root: Path) -> Path:
    """The fixed folder: generation metadata, texts, damaged and unsupported files, an archive."""
    case = root / "case"
    case.mkdir(parents=True)
    benchmark = REPO / "fixtures" / "benchmark"
    for name in ("a1111-metadata-marker.png", "real-like-texture.png"):
        (case / name).write_bytes((benchmark / name).read_bytes())
    (case / "사람 글.txt").write_text("오늘 회의에서 언어 모델 도입 일정을 논의했다. 담당자는 다음 주까지 초안을 정리하기로 했다.\n", encoding="utf-8")
    (case / "ai-style.txt").write_text(
        "As an AI language model, I cannot provide personal opinions. In conclusion, it is important to note that "
        "there are several factors to consider. Furthermore, it is worth mentioning that each case is unique.\n",
        encoding="utf-8",
    )
    (case / "gradient.png").write_bytes(_gradient_png())  # R16-5: a non-photo above the size floor
    (case / "empty.jpg").write_bytes(b"")
    (case / "truncated.jpg").write_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + bytes(range(64)))
    (case / "fake.gif").write_bytes(b"not a gif, only text\n" * 4)
    (case / "tone.wav").write_bytes(_wav_bytes())
    (case / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(32))
    (case / "unknown.xyz").write_bytes(b"\x00\x01\x02 unsupported")
    with zipfile.ZipFile(case / "bundle.zip", "w") as archive:
        for member, text in (("inner/first.txt", "압축 안 첫 번째 메모"), ("second.txt", "압축 안 두 번째 메모")):
            # A fixed timestamp: the archive's bytes (and so its sha256) never depend on the clock.
            archive.writestr(zipfile.ZipInfo(member, date_time=FIXED_ZIP_TIME), text)
    return case


def normalize(node: Any, prefixes: tuple[str, ...]) -> Any:
    """Drop clock/location keys; absolute paths under the fixture folder become <ROOT>."""
    if isinstance(node, dict):
        return {key: normalize(value, prefixes) for key, value in node.items() if key not in VOLATILE_KEYS}
    if isinstance(node, list):
        return [normalize(value, prefixes) for value in node]
    if isinstance(node, str):
        for prefix in prefixes:
            node = node.replace(prefix, "<ROOT>")
        return node
    return node


# R16-5: the golden sets, in record order.
STDLIB_SET = "stdlib"
FULL_EXTRAS_SET = "full-extras"
GOLDEN_SETS = (STDLIB_SET, FULL_EXTRAS_SET)
# R16-5: the interpreter flags of each set's child: -S keeps site packages
# out (stdlib only), -I alone keeps PYTHON* variables and the user site out.
SET_FLAGS = {STDLIB_SET: ("-I", "-S"), FULL_EXTRAS_SET: ("-I",)}
# R16-5: the modules a full-extras environment must have — the pyproject
# ``full`` and ``dev`` extras that change what a default scan says (pixel
# heuristics, image classification, audio features, C2PA).
FULL_EXTRAS_REQUIRED = ("numpy", "cv2", "PIL", "scipy", "librosa", "sklearn", "c2pa")
# R16-5: the optional modules whose presence or version can change scan
# output; the installed ones (with their distribution versions) describe the
# environment a full-extras golden was recorded in.
ENVIRONMENT_MODULES = (
    *FULL_EXTRAS_REQUIRED,
    "soundfile", "mediapipe", "torch", "torchvision", "torchaudio", "transformers", "onnxruntime",
    "speechbrain", "syncnet_python", "syhwp", "olefile", "py7zr", "rarfile", "pymupdf", "fitz",
    "markdown_it", "huggingface_hub",
)
# R16-5: the modules whose user-facing constants are hashed.
CONSTANT_MODULES = ("deepfake_lens.result_text", "deepfake_lens.core")
ENVIRONMENT_SCRIPT = textwrap.dedent(
    """
    import importlib.metadata, importlib.util, json, sys
    distributions = importlib.metadata.packages_distributions()
    out = {}
    for module in sys.argv[1:]:
        if importlib.util.find_spec(module) is None:
            continue
        versions = []
        for dist in sorted(set(distributions.get(module, []))):
            try:
                versions.append(dist + "==" + importlib.metadata.version(dist))
            except importlib.metadata.PackageNotFoundError:
                continue
        out[module] = ",".join(versions) or "installed"
    print(json.dumps(out, sort_keys=True))
    """
)
CONSTANTS_SCRIPT = textwrap.dedent(
    """
    import enum, importlib, json, re, sys
    sys.path.insert(0, sys.argv[1])
    HANGUL = re.compile("[\\uac00-\\ud7a3]")
    NAME = re.compile("^_?[A-Z][A-Z0-9_]*$")
    def canon(value):
        if isinstance(value, enum.Enum):
            return canon(value.value)
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, dict):
            return sorted([json.dumps(canon(k), ensure_ascii=False, sort_keys=True), canon(v)] for k, v in value.items())
        if isinstance(value, (set, frozenset)):
            return sorted(json.dumps(canon(v), ensure_ascii=False, sort_keys=True) for v in value)
        if isinstance(value, (list, tuple)):
            return [canon(v) for v in value]
        return None
    out = {}
    for module_name in sys.argv[2:]:
        module = importlib.import_module(module_name)
        for name, value in sorted(vars(module).items()):
            if not NAME.match(name):
                continue
            text = canon(value)
            if text is None or not HANGUL.search(json.dumps(text, ensure_ascii=False)):
                continue
            out[module_name + "." + name] = text
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    """
)


def _child_env(root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "DEEPFAKE_LENS_"))}
    env.update({"HOME": str(root / "home"), "DEEPFAKE_LENS_LOG_DIR": str(root / "logs"), "TMPDIR": str(root)})
    return env


def _run_child(flags: tuple[str, ...], script: str, args: list[str], root: Path) -> str:
    done = subprocess.run(
        [sys.executable, *flags, "-c", script, *args],
        capture_output=True, env=_child_env(root), timeout=CHILD_TIMEOUT_SECONDS, cwd=str(root),
    )
    if done.returncode != 0:
        raise RuntimeError(f"golden child failed ({done.returncode}): {done.stderr.decode('utf-8', 'replace')[-800:]}")
    return done.stdout.decode("utf-8")


def golden_payload(golden_set: str = STDLIB_SET, repo: Path = REPO) -> Any:
    """The normalised scan JSON of the fixture folder in a child interpreter of ``golden_set`` (package from ``repo``)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        case = build_fixture(root)
        payload = json.loads(_run_child(SET_FLAGS[golden_set], SCAN_SCRIPT, [str(repo), str(case)], root))
        return normalize(payload, (str(case), str(root)))


def environment() -> dict[str, str]:
    """R16-5: ``{module: "dist==version"}`` of the installed :data:`ENVIRONMENT_MODULES` (full-extras child flags)."""
    with tempfile.TemporaryDirectory() as tmp:
        return json.loads(_run_child(SET_FLAGS[FULL_EXTRAS_SET], ENVIRONMENT_SCRIPT, list(ENVIRONMENT_MODULES), Path(tmp).resolve()))


def missing_full_extras(described: dict[str, str]) -> list[str]:
    """R16-5: the :data:`FULL_EXTRAS_REQUIRED` modules this environment lacks."""
    return [module for module in FULL_EXTRAS_REQUIRED if module not in described]


def user_constants(repo: Path = REPO) -> dict[str, Any]:
    """R16-5: ``{module.NAME: value}`` of the Hangul upper-case constants of :data:`CONSTANT_MODULES` (stdlib child, package from ``repo``)."""
    with tempfile.TemporaryDirectory() as tmp:
        return json.loads(_run_child(SET_FLAGS[STDLIB_SET], CONSTANTS_SCRIPT, [str(repo), *CONSTANT_MODULES], Path(tmp).resolve()))


def digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def load_record(path: Path = GOLDEN_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def recorded_sets(entry: dict[str, Any]) -> dict[str, str]:
    """``{set: sha256}`` of a history entry (an R15-7 entry has only ``sha256``, the stdlib set)."""
    sets = dict(entry.get("sets") or {})
    sets.setdefault(STDLIB_SET, entry["sha256"])
    return sets


def check(current: dict[str, str], generation: int, history: list[dict[str, Any]]) -> str:
    """"" when every hash in ``current`` equals the last recorded one of ``generation``, else the Korean reason.

    ``current`` maps a golden set (``stdlib``, ``full-extras``) or
    ``constants`` to the hash in this environment; a key that is absent was
    not computed (full-extras in another environment) and is not compared.
    """
    if not history:
        return "골든 기록이 비어 있습니다 — scripts/update_golden_output.py로 기록하십시오."
    last = history[-1]
    recorded = recorded_sets(last)
    if last.get("constants_sha256"):
        recorded["constants"] = last["constants_sha256"]
    changed = [name for name, value in current.items() if name in recorded and value != recorded[name]]
    if generation == last["generation"]:
        if changed:
            return (
                f"골든이 바뀌었는데({', '.join(changed)}: 골든 "
                + ", ".join(f"{recorded[name][:12]}… → {current[name][:12]}…" for name in changed)
                + f") OUTPUT_FORMAT_GENERATION이 {generation} 그대로입니다 — 올린 뒤 scripts/update_golden_output.py --reason '<사유>'로 새 골든을 기록하십시오 "
                "(올리지 않으면 이전 커밋의 캐시 행이 그대로 재생됩니다, R14-3)."
            )
        return ""
    if generation > last["generation"]:
        # R16-5: a raise is always allowed — but recorded, with its reason.
        return (
            f"OUTPUT_FORMAT_GENERATION이 {generation}인데 골든은 세대 {last['generation']}의 것입니다 — "
            "scripts/update_golden_output.py --reason '<사유>'로 새 세대의 골든을 기록하십시오."
        )
    return f"OUTPUT_FORMAT_GENERATION이 {generation}로 골든 기록의 마지막 세대 {last['generation']}보다 낮습니다."


def history_problems(history: list[dict[str, Any]]) -> list[str]:
    """Generations strictly increasing; R16-5: every entry after the first carries its reason and every set."""
    problems: list[str] = []
    for before, after in zip(history, history[1:]):
        if after["generation"] <= before["generation"]:
            problems.append(f"세대가 증가하지 않음: {before['generation']} → {after['generation']}")
        if not str(after.get("reason") or "").strip():
            problems.append(f"세대 {after['generation']}에 사유(reason)가 기록되지 않음")
        missing = [name for name in GOLDEN_SETS if name not in (after.get("sets") or {})]
        if missing or not after.get("constants_sha256"):
            problems.append(f"세대 {after['generation']}에 빠진 골든: {', '.join(missing + ([] if after.get('constants_sha256') else ['constants']))}")
    return problems

"""Golden scan output for the output-format-generation meta-test (R15-7, round 15).

``scan_cache.OUTPUT_FORMAT_GENERATION`` is part of every cache key; it must
be bumped in every commit that changes what a row says for the same input,
or an older commit's cached row is replayed (R14-3). It used to be bumped by
hand only. This module scans a fixed fixture folder and hashes the
normalised scan JSON; ``tests/golden_output.json`` records the hash with the
generation it belongs to, and ``tests/test_output_generation.py`` fails when
the hash changed but the generation did not.

The scan runs in a child interpreter started with ``-I -S`` (no site
packages): only the standard library and this package are importable, so
the golden is the same in every environment (with or without opencv,
Pillow, librosa, mediapipe …) — the optional layers are recorded as
skipped "의존성 부재: …", whose wording is part of the output too.
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


def golden_payload() -> Any:
    """The normalised scan JSON of the fixture folder (child interpreter, stdlib only)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        case = build_fixture(root)
        env = {key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "DEEPFAKE_LENS_"))}
        env.update({"HOME": str(root / "home"), "DEEPFAKE_LENS_LOG_DIR": str(root / "logs"), "TMPDIR": str(root)})
        done = subprocess.run(
            [sys.executable, "-I", "-S", "-c", SCAN_SCRIPT, str(REPO), str(case)],
            capture_output=True, env=env, timeout=CHILD_TIMEOUT_SECONDS, cwd=str(root),
        )
        if done.returncode != 0:
            raise RuntimeError(f"golden scan failed ({done.returncode}): {done.stderr.decode('utf-8', 'replace')[-800:]}")
        payload = json.loads(done.stdout.decode("utf-8"))
        return normalize(payload, (str(case), str(root)))


def digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def load_record(path: Path = GOLDEN_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check(current_digest: str, generation: int, history: list[dict[str, Any]]) -> str:
    """"" when (digest, generation) is the last recorded pair, else the Korean reason it is not."""
    if not history:
        return "골든 기록이 비어 있습니다 — scripts/update_golden_output.py로 기록하십시오."
    last = history[-1]
    if current_digest != last["sha256"]:
        if generation == last["generation"]:
            return (
                f"고정 픽스처의 스캔 출력이 바뀌었는데(골든 {last['sha256'][:12]}… → {current_digest[:12]}…) "
                f"OUTPUT_FORMAT_GENERATION이 {generation} 그대로입니다 — 올린 뒤 scripts/update_golden_output.py로 새 골든을 기록하십시오 "
                "(올리지 않으면 이전 커밋의 캐시 행이 그대로 재생됩니다, R14-3)."
            )
        return (
            f"OUTPUT_FORMAT_GENERATION이 {generation}인데 골든은 세대 {last['generation']}의 것입니다 — "
            "scripts/update_golden_output.py로 새 골든을 기록하십시오."
        )
    if generation != last["generation"]:
        return f"출력은 그대로인데 세대가 {last['generation']} → {generation}로 바뀌었습니다 — 골든 기록과 세대가 어긋납니다."
    return ""


def history_problems(history: list[dict[str, Any]]) -> list[str]:
    """Generations strictly increasing, each with one distinct digest."""
    problems: list[str] = []
    for before, after in zip(history, history[1:]):
        if after["generation"] <= before["generation"]:
            problems.append(f"세대가 증가하지 않음: {before['generation']} → {after['generation']}")
        if after["sha256"] == before["sha256"]:
            problems.append(f"세대 {after['generation']}의 골든이 이전 세대와 같음(출력이 바뀌지 않았는데 세대만 올림)")
    return problems

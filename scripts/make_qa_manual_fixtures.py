#!/usr/bin/env python3
"""Fixture folders for the manual phase-0 QA checklists (docs/QA-MANUAL.md).

    python scripts/make_qa_manual_fixtures.py --out <dir>

Writes, deterministically:

- ``<out>/formats/``    one sample per supported extension (QA-IN-3, QA-SYS-4;
                         same generator as the automated QA-IN-1 test)
- ``<out>/resume-500/`` 500 files for the kill-and-resume run (QA-SYS-5):
                         300 photo-like PNG scenes (numpy) or text notes
                         without numpy, 150 text notes, 50 WAV tones
- ``<out>/timing/``     the CPU timing set (QA-SYS-8): 100 photo-like
                         1024x768 PNG scenes, 20 WAV (10 s), 30 text notes

Prints a JSON summary (counts, formats not producible here and why).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from deepfake_lens.tests.qa.samples import _wav_bytes, write_samples  # noqa: E402

RESUME_SCENES = 300
RESUME_TEXTS = 150
RESUME_WAVS = 50
TIMING_SCENES = 100
TIMING_WAVS = 20
TIMING_TEXTS = 30
TIMING_SIZE = (1024, 768)
TIMING_WAV_SECONDS = 10.0


def _note(index: int) -> str:
    return f"사건 메모 {index:04d}: 오전 {index % 12 + 1}시 회의. 참석자 {index % 5 + 2}명. 안건 {index % 3 + 1}건 논의."


def _scene(path: Path, seed: int, size: tuple[int, int]) -> bool:
    if importlib.util.find_spec("numpy") is None:
        return False
    from deepfake_lens.tests.qa.test_qa_out import write_scene_png

    write_scene_png(path, seed=seed, width=size[0], height=size[1])
    return True


def build(out: Path) -> dict[str, object]:
    made, unmade = write_samples(out / "formats")
    resume = out / "resume-500"
    resume.mkdir(parents=True, exist_ok=True)
    scenes = 0
    for index in range(RESUME_SCENES):
        if _scene(resume / f"scene-{index:04d}.png", 10_000 + index, (320, 240)):
            scenes += 1
        else:
            (resume / f"scene-{index:04d}.txt").write_text(_note(index), encoding="utf-8")
    for index in range(RESUME_TEXTS):
        (resume / f"note-{index:04d}.txt").write_text(_note(1000 + index), encoding="utf-8")
    for index in range(RESUME_WAVS):
        (resume / f"tone-{index:04d}.wav").write_bytes(_wav_bytes(seconds=1.0 + index % 3))
    timing = out / "timing"
    timing.mkdir(parents=True, exist_ok=True)
    timing_scenes = sum(1 for index in range(TIMING_SCENES) if _scene(timing / f"photo-{index:03d}.png", 20_000 + index, TIMING_SIZE))
    for index in range(TIMING_WAVS):
        (timing / f"speech-{index:03d}.wav").write_bytes(_wav_bytes(seconds=TIMING_WAV_SECONDS))
    for index in range(TIMING_TEXTS):
        (timing / f"doc-{index:03d}.txt").write_text("\n".join(_note(index * 10 + line) for line in range(40)), encoding="utf-8")
    return {
        "formats": {"made": sorted(made), "unmade": unmade},
        "resume-500": {"files": sum(1 for _ in resume.iterdir()), "photo_scenes": scenes},
        "timing": {"photo_scenes": timing_scenes, "wav": TIMING_WAVS, "text": TIMING_TEXTS},
    }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    from deepfake_lens.cli_parser import KoreanArgumentParser  # G13: Korean --help
    parser = KoreanArgumentParser(description="0단계 수동 QA 체크리스트(docs/QA-MANUAL.md)용 픽스처 폴더를 만듭니다.")
    parser.add_argument("--out", type=Path, required=True, help="출력 폴더(새로 만듦)")
    args = parser.parse_args(argv)
    print(json.dumps(build(args.out), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

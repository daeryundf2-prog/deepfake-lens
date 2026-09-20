"""Unified labeled-corpus evaluation for every model member.

    python experiments/eval_all.py --corpus <dir> [--modality image,audio,text]
        [--report <out.json>] [--md <out.md>]

Corpus layout (each modality dir is optional):

    corpus/
      image/real/  image/fake/    # any image suffix
      audio/real/  audio/fake/    # wav/mp3/flac/ogg/m4a
      text/real/   text/fake/     # .txt/.md
      face/real/   [face/fake/]   # like eval_face_manipulation: real images get
                                  # an SBI fake synthesized for the positive class

Per-member metrics: availability, AUROC, EER, FPR@50, recall@50, skipped.
The ensemble row aggregates every available member of the modality.

Members are matched to the modality via each profile's ``modality`` field;
profiles whose members can't run (missing checkpoints/deps) degrade to
available=0 entries — they are reported, not hidden.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from deepfake_lens.evaluation_metrics import auroc, eer, threshold_at_fpr  # noqa: E402
from deepfake_lens.model_adapter import analyze_external_model  # noqa: E402

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
TEXT_SUFFIXES = {".txt", ".md"}


def _collect(directory: Path, suffixes: set[str]) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.suffix.lower() in suffixes)


def _profiles_for(modality: str) -> tuple[list[Path], list[str]]:
    """Profiles matching the modality, reusing the adapter's inference.

    ``face`` is evaluated through image-modality members that declare
    ``crop_faces`` — the crop gate is what makes a face detector. Returns
    (profiles, skipped_names) where skipped are declared-unsupported
    placeholders reported for completeness.
    """
    from deepfake_lens.model_adapter import _profile_modality

    models_dir = REPO_ROOT / "models"
    out, skipped = [], []
    for path in sorted(models_dir.glob("*.json")):
        try:
            prof = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not prof.get("supported", True):
            skipped.append(path.name)
            continue
        if modality == "face":
            if _profile_modality(path) == "image" and prof.get("crop_faces"):
                out.append(path)
        elif _profile_modality(path) == modality:
            out.append(path)
    return out, skipped


def _synthesize_sbi_fakes(real_dir: Path, out_dir: Path, limit: int) -> list[Path]:
    """Generate SBI positives from real faces for the face modality."""
    import numpy as np
    from experiments.sbi import self_blended_image

    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    made = []
    for src in _collect(real_dir, IMAGE_SUFFIXES)[:limit]:
        import cv2
        img = cv2.imread(str(src))
        if img is None:
            continue
        try:
            fake, _mask = self_blended_image(img, rng)
        except Exception:
            continue
        dst = out_dir / f"{src.stem}_sbi.png"
        cv2.imwrite(str(dst), fake)
        made.append(dst)
    return made


def _score_member(path: Path, profile: Path, modality: str) -> tuple[int, bool]:
    """Return (score 0-100, available)."""
    try:
        result = analyze_external_model(path, profile, modality=modality)
    except Exception:
        return 0, False
    if result is None or not result.available:
        return 0, False
    return int(result.score), True


def _metrics(pairs: list[tuple[float, int]]) -> dict:
    if not pairs:
        return {"n": 0}
    n_pos = sum(l for _, l in pairs)
    n_neg = len(pairs) - n_pos
    row = {
        "n": len(pairs),
        "pos": n_pos,
        "neg": n_neg,
        "auroc": auroc(pairs),
        "eer": eer(pairs),
        "thr_at_fpr1": threshold_at_fpr(pairs, 0.01),
        "thr_at_fpr5": threshold_at_fpr(pairs, 0.05),
    }
    at50 = [(s, l) for s, l in pairs if True]
    row["fpr_at_50"] = (
        sum(1 for s, l in at50 if s >= 50 and l == 0) / n_neg if n_neg else None
    )
    row["recall_at_50"] = (
        sum(1 for s, l in at50 if s >= 50 and l == 1) / n_pos if n_pos else None
    )
    return row


def evaluate_modality(corpus: Path, modality: str, sbi_limit: int = 60,
                      max_per_class: int = 0) -> dict:
    suffixes = {"image": IMAGE_SUFFIXES, "audio": AUDIO_SUFFIXES,
                "text": TEXT_SUFFIXES, "face": IMAGE_SUFFIXES}[modality]
    real = _collect(corpus / modality / "real", suffixes)
    fake = _collect(corpus / modality / "fake", suffixes)
    if max_per_class:
        real, fake = real[:max_per_class], fake[:max_per_class]
    if modality == "face" and real and not fake:
        tmp = Path(tempfile.mkdtemp(prefix="evalall_sbi_"))
        fake = _synthesize_sbi_fakes(corpus / modality / "real", tmp, sbi_limit)
    samples = [(p, 0) for p in real] + [(p, 1) for p in fake]
    if not samples:
        return {"modality": modality, "skipped": "no corpus samples"}

    profiles, unsupported = _profiles_for(modality)
    score_modality = "image" if modality == "face" else modality
    members = []
    for prof in profiles:
        pairs, avail, skipped = [], 0, 0
        for path, label in samples:
            score, ok = _score_member(path, prof, score_modality)
            if ok:
                avail += 1
                pairs.append((score, label))
            else:
                skipped += 1
        row = _metrics(pairs)
        row.update({"profile": prof.name, "available": avail, "skipped": skipped})
        members.append(row)

    # Ensemble row: every available member of the modality.
    ens_pairs = []
    for path, label in samples:
        member_scores = []
        for prof in profiles:
            score, ok = _score_member(path, prof, score_modality)
            if ok:
                member_scores.append(score)
        if member_scores:
            ens_pairs.append((sum(member_scores) / len(member_scores), label))
    ensemble = _metrics(ens_pairs)
    ensemble.update({"profile": "<ensemble-mean>", "available": len(ens_pairs)})
    members.append(ensemble)
    return {"modality": modality, "samples": len(samples),
            "real": len(real), "fake": len(fake), "members": members,
            "unsupported_profiles": unsupported}


def _md_table(report: dict) -> str:
    lines = []
    for block in report["modalities"]:
        if "skipped" in block:
            lines.append(f"## {block['modality']} — skipped ({block['skipped']})")
            continue
        lines.append(f"## {block['modality']} — {block['samples']} samples "
                     f"({block['real']} real / {block['fake']} fake)")
        lines.append("| member | avail | AUROC | EER | FPR@50 | recall@50 | thr@FPR5% |")
        lines.append("|---|---|---|---|---|---|---|")
        for m in block["members"]:
            def f(v, pct=True):
                return "-" if v is None else (f"{v:.2f}" if pct else str(v))
            lines.append(
                f"| {m['profile']} | {m['available']}/{m['available']+m.get('skipped',0)} | "
                f"{f(m.get('auroc'))} | {f(m.get('eer'))} | "
                f"{f(m.get('fpr_at_50'))} | {f(m.get('recall_at_50'))} | "
                f"{f(m.get('thr_at_fpr5'), pct=False)} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--modality", default="image,audio,text,face")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--md", type=Path, default=None)
    parser.add_argument("--sbi-limit", type=int, default=60)
    parser.add_argument("--max-per-class", type=int, default=0,
                        help="cap samples per class per modality (0 = no cap)")
    args = parser.parse_args()

    modalities = [m.strip() for m in args.modality.split(",") if m.strip()]
    report = {"corpus": str(args.corpus), "modalities": []}
    for modality in modalities:
        print(f"[eval_all] {modality}...", flush=True)
        block = evaluate_modality(args.corpus, modality, args.sbi_limit,
                                  args.max_per_class)
        report["modalities"].append(block)
        if "skipped" in block:
            print(f"  skipped: {block['skipped']}")
            continue
        for m in block["members"]:
            print(f"  {m['profile']:<45s} avail={m['available']:<3d} "
                  f"auroc={m.get('auroc')} fpr@50={m.get('fpr_at_50')} "
                  f"recall@50={m.get('recall_at_50')}", flush=True)

    if args.report:
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.md:
        args.md.write_text(_md_table(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


SUPPORTED_DATASET_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".txt", ".md"}
POSITIVE_LABELS = {"ai", "fake", "synthetic", "generated", "edited", "deepfake"}
NEGATIVE_LABELS = {"real", "human", "camera", "authentic", "original"}
SPLIT_NAMES = {"train", "val", "valid", "validation", "test"}
ROBUSTNESS_TRANSFORMS = [
    "jpeg_q95",
    "jpeg_q75",
    "resize_75",
    "resize_50",
    "center_crop_90",
    "gaussian_blur_light",
    "screenshot",
    "social_recompress",
]


@dataclass(frozen=True)
class DatasetRecord:
    path: str
    label: str
    split: str
    source: str
    mask_path: str | None = None


@dataclass(frozen=True)
class DatasetSummary:
    root: str
    total: int
    positive: int
    negative: int
    unknown: int
    splits: dict[str, int]
    sources: dict[str, int]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DatasetAudit:
    summary: DatasetSummary
    issues: list[str]
    mask_coverage: dict[str, int | float]
    duplicate_groups: list[dict[str, object]]
    split_coverage: dict[str, int]
    source_coverage: dict[str, int]

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        data["summary"] = self.summary.to_json()
        return data


def discover_dataset(root: Path | str, *, recursive: bool = True, max_files: int | None = None) -> tuple[DatasetSummary, list[DatasetRecord]]:
    root_path = Path(root)
    iterator = root_path.rglob("*") if recursive else root_path.iterdir()
    records: list[DatasetRecord] = []
    for path in iterator:
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_DATASET_EXTENSIONS:
            continue
        if path.name.endswith((".ivy.json", ".model.json")) or ".mask." in path.name:
            continue
        label = _label_for(path, root_path)
        split = _split_for(path, root_path)
        source = _source_for(path, root_path, label, split)
        records.append(DatasetRecord(str(path), label, split, source, _mask_for(path)))
        if max_files is not None and len(records) >= max_files:
            break
    return _summary(root_path, records), records


def write_manifest(
    root: Path | str,
    output_path: Path | str,
    *,
    recursive: bool = True,
    include_fingerprints: bool = False,
) -> tuple[DatasetSummary, list[DatasetRecord]]:
    summary, records = discover_dataset(root, recursive=recursive)
    payload_records = [_record_payload(record, include_fingerprints=include_fingerprints) for record in records]
    payload = {"summary": summary.to_json(), "records": payload_records}
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary, records


def audit_dataset(root: Path | str, *, recursive: bool = True) -> DatasetAudit:
    summary, records = discover_dataset(root, recursive=recursive)
    issues: list[str] = []
    if summary.total == 0:
        issues.append("dataset contains no supported files")
    if summary.positive == 0:
        issues.append("dataset has no positive ai/fake/synthetic labels")
    if summary.negative == 0:
        issues.append("dataset has no negative real/camera/authentic labels")
    if summary.unknown:
        issues.append(f"dataset has {summary.unknown} files with unknown labels")
    missing_splits = {"train", "val", "test"} - set(summary.splits)
    if missing_splits:
        issues.append("dataset is missing explicit splits: " + ", ".join(sorted(missing_splits)))

    positive_records = [record for record in records if is_positive_label(record.label)]
    masks = sum(1 for record in positive_records if record.mask_path)
    mask_coverage = {
        "positive_samples": len(positive_records),
        "with_mask": masks,
        "coverage": masks / max(1, len(positive_records)),
    }
    duplicate_groups = _duplicate_groups(records)
    if duplicate_groups:
        issues.append(f"dataset has {len(duplicate_groups)} duplicate content groups")

    return DatasetAudit(
        summary=summary,
        issues=issues,
        mask_coverage=mask_coverage,
        duplicate_groups=duplicate_groups,
        split_coverage=dict(sorted(summary.splits.items())),
        source_coverage=dict(sorted(summary.sources.items())),
    )


def write_audit(root: Path | str, output_path: Path | str, *, recursive: bool = True) -> DatasetAudit:
    audit = audit_dataset(root, recursive=recursive)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(audit.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def plan_dataset_splits(
    root: Path | str,
    *,
    recursive: bool = True,
    train_ratio: float = 0.8,
    val_ratio: float = 0.1,
    test_ratio: float = 0.1,
    seed: str = "deepfake-lens-v1",
) -> dict[str, object]:
    summary, records = discover_dataset(root, recursive=recursive)
    total_ratio = train_ratio + val_ratio + test_ratio
    if total_ratio <= 0:
        raise ValueError("split ratios must add up to a positive number")
    train_cut = train_ratio / total_ratio
    val_cut = (train_ratio + val_ratio) / total_ratio
    planned = []
    counts: dict[str, int] = {"train": 0, "val": 0, "test": 0}
    for record in records:
        value = _stable_unit_interval(seed, record.path)
        split = "train" if value < train_cut else "val" if value < val_cut else "test"
        counts[split] += 1
        planned.append({**asdict(record), "planned_split": split, "fingerprint": file_fingerprint(record.path)})
    return {
        "version": "dataset-split-plan-v1",
        "root": str(Path(root)),
        "ratios": {"train": train_ratio, "val": val_ratio, "test": test_ratio},
        "seed": seed,
        "summary": summary.to_json(),
        "counts": counts,
        "records": planned,
    }


def write_split_plan(root: Path | str, output_path: Path | str, **kwargs) -> dict[str, object]:
    plan = plan_dataset_splits(root, **kwargs)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def build_robustness_plan(root: Path | str, *, recursive: bool = True) -> dict[str, object]:
    summary, records = discover_dataset(root, recursive=recursive)
    items = []
    for record in records:
        if record.label == "unknown":
            continue
        items.append(
            {
                "path": record.path,
                "label": record.label,
                "source": record.source,
                "split": record.split,
                "transforms": ROBUSTNESS_TRANSFORMS,
            }
        )
    return {
        "version": "robustness-plan-v1",
        "summary": summary.to_json(),
        "transforms": ROBUSTNESS_TRANSFORMS,
        "items": items,
        "notes": [
            "Generate these variants with an external image tool, then place them under folders named by transform.",
            "Evaluate clean and transformed folders separately to measure robustness drop.",
        ],
    }


def write_robustness_plan(root: Path | str, output_path: Path | str, *, recursive: bool = True) -> dict[str, object]:
    plan = build_robustness_plan(root, recursive=recursive)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def is_positive_label(label: str) -> bool:
    return label in {"ai", "edited"}


def is_negative_label(label: str) -> bool:
    return label == "real"


def _label_for(path: Path, root: Path) -> str:
    parts = [part.lower() for part in _relative_parts(path, root)]
    if any(part in POSITIVE_LABELS for part in parts):
        return "edited" if "edited" in parts else "ai"
    if any(part in NEGATIVE_LABELS for part in parts):
        return "real"
    return "unknown"


def _split_for(path: Path, root: Path) -> str:
    for part in _relative_parts(path, root):
        lowered = part.lower()
        if lowered in SPLIT_NAMES:
            return "val" if lowered in {"valid", "validation"} else lowered
    return "unspecified"


def _source_for(path: Path, root: Path, label: str, split: str) -> str:
    for part in _relative_parts(path, root)[:-1]:
        lowered = part.lower()
        if lowered in POSITIVE_LABELS or lowered in NEGATIVE_LABELS or lowered in SPLIT_NAMES:
            continue
        return lowered
    return label if label != "unknown" else split


def _mask_for(path: Path) -> str | None:
    candidates = [
        path.with_suffix(".mask.png"),
        path.with_suffix(path.suffix + ".mask.png"),
        path.parent / f"{path.stem}_mask.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _relative_parts(path: Path, root: Path) -> tuple[str, ...]:
    try:
        return path.relative_to(root).parts
    except ValueError:
        return path.parts


def _summary(root: Path, records: list[DatasetRecord]) -> DatasetSummary:
    splits: dict[str, int] = {}
    sources: dict[str, int] = {}
    for record in records:
        splits[record.split] = splits.get(record.split, 0) + 1
        sources[record.source] = sources.get(record.source, 0) + 1
    return DatasetSummary(
        root=str(root),
        total=len(records),
        positive=sum(1 for record in records if is_positive_label(record.label)),
        negative=sum(1 for record in records if is_negative_label(record.label)),
        unknown=sum(1 for record in records if record.label == "unknown"),
        splits=splits,
        sources=sources,
    )


def _record_payload(record: DatasetRecord, *, include_fingerprints: bool) -> dict[str, object]:
    payload = asdict(record)
    if include_fingerprints:
        payload["fingerprint"] = file_fingerprint(record.path)
    return payload


def file_fingerprint(path: Path | str) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _duplicate_groups(records: list[DatasetRecord]) -> list[dict[str, object]]:
    by_hash: dict[str, list[DatasetRecord]] = {}
    for record in records:
        fingerprint = file_fingerprint(record.path)
        if fingerprint:
            by_hash.setdefault(fingerprint, []).append(record)
    groups = []
    for fingerprint, members in sorted(by_hash.items()):
        if len(members) < 2:
            continue
        groups.append({"fingerprint": fingerprint, "paths": [member.path for member in members], "count": len(members)})
    return groups


def _stable_unit_interval(seed: str, value: str) -> float:
    digest = hashlib.sha256(f"{seed}:{value}".encode("utf-8")).digest()
    number = int.from_bytes(digest[:8], "big")
    return number / float(2**64 - 1)

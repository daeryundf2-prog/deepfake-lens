"""Air-gapped forensic lab model weights profiler, bundler, and integrity verifier.

Digital forensic laboratories (대검찰청 사이버수사부, 국과수, 경찰청 안보수사대,
법무법인 포렌식센터) operate within strictly air-gapped (망분리/폐쇄망) environments
where external downloads (Hugging Face, PyTorch Hub, GitHub) are blocked at the firewall.

This module inspects all committed model runtime profiles, verifies pre-cached
weights and their SHA-256 cryptographic hashes, and bundles a self-contained
offline distribution package with an `offline_manifest.json` for court-admissible
forensic deployments.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelWeightEntry:
    name: str
    runtime_profile: str
    checkpoint_relpath: str
    checkpoint_abspath: str
    exists: bool
    size_bytes: int
    sha256: str | None
    expected_sha256: str | None
    integrity_status: str  # verified, missing, unhashed, mismatch
    modality: str
    engine: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VendorManifest:
    generated_at: str
    models_dir: str
    total_profiles: int
    available_weights: int
    missing_weights: int
    total_bytes: int
    entries: list[ModelWeightEntry]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown(self) -> str:
        lines = [
            "# 포렌식 폐쇄망(Air-Gapped) AI 모델 가중치 검증 보고서",
            "",
            f"**검증 일시**: {self.generated_at}",
            f"**모델 디렉터리**: `{self.models_dir}`",
            f"**총 런타임 프로파일**: {self.total_profiles}개",
            f"**탑재 완료 가중치**: {self.available_weights}개 (미탑재: {self.missing_weights}개)",
            f"**총 가중치 용량**: {self.total_bytes / (1024 * 1024):.1f} MB",
            "",
            "| 모델명 | 모달리티 | 엔진 | 상태 | 용량 (MB) | SHA-256 무결성 해시 |",
            "| :--- | :--- | :--- | :--- | :---: | :--- |",
        ]
        for e in self.entries:
            size_mb = f"{e.size_bytes / (1024 * 1024):.2f}" if e.exists else "-"
            hash_display = f"`{e.sha256[:16]}...`" if e.sha256 else "N/A"
            status_badge = "✅ 정상 탑재" if e.integrity_status in ("verified", "present") else ("❌ 미탑재" if e.integrity_status == "missing" else "⚠️ 불일치")
            lines.append(
                f"| **{e.name}** | {e.modality} | {e.engine} | {status_badge} | {size_mb} | {hash_display} |"
            )

        lines.extend([
            "",
            "> [!NOTE]",
            "> 폐쇄망 분석 환경에서는 외부 통신이 전면 차단되므로, 분석 착수 전 전수 SHA-256 무결성 검증을 필히 완료해야 합니다.",
        ])
        return "\n".join(lines)


def _compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(256 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _infer_modality(name: str, profile_data: dict[str, Any]) -> str:
    mod = str(profile_data.get("modality", "")).lower()
    if mod in ("image", "audio", "text", "video"):
        return mod
    n = name.lower()
    if "audio" in n or "wav" in n or "aasist" in n:
        return "audio"
    if "text" in n or "roberta" in n or "ppl" in n or "detector" in n and "openai" in n:
        return "text"
    if "video" in n or "frames" in n or "temporal" in n:
        return "video"
    return "image"


def inspect_model_manifest(models_dir: Path | str | None = None) -> VendorManifest:
    """Scan models directory and build an air-gap verification manifest."""
    base_dir = (
        Path(models_dir).resolve()
        if models_dir is not None
        else Path(__file__).resolve().parent.parent / "models"
    )

    entries: list[ModelWeightEntry] = []
    total_bytes = 0
    available_cnt = 0
    missing_cnt = 0

    if not base_dir.is_dir():
        return VendorManifest(
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            models_dir=str(base_dir),
            total_profiles=0,
            available_weights=0,
            missing_weights=0,
            total_bytes=0,
            entries=[],
        )

    for profile_path in sorted(base_dir.glob("*-runtime.json")):
        name = profile_path.stem.replace("-runtime", "")
        try:
            data = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        checkpoint_rel = data.get("checkpoint") or data.get("weights") or ""
        engine = str(data.get("engine", data.get("runtime", "pytorch")))
        modality = _infer_modality(name, data)
        expected_sha = data.get("sha256") or data.get("expected_sha256")

        if checkpoint_rel:
            chk_path = (base_dir / checkpoint_rel).resolve() if not Path(checkpoint_rel).is_absolute() else Path(checkpoint_rel)
            exists = chk_path.is_file()
            size = chk_path.stat().st_size if exists else 0
            file_sha = _compute_file_sha256(chk_path) if exists else None

            if exists:
                available_cnt += 1
                total_bytes += size
                if expected_sha:
                    status = "verified" if file_sha == expected_sha else "mismatch"
                else:
                    status = "present"
            else:
                missing_cnt += 1
                status = "missing"
        else:
            exists = False
            chk_path = base_dir
            size = 0
            file_sha = None
            status = "missing"
            missing_cnt += 1

        entries.append(
            ModelWeightEntry(
                name=name,
                runtime_profile=profile_path.name,
                checkpoint_relpath=str(checkpoint_rel),
                checkpoint_abspath=str(chk_path),
                exists=exists,
                size_bytes=size,
                sha256=file_sha,
                expected_sha256=expected_sha,
                integrity_status=status,
                modality=modality,
                engine=engine,
            )
        )

    return VendorManifest(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        models_dir=str(base_dir),
        total_profiles=len(entries),
        available_weights=available_cnt,
        missing_weights=missing_cnt,
        total_bytes=total_bytes,
        entries=entries,
    )


def verify_offline_integrity(models_dir: Path | str | None = None) -> dict[str, Any]:
    """Verify SHA-256 checksums of offline models and return status summary."""
    manifest = inspect_model_manifest(models_dir)
    mismatches = [e for e in manifest.entries if e.integrity_status == "mismatch"]
    missing = [e for e in manifest.entries if e.integrity_status == "missing"]

    return {
        "status": "pass" if not mismatches and manifest.available_weights > 0 else "warn",
        "total_profiles": manifest.total_profiles,
        "available_weights": manifest.available_weights,
        "missing_weights": manifest.missing_weights,
        "mismatches": [e.to_json() for e in mismatches],
        "missing": [e.name for e in missing],
        "total_size_mb": round(manifest.total_bytes / (1024 * 1024), 2),
    }


def bundle_offline_weights(
    dest_dir: Path | str,
    models_dir: Path | str | None = None,
    copy_weights: bool = False,
) -> Path:
    """Create an air-gap distribution package with offline_manifest.json."""
    dest = Path(dest_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)

    manifest = inspect_model_manifest(models_dir)
    manifest_data = manifest.to_json()

    manifest_file = dest / "offline_manifest.json"
    manifest_file.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    src_dir = Path(manifest.models_dir)
    # Copy runtime json profiles
    for e in manifest.entries:
        src_profile = src_dir / e.runtime_profile
        if src_profile.is_file():
            shutil.copy2(src_profile, dest / e.runtime_profile)

        if copy_weights and e.exists:
            src_chk = Path(e.checkpoint_abspath)
            if src_chk.is_file():
                shutil.copy2(src_chk, dest / src_chk.name)

    return manifest_file

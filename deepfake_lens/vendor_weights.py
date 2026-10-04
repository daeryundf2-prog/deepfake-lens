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


ENV_MODELS_DIR = "DEEPFAKE_LENS_MODELS_DIR"


def default_models_dir() -> Path:
    """Resolve the effective models directory.

    Precedence: ``$DEEPFAKE_LENS_MODELS_DIR`` → the packaged profile dir
    (``deepfake_lens/models``, present in wheels and the source tree) →
    the legacy repo-root ``models/``. Weight binaries always resolve
    relative to the same directory that owns the profile JSONs.
    """
    import os

    env = os.environ.get(ENV_MODELS_DIR)
    if env:
        return Path(env).resolve()
    packaged = Path(__file__).resolve().parent / "models"
    if any(packaged.glob("*-runtime.json")):
        return packaged
    legacy = Path(__file__).resolve().parent.parent / "models"
    return legacy


def _resolve_models_dir(models_dir: Path | str | None) -> Path:
    return Path(models_dir).resolve() if models_dir is not None else default_models_dir()


def _profile_weight_kind(data: dict[str, Any]) -> tuple[str, str]:
    """Classify a runtime profile's weight requirement.

    Returns ``(kind, checkpoint_relpath)`` where kind is one of:
    ``unsupported`` (profile marked supported:false — not a gap),
    ``local`` (declares a local checkpoint — must be present+verified),
    ``hub`` (resolves weights from a hub at runtime — no local file),
    ``none`` (no weight requirement at all).
    """
    inner = data.get("inner")
    checkpoint = data.get("checkpoint") or data.get("weights") or ""
    if not checkpoint and isinstance(inner, dict):
        checkpoint = inner.get("checkpoint") or inner.get("weights") or ""
    if data.get("supported") is False:
        return "unsupported", str(checkpoint)
    if checkpoint:
        return "local", str(checkpoint)
    if data.get("hub_model") or data.get("model_id") or data.get("hf_model"):
        return "hub", ""
    return "none", ""


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
    if "audio" in n or "wav" in n or "aasist" in n or "w2v2" in n or "wav2vec" in n or "melodymachine" in n:
        return "audio"
    if "text" in n or "roberta" in n or "ppl" in n or "binoculars" in n or "qwen" in n or ("detector" in n and "openai" in n):
        return "text"
    if "video" in n or "frames" in n or "temporal" in n:
        return "video"
    return "image"


def inspect_model_manifest(models_dir: Path | str | None = None) -> VendorManifest:
    """Scan models directory and build an air-gap verification manifest."""
    base_dir = _resolve_models_dir(models_dir)

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
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            entries.append(
                ModelWeightEntry(
                    name=name, runtime_profile=profile_path.name,
                    checkpoint_relpath="", checkpoint_abspath="",
                    exists=False, size_bytes=0, sha256=None, expected_sha256=None,
                    integrity_status=f"profile-unreadable:{type(exc).__name__}",
                    modality="unknown", engine="unknown",
                )
            )
            missing_cnt += 1
            continue

        engine = str(data.get("engine", data.get("runtime", "pytorch")))
        modality = _infer_modality(name, data)
        expected_sha = data.get("sha256") or data.get("expected_sha256")
        kind, checkpoint_rel = _profile_weight_kind(data)

        if kind != "local":
            # Hub-resolved / unsupported / weightless profiles are NOT
            # "missing" — conflating them makes air-gap verify fail forever.
            entries.append(
                ModelWeightEntry(
                    name=name, runtime_profile=profile_path.name,
                    checkpoint_relpath=checkpoint_rel, checkpoint_abspath="",
                    exists=False, size_bytes=0, sha256=None, expected_sha256=None,
                    integrity_status=kind, modality=modality, engine=engine,
                )
            )
            continue

        chk_path = (base_dir / checkpoint_rel).resolve() if not Path(checkpoint_rel).is_absolute() else Path(checkpoint_rel)
        exists = chk_path.is_file()
        try:
            size = chk_path.stat().st_size if exists else 0
        except OSError:
            size = 0
        file_sha: str | None = None
        if exists:
            try:
                file_sha = _compute_file_sha256(chk_path)
            except OSError:
                file_sha = None  # present but unreadable mid-read

        if exists:
            available_cnt += 1
            total_bytes += size
            if file_sha is None:
                status = "unreadable"
            elif expected_sha:
                status = "verified" if file_sha == expected_sha else "mismatch"
            else:
                status = "unverified"
        else:
            missing_cnt += 1
            status = "missing"

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
    """Verify declared SHA-256 checksums; report honest per-class counts.

    Status classes: ``verified`` (hash pinned & matched), ``unverified``
    (present but no declared hash — presence is not verification),
    ``mismatch`` (hash pinned & different — FAIL), ``missing`` (required
    local checkpoint absent — FAIL), ``unreadable``, ``hub`` /
    ``unsupported`` / ``none`` (informational, never counted as gaps).
    """
    manifest = inspect_model_manifest(models_dir)
    by_status: dict[str, list[str]] = {}
    for e in manifest.entries:
        key = e.integrity_status.split(":", 1)[0]
        by_status.setdefault(key, []).append(e.name)
    mismatches = by_status.get("mismatch", [])
    missing = by_status.get("missing", [])
    unreadable = by_status.get("unreadable", [])
    unverified = by_status.get("unverified", [])
    profile_errs = by_status.get("profile-unreadable", [])

    if mismatches or missing or unreadable or profile_errs:
        status = "fail"
    elif manifest.available_weights == 0 and manifest.total_profiles:
        status = "fail"  # nothing usable at all
    else:
        status = "pass" if not unverified else "pass-unverified"

    return {
        "status": status,
        "total_profiles": manifest.total_profiles,
        "local_checkpoints": sum(len(v) for k, v in by_status.items() if k in ("verified", "unverified", "mismatch", "missing", "unreadable")),
        "verified": len(by_status.get("verified", [])),
        "unverified": unverified,
        "mismatches": [e.to_json() for e in manifest.entries if e.integrity_status == "mismatch"],
        "missing": missing,
        "unreadable": unreadable,
        "hub_resolved": by_status.get("hub", []),
        "unsupported": by_status.get("unsupported", []),
        "profile_errors": profile_errs,
        "total_size_mb": round(manifest.total_bytes / (1024 * 1024), 2),
        "models_dir": manifest.models_dir,
        # Back-compat aliases for earlier consumers of this payload.
        "available_weights": manifest.available_weights,
        "missing_weights": manifest.missing_weights,
    }


def weights_coverage(models_dir: Path | str | None = None) -> dict[str, int]:
    """Cheap exists/missing count across runtime profiles — no hashing.

    Only ``local`` profiles (declared checkpoints) count toward coverage;
    hub-resolved and unsupported profiles are informational, not gaps.
    """
    base_dir = _resolve_models_dir(models_dir)
    total = present = 0
    if not base_dir.is_dir():
        return {"weights_total": 0, "weights_available": 0, "weights_missing": 0, "weights_hub": 0, "weights_unsupported": 0}
    hub = unsupported = 0
    for profile_path in sorted(base_dir.glob("*-runtime.json")):
        try:
            data = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        kind, checkpoint_rel = _profile_weight_kind(data)
        if kind == "hub":
            hub += 1
            continue
        if kind in ("unsupported", "none"):
            unsupported += 1
            continue
        total += 1
        chk = (base_dir / checkpoint_rel) if not Path(checkpoint_rel).is_absolute() else Path(checkpoint_rel)
        if chk.is_file():
            present += 1
    return {
        "weights_total": total,
        "weights_available": present,
        "weights_missing": total - present,
        "weights_hub": hub,
        "weights_unsupported": unsupported,
    }


def fetch_weights(
    models_dir: Path | str | None = None,
    *,
    offline: bool = False,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Download profile checkpoints from their declared URLs and verify SHA-256.

    Each ``*-runtime.json`` may carry ``checkpoint_url`` plus ``sha256``.
    Downloads stream to a temp file inside the models dir and are renamed
    only after the declared hash matches — a failed fetch never leaves a
    partial or poisoned checkpoint. ``offline=True`` refuses outright:
    air-gapped hosts must never open a socket.

    Per-profile results land in ``results`` with status
    fetched / already-present / unsupported / failed.
    """
    if offline:
        return {"status": "skipped", "reason": "offline mode: network fetch refused", "results": [], "fetched": [], "failed": []}

    import os
    import tempfile
    import urllib.request

    base_dir = _resolve_models_dir(models_dir)
    results: list[dict[str, str]] = []
    fetched: list[str] = []
    failed: list[dict[str, str]] = []
    for profile_path in sorted(base_dir.glob("*-runtime.json")):
        name = profile_path.stem.replace("-runtime", "")
        try:
            data = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            entry = {"name": name, "status": "failed", "error": f"profile unreadable: {type(exc).__name__}"}
            results.append(entry); failed.append(entry)
            continue
        kind, checkpoint_rel = _profile_weight_kind(data)
        url = data.get("checkpoint_url")
        if kind != "local" or not checkpoint_rel:
            results.append({"name": name, "status": "unsupported", "reason": f"profile kind '{kind}' has no fetchable local checkpoint"})
            continue
        if not url:
            results.append({"name": name, "status": "unsupported", "reason": "no checkpoint_url declared — manual provisioning required"})
            continue

        # Confine the destination inside the models dir — a profile's
        # checkpoint path must not escape via ../ or absolute paths.
        dest = (base_dir / checkpoint_rel).resolve() if not Path(checkpoint_rel).is_absolute() else Path(checkpoint_rel)
        try:
            dest.relative_to(base_dir)
        except ValueError:
            entry = {"name": name, "status": "failed", "error": "checkpoint path escapes models dir"}
            results.append(entry); failed.append(entry)
            continue

        expected = str(data.get("sha256") or data.get("expected_sha256") or "")
        if dest.is_file():
            try:
                if expected and _compute_file_sha256(dest) == expected.lower():
                    results.append({"name": name, "status": "already-present"})
                    continue
            except OSError:
                pass

        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".fetch-", suffix=".part")
        try:
            digest = hashlib.sha256()
            with os.fdopen(fd, "wb") as out_fh, urllib.request.urlopen(str(url), timeout=timeout) as response:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    out_fh.write(chunk)
            actual = digest.hexdigest()
            if expected and actual != expected.lower():
                failed.append({"name": name, "status": "failed", "error": f"sha256 mismatch (expected {expected[:12]}…, got {actual[:12]}…)"})
                results.append(failed[-1])
                continue
            os.replace(tmp_name, dest)
            fetched.append(name)
            results.append({"name": name, "status": "fetched"})
        except (OSError, ValueError) as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            entry = {"name": name, "status": "failed", "error": f"download failed: {exc}"}
            results.append(entry); failed.append(entry)
            continue

    status = "ok" if not failed else "failed"
    return {"status": status, "fetched": fetched, "failed": failed, "results": results}


def bundle_offline_weights(
    dest_dir: Path | str,
    models_dir: Path | str | None = None,
    copy_weights: bool = False,
    *,
    force: bool = False,
) -> Path:
    """Create a deterministic air-gap distribution package.

    Layout (all paths relative — no source-machine absolute paths):
      offline_manifest.json   profile/weight inventory + declared hashes
      *-runtime.json          model profiles
      <checkpoint relpaths>   weight binaries (with --copy-weights)
      NOTICE.md / README.md   provenance & license notes when present

    The destination must be empty (or pass ``force``) so a stale bundle
    can never masquerade as a fresh one.
    """
    dest = Path(dest_dir).resolve()
    if dest.exists() and any(dest.iterdir()) and not force:
        raise SystemExit(f"bundle destination is not empty: {dest} (pass --force to overwrite)")
    dest.mkdir(parents=True, exist_ok=True)

    manifest = inspect_model_manifest(models_dir)
    src_dir = Path(manifest.models_dir)

    # Bundle manifest carries relative paths only — absolute source paths
    # would leak the provisioning machine's filesystem layout.
    manifest_data = manifest.to_json()
    for entry in manifest_data["entries"]:
        entry.pop("checkpoint_abspath", None)
    manifest_data["models_dir"] = "."
    manifest_data["copy_weights"] = bool(copy_weights)

    manifest_file = dest / "offline_manifest.json"
    manifest_file.write_text(json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Copy runtime profiles + provenance docs
    for e in manifest.entries:
        src_profile = src_dir / e.runtime_profile
        if src_profile.is_file():
            shutil.copy2(src_profile, dest / e.runtime_profile)
    for extra in ("NOTICE.md", "README.md"):
        src = src_dir / extra
        if src.is_file():
            shutil.copy2(src, dest / extra)

    missing_required: list[str] = []
    if copy_weights:
        for e in manifest.entries:
            if e.integrity_status not in ("verified", "unverified", "present"):
                if e.checkpoint_relpath:
                    missing_required.append(e.name)
                continue
            src_chk = Path(e.checkpoint_abspath)
            if src_chk.is_file():
                rel = Path(e.checkpoint_relpath)
                dest_chk = dest / src_chk.name if rel.is_absolute() or ".." in rel.parts else dest / rel
                dest_chk.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_chk, dest_chk)

    if copy_weights and missing_required:
        raise SystemExit(
            "incomplete bundle: required weights absent -> " + ", ".join(missing_required)
        )
    return manifest_file


def install_bundle(
    bundle_dir: Path | str,
    target_dir: Path | str,
) -> dict[str, Any]:
    """Install an offline bundle into a models directory on the target host.

    Copies profiles + weights + docs, then re-verifies hashes so a
    corrupted transfer is caught at install time, not at scan time.
    """
    bundle = Path(bundle_dir).resolve()
    target = Path(target_dir).resolve()
    if not (bundle / "offline_manifest.json").is_file():
        return {"status": "failed", "error": f"not a bundle directory: {bundle}"}
    target.mkdir(parents=True, exist_ok=True)
    for src in sorted(bundle.iterdir()):
        if src.is_file():
            shutil.copy2(src, target / src.name)
        elif src.is_dir():
            dst_sub = target / src.name
            if dst_sub.exists():
                shutil.rmtree(dst_sub)
            shutil.copytree(src, dst_sub)
    verify = verify_offline_integrity(target)
    return {
        "status": "installed" if verify["status"] in ("pass", "pass-unverified") else "installed-with-errors",
        "target": str(target),
        "verify": verify,
        "activate": f"export {ENV_MODELS_DIR}={target}",
    }

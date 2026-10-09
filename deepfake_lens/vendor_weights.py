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
from typing import Any, Callable

from .model_pins import (
    PIN_FIELD,
    declared_sha256,
    file_sha256,
    is_commit_sha,
    pin_target,
    required_pin_keys,
)
from .json_text import json_dumps


ENV_MODELS_DIR = "DEEPFAKE_LENS_MODELS_DIR"

# Upper bound on one checkpoint download (G9). The largest checkpoint any
# profile has referenced is AIDE progan_train.pth (~3.3 GB, aide-runtime.json
# limitations); 4 GiB leaves headroom without letting a hostile or
# misconfigured URL fill the disk.
MAX_CHECKPOINT_DOWNLOAD_BYTES = 4 * 1024 ** 3
# Only TLS downloads are accepted: http:// can be rewritten in transit and
# file:// / ftp:// read arbitrary local or unauthenticated sources.
ALLOWED_DOWNLOAD_SCHEME = "https://"


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


# G3 (round 5): Korean labels for ModelWeightEntry.integrity_status — the
# table, the Markdown report and the summary counts all use these, so a row
# and the count it belongs to can never disagree (the old table printed
# "[MISSING]" for disabled/hub profiles while "Missing: 0").
WEIGHT_STATUS_LABELS = {
    "verified": "검증됨(해시 일치)",
    "unverified": "있음(선언 해시 없음 — 미검증)",
    "mismatch": "해시 불일치",
    "missing": "없음",
    "unreadable": "읽기 실패",
    "hub": "허브 모델(로컬 파일 없음)",
    "unsupported": "비활성 프로필(집계 제외)",
    "none": "가중치 불필요",
    "profile-unreadable": "프로필 읽기 실패",
}
MODALITY_LABELS = {"image": "이미지", "audio": "음성", "text": "텍스트", "video": "영상", "unknown": "알 수 없음"}


def weight_status_label(status: str) -> str:
    """Korean label of an integrity status (``profile-unreadable:<Exc>`` keeps the class)."""
    key, _, detail = status.partition(":")
    label = WEIGHT_STATUS_LABELS.get(key, status)
    return f"{label}({detail})" if detail and key in WEIGHT_STATUS_LABELS else label


def weight_status_counts(entries: list["ModelWeightEntry"]) -> list[tuple[str, int]]:
    """(label, count) per status in WEIGHT_STATUS_LABELS order — counted from the rows themselves."""
    counts: dict[str, int] = {}
    for entry in entries:
        key = entry.integrity_status.split(":", 1)[0]
        counts[key] = counts.get(key, 0) + 1
    return [(WEIGHT_STATUS_LABELS[key], counts[key]) for key in WEIGHT_STATUS_LABELS if counts.get(key)]


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

    def to_table(self) -> str:
        """Korean text table for ``vendor-weights`` (G3).

        The status counts are taken from the rows, so a row's label and the
        summary always agree.
        """
        counts = ", ".join(f"{label} {count}개" for label, count in weight_status_counts(self.entries)) or "프로필 없음"
        lines = [
            f"모델 폴더: {self.models_dir}",
            f"런타임 프로필 {self.total_profiles}개 — {counts} · 로컬 가중치 용량 {self.total_bytes / (1024 * 1024):.1f} MB",
        ]
        for e in self.entries:
            lines.append(
                f"  [{weight_status_label(e.integrity_status)}] {e.name} "
                f"({MODALITY_LABELS.get(e.modality, e.modality)}) {e.checkpoint_relpath or '-'}"
            )
        return "\n".join(lines)

    def to_markdown(self) -> str:
        lines = [
            "# 포렌식 폐쇄망 AI 모델 가중치 검증 보고서",
            "",
            f"**검증 일시**: {self.generated_at}",
            f"**모델 디렉터리**: `{self.models_dir}`",
            f"**총 런타임 프로파일**: {self.total_profiles}개",
            f"**탑재 완료 가중치**: {self.available_weights}개 (미탑재: {self.missing_weights}개)",
            f"**상태별 프로필 수**: {', '.join(f'{label} {count}개' for label, count in weight_status_counts(self.entries)) or '없음'}",
            f"**총 가중치 용량**: {self.total_bytes / (1024 * 1024):.1f} MB",
            "",
            "| 모델명 | 모달리티 | 엔진 | 상태 | 용량 (MB) | SHA-256 무결성 해시 |",
            "| :--- | :--- | :--- | :--- | :---: | :--- |",
        ]
        for e in self.entries:
            size_mb = f"{e.size_bytes / (1024 * 1024):.2f}" if e.exists else "-"
            hash_display = f"`{e.sha256}`" if e.sha256 else "없음"
            lines.append(
                f"| **{e.name}** | {MODALITY_LABELS.get(e.modality, e.modality)} | {e.engine} | {weight_status_label(e.integrity_status)} | {size_mb} | {hash_display} |"
            )

        lines.extend([
            "",
            "> **참고**",
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
        expected_sha = declared_sha256(data) or None
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
    max_bytes: int = MAX_CHECKPOINT_DOWNLOAD_BYTES,
) -> dict[str, Any]:
    """Download profile checkpoints from their declared URLs and verify SHA-256.

    Each ``*-runtime.json`` may carry ``checkpoint_url`` plus ``pin.sha256``.
    Downloads stream to a temp file inside the models dir and are renamed
    only after the declared hash matches — a failed fetch never leaves a
    partial or poisoned checkpoint. ``offline=True`` refuses outright:
    air-gapped hosts must never open a socket.

    G9: only ``https://`` URLs are fetched, and a download larger than
    ``max_bytes`` is aborted. A profile without a declared ``pin.sha256``
    is still downloaded but recorded as ``unverified`` — never ``fetched``
    — and the adapter keeps refusing to load it until the operator pins it
    (``deepfake-lens vendor-weights pin``).

    Per-profile results land in ``results`` with status
    fetched / unverified / already-present / unsupported / failed.
    """
    if offline:
        return {"status": "skipped", "reason": "오프라인 모드: 네트워크 다운로드를 거부했습니다", "results": [], "fetched": [], "unverified": [], "failed": []}

    import os
    import tempfile
    import urllib.request

    base_dir = _resolve_models_dir(models_dir)
    results: list[dict[str, str]] = []
    fetched: list[str] = []
    unverified: list[str] = []
    failed: list[dict[str, str]] = []
    for profile_path in sorted(base_dir.glob("*-runtime.json")):
        name = profile_path.stem.replace("-runtime", "")
        try:
            data = json.loads(profile_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            entry = {"name": name, "status": "failed", "error": f"프로필을 읽을 수 없습니다: {type(exc).__name__}"}
            results.append(entry); failed.append(entry)
            continue
        kind, checkpoint_rel = _profile_weight_kind(data)
        url = data.get("checkpoint_url")
        if kind != "local" or not checkpoint_rel:
            results.append({"name": name, "status": "unsupported", "reason": f"프로필 종류 '{kind}'에는 내려받을 로컬 체크포인트가 없습니다"})
            continue
        if not url:
            results.append({"name": name, "status": "unsupported", "reason": "checkpoint_url이 선언되지 않았습니다 — 수동으로 배치해야 합니다"})
            continue
        if not str(url).lower().startswith(ALLOWED_DOWNLOAD_SCHEME):
            entry = {"name": name, "status": "failed", "error": "checkpoint_url은 https://여야 합니다(http/file/ftp 거부)"}
            results.append(entry); failed.append(entry)
            continue

        # Confine the destination inside the models dir — a profile's
        # checkpoint path must not escape via ../ or absolute paths.
        dest = (base_dir / checkpoint_rel).resolve() if not Path(checkpoint_rel).is_absolute() else Path(checkpoint_rel)
        try:
            dest.relative_to(base_dir)
        except ValueError:
            entry = {"name": name, "status": "failed", "error": "체크포인트 경로가 모델 디렉터리를 벗어납니다"}
            results.append(entry); failed.append(entry)
            continue

        expected = declared_sha256(data)
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
            received = 0
            with os.fdopen(fd, "wb") as out_fh, urllib.request.urlopen(str(url), timeout=timeout) as response:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > max_bytes:
                        raise ValueError(f"다운로드가 크기 상한({max_bytes}바이트)을 넘었습니다")
                    digest.update(chunk)
                    out_fh.write(chunk)
            actual = digest.hexdigest()
            if expected and actual != expected.lower():
                os.unlink(tmp_name)
                failed.append({"name": name, "status": "failed", "error": f"sha256 불일치(기대값 {expected[:12]}…, 실제 {actual[:12]}…)"})
                results.append(failed[-1])
                continue
            os.replace(tmp_name, dest)
            if expected:
                fetched.append(name)
                results.append({"name": name, "status": "fetched", "sha256": actual})
            else:
                # Presence is not verification: the bytes are on disk, but
                # nothing vouches for them until the operator pins them.
                unverified.append(name)
                results.append({"name": name, "status": "unverified", "sha256": actual, "reason": "pin.sha256 미선언 — 검증 없이 저장됨"})
        except (OSError, ValueError) as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            entry = {"name": name, "status": "failed", "error": f"다운로드 실패: {exc}"}
            results.append(entry); failed.append(entry)
            continue

    status = "failed" if failed else ("unverified" if unverified else "ok")
    return {"status": status, "fetched": fetched, "unverified": unverified, "failed": failed, "results": results}


class ProfileNotFoundError(FileNotFoundError):
    """Z2: the profile argument is neither a file nor a profile name in the models dir (CLI: exit 2)."""


def resolve_profile_path(profile: Path | str, models_dir: Path | str | None = None) -> Path:
    """Resolve a profile argument: an existing path, or a name in the models dir.

    ``aasist``, ``aasist-runtime`` and ``aasist-runtime.json`` all name
    ``<models_dir>/aasist-runtime.json``.
    """
    candidate = Path(profile)
    if candidate.is_file():
        return candidate.resolve()
    base_dir = _resolve_models_dir(models_dir)
    stem = candidate.name
    for name in (stem, f"{stem}.json", f"{stem}-runtime.json"):
        path = base_dir / name
        if path.is_file():
            return path.resolve()
    raise ProfileNotFoundError(
        f"파일을 찾을 수 없습니다: 「{profile}」 — 프로필 파일 경로도, 모델 디렉터리 「{base_dir}」의 프로필 이름도 아닙니다"
    )


def _hub_commit_sha(model_id: str) -> str:
    """Current commit of a Hugging Face hub model (network, lazy import)."""
    from huggingface_hub import HfApi  # lazy: optional, online-only dependency

    info = HfApi().model_info(model_id)
    sha = str(getattr(info, "sha", "") or "").lower()
    if not is_commit_sha(sha):
        raise RuntimeError(f"허브가 {model_id}의 커밋 sha를 돌려주지 않았습니다")
    return sha


# Profile field naming the hub model each revision key pins.
_REVISION_SOURCES = {"revision": ("hub_model", "backbone"), "observer_revision": ("observer_model",)}


def pin_profile(
    profile: Path | str,
    models_dir: Path | str | None = None,
    *,
    revision: str | None = None,
    hub_resolver: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Write the profile's ``pin`` from the weights it names (G9).

    - ``sha256``: computed from the local checkpoint the profile (or its
      ``inner`` profile for video-frames) points at; the file must exist.
    - ``revision`` / ``observer_revision``: the hub model's current commit,
      resolved with ``huggingface_hub`` (online — only on this explicit
      command), or taken from ``revision`` when given.

    Returns ``{"status": "pinned", "profile", "pin"}``, or
    ``{"status": "needs-manual", "instructions"}`` when a hub commit cannot
    be resolved (``huggingface_hub`` missing). The profile file is rewritten
    atomically; nothing is written unless every required key resolved.
    """
    import os
    import tempfile

    path = resolve_profile_path(profile, models_dir)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"profile is not a JSON object: {path}")
    keys = required_pin_keys(data)
    if not keys:
        raise ValueError(f"profile loads no weights (runtime {data.get('runtime')!r}) — nothing to pin: {path}")
    target = pin_target(data)
    pin: dict[str, str] = {}
    for key in keys:
        if key == "sha256":
            checkpoint_rel = str(target.get("checkpoint") or target.get("path") or "")
            if not checkpoint_rel:
                raise ValueError(f"프로필에 체크포인트가 선언되어 있지 않습니다: {path}")
            checkpoint = Path(checkpoint_rel)
            if not checkpoint.is_absolute():
                checkpoint = path.parent / checkpoint
            if not checkpoint.is_file():
                raise FileNotFoundError(f"체크포인트를 찾을 수 없습니다: {checkpoint}")
            pin["sha256"] = file_sha256(checkpoint)
            continue
        model_id = next((str(target.get(field)) for field in _REVISION_SOURCES[key] if target.get(field)), "")
        if not model_id:
            raise ValueError(f"프로필에 pin.{key}에 쓸 허브 모델 id가 없습니다: {path}")
        if revision and key == "revision":
            value = revision.strip().lower()
            if not is_commit_sha(value):
                raise ValueError("--revision은 40자리 16진수 커밋 sha여야 합니다(브랜치나 태그는 고정값이 아닙니다)")
            pin[key] = value
            continue
        resolver = hub_resolver or _hub_commit_sha
        try:
            pin[key] = resolver(model_id)
        except ImportError:
            return {
                "status": "needs-manual",
                "profile": str(path),
                "instructions": (
                    f"huggingface_hub가 설치되어 있지 않아 {model_id}의 커밋을 조회할 수 없습니다. "
                    "`pip install huggingface_hub` 후 다시 실행하거나, 허브 페이지에서 커밋 SHA(40자리)를 확인해 "
                    f"'deepfake-lens vendor-weights pin {path.name} --revision <커밋SHA>'로 지정하세요."
                ),
            }
    data[PIN_FIELD] = {**{k: "" for k in keys}, **pin}
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".pin-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json_dumps(data, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return {"status": "pinned", "profile": str(path), "pin": data[PIN_FIELD]}


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
        raise SystemExit(f"번들 대상 폴더가 비어 있지 않습니다: {dest} (덮어쓰려면 --force)")
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
    manifest_file.write_text(json_dumps(manifest_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
            "불완전한 번들: 필요한 가중치가 없습니다 -> " + ", ".join(missing_required)
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
        return {"status": "failed", "error": f"번들 디렉터리가 아닙니다: {bundle}"}
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

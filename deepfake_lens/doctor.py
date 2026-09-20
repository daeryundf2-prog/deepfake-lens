"""`deepfake-lens doctor` — environment and model-integrity diagnostics.

Checks, in one pass:
  1. every committed runtime profile under models/ — whether its checkpoint
     file exists, its size, and sha256 when the profile pins one
  2. hardware acceleration visibility (torch CUDA, onnxruntime providers,
     DirectML)
  3. optional dependency imports (transformers, librosa, speechbrain, …)
  4. external tool availability (ffmpeg/ffprobe)

Output is a table by default or JSON via --format json. Exit code is 0 even
when checks report missing pieces — doctor reports state, it does not gate.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# (import name, pip package, what it enables)
OPTIONAL_DEPS = [
    ("torch", "torch", "AASIST / torchvision / SBI runtimes"),
    ("torchvision", "torchvision", "torchvision runtime (CNNDetection, SBI-EffNet)"),
    ("transformers", "transformers", "HF image/audio/text classifiers"),
    ("onnxruntime", "onnxruntime", "ONNX runtime + mobile export path"),
    ("cv2", "opencv-python", "face detection, video frames, image forensics"),
    ("PIL", "Pillow", "image decoding"),
    ("numpy", "numpy", "all signal processing"),
    ("librosa", "librosa", "audio waveform decode + heuristics"),
    ("soundfile", "soundfile", "lossless audio decode"),
    ("speechbrain", "speechbrain", "ECAPA-TDNN speaker verification"),
    ("mediapipe", "mediapipe", "FaceMesh face detection fallback"),
    ("c2pa", "c2pa-python", "C2PA manifest verification"),
    ("syhwp", "syhwp", "HWP/HWPX document parsing"),
    ("fitz", "PyMuPDF", "PDF rendering forensics"),
]

EXTERNAL_TOOLS = ["ffmpeg", "ffprobe"]


@dataclass
class Check:
    name: str
    status: str  # ok | missing | warn
    detail: str


@dataclass
class DoctorReport:
    profiles: list[Check] = field(default_factory=list)
    accelerators: list[Check] = field(default_factory=list)
    dependencies: list[Check] = field(default_factory=list)
    tools: list[Check] = field(default_factory=list)

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_profile(profile_path: Path) -> Check:
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Check(profile_path.name, "warn", f"profile JSON unreadable: {exc}")
    if profile.get("supported") is False:
        return Check(profile_path.name, "warn", "supported:false — measured-rejected placeholder, skipped at runtime")
    name = str(profile.get("name") or profile_path.stem)
    runtime = str(profile.get("runtime") or "")
    if profile.get("hub_model") or runtime in {"hf-text-classifier", "hf-image-classifier", "hf-audio-classifier", "causal-lm-ppl", "binoculars"}:
        return Check(name, "ok", f"{runtime}: hub-resolved ({profile.get('hub_model', 'n/a')}), downloads on first use")
    checkpoint = profile.get("checkpoint") or profile.get("path")
    if runtime == "video-frames" and isinstance(profile.get("inner"), dict):
        checkpoint = profile["inner"].get("checkpoint")
    if not checkpoint:
        return Check(name, "warn", f"{runtime}: no checkpoint/hub_model field")
    ckpt_path = Path(str(checkpoint))
    if not ckpt_path.is_absolute():
        ckpt_path = profile_path.parent / ckpt_path
    if not ckpt_path.is_file():
        return Check(name, "missing", f"checkpoint absent: {ckpt_path.name}")
    size_mb = ckpt_path.stat().st_size / (1024 * 1024)
    expected = str(profile.get("sha256") or "").lower()
    if expected:
        actual = _sha256_file(ckpt_path)
        if actual != expected:
            return Check(name, "warn", f"checkpoint sha256 mismatch (expected {expected[:12]}…, got {actual[:12]}…)")
        return Check(name, "ok", f"{ckpt_path.name} {size_mb:.1f} MB, sha256 verified")
    return Check(name, "ok", f"{ckpt_path.name} {size_mb:.1f} MB (no pinned sha256)")


def _check_accelerators() -> list[Check]:
    checks: list[Check] = []
    try:
        torch = importlib.import_module("torch")
        cuda = bool(torch.cuda.is_available())
        detail = f"torch {torch.__version__}, CUDA {'available' if cuda else 'not available'}"
        if cuda:
            detail += f" ({torch.cuda.get_device_name(0)})"
        checks.append(Check("torch", "ok" if cuda else "warn", detail))
        dml = getattr(torch, "directml", None) or importlib.util.find_spec("torch_directml")
        checks.append(Check("directml", "ok" if dml else "warn", "DirectML " + ("detected" if dml else "not installed")))
    except ImportError:
        checks.append(Check("torch", "missing", "torch not installed — neural runtimes disabled"))
    try:
        ort = importlib.import_module("onnxruntime")
        providers = ", ".join(ort.get_available_providers())
        checks.append(Check("onnxruntime", "ok", f"providers: {providers}"))
    except ImportError:
        checks.append(Check("onnxruntime", "missing", "onnxruntime not installed"))
    return checks


def run_diagnostics(models_dir: Path | None = None) -> DoctorReport:
    report = DoctorReport()
    root = models_dir or MODELS_DIR
    if root.is_dir():
        for profile_path in sorted(root.glob("*.json")):
            report.profiles.append(_check_profile(profile_path))
    else:
        report.profiles.append(Check(str(root), "warn", "models directory not found"))
    report.accelerators = _check_accelerators()
    for import_name, package, purpose in OPTIONAL_DEPS:
        try:
            module = importlib.import_module(import_name)
            version = getattr(module, "__version__", "?")
            report.dependencies.append(Check(package, "ok", f"v{version} — {purpose}"))
        except ImportError:
            report.dependencies.append(Check(package, "missing", f"not installed — {purpose}"))
    for tool in EXTERNAL_TOOLS:
        found = shutil.which(tool)
        report.tools.append(Check(tool, "ok" if found else "missing", found or "not on PATH"))
    return report


def format_report(report: DoctorReport) -> str:
    lines: list[str] = []
    icon = {"ok": " OK  ", "missing": " MISS", "warn": " WARN"}
    for section, checks in (
        ("Model profiles", report.profiles),
        ("Accelerators", report.accelerators),
        ("Dependencies", report.dependencies),
        ("External tools", report.tools),
    ):
        lines.append(f"\n== {section} ==")
        for check in checks:
            lines.append(f"[{icon.get(check.status, '????')}] {check.name}: {check.detail}")
    missing = sum(1 for c in (*report.profiles, *report.dependencies, *report.tools) if c.status == "missing")
    warned = sum(1 for c in (*report.profiles, *report.accelerators, *report.dependencies) if c.status == "warn")
    lines.append(f"\nsummary: {missing} missing, {warned} warnings")
    return "\n".join(lines)

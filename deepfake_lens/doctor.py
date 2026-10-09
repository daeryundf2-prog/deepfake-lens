"""`deepfake-lens doctor` — environment and model-integrity diagnostics.

Checks, in one pass:
  1. every runtime profile under the models dir, in three columns (G29):
     - pin: the profile's ``pin`` object carries every key its runtime needs
       (sha256 for a local checkpoint, a 40-hex commit for a hub model)
     - runtime dependencies: the runtime's Python modules actually import
       (torch/transformers for hub classifiers, onnxruntime for onnx, …)
     - checkpoint: the local weight file exists and its sha256 matches the
       pin (hub runtimes have no local file; they are pinned by revision)
     A profile is "실행 가능" only when it is ``supported`` (measurement gate)
     and all three columns are OK — the same conditions under which a scan
     reports its ``model:<name>`` coverage entry as ``ran``.
  2. decision thresholds (measured / provisional / in-sample)
  3. hardware acceleration visibility (torch CUDA, onnxruntime providers,
     DirectML)
  4. optional dependency imports (transformers, librosa, speechbrain, …)
  5. external tool availability (ffmpeg/ffprobe)

Output is a table by default or JSON via --format json. Exit code is 0 even
when checks report missing pieces — doctor reports state, it does not gate.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .model_pins import (
    HUB_ONLY_RUNTIMES,
    LOCAL_AND_HUB_RUNTIMES,
    LOCAL_RUNTIMES,
    PIN_FIELD,
    declared_revision,
    declared_sha256,
    file_sha256,
    is_commit_sha,
    is_sha256,
    pin_target,
    required_pin_keys,
)
from .vendor_weights import default_models_dir

# (import name, pip package, what it enables)
OPTIONAL_DEPS = [
    ("torch", "torch", "AASIST / torchvision / SBI 런타임"),
    ("torchvision", "torchvision", "torchvision 런타임(SBI-EffNet)"),
    ("transformers", "transformers", "HF 이미지/오디오/텍스트 분류기"),
    ("onnxruntime", "onnxruntime", "ONNX 런타임 + 모바일 내보내기 경로"),
    ("cv2", "opencv-python", "얼굴 검출, 영상 프레임, 이미지 포렌식"),
    ("PIL", "Pillow", "이미지 디코딩"),
    ("numpy", "numpy", "모든 신호 처리"),
    ("librosa", "librosa", "오디오 파형 디코딩 + 휴리스틱"),
    ("soundfile", "soundfile", "무손실 오디오 디코딩"),
    ("speechbrain", "speechbrain", "ECAPA-TDNN 화자 검증"),
    ("mediapipe", "mediapipe", "FaceMesh 얼굴 검출(대체 경로)"),
    ("c2pa", "c2pa-python", "C2PA 매니페스트 검증"),
    ("syhwp", "syhwp", "HWP/HWPX 문서 해석"),
    ("fitz", "PyMuPDF", "PDF 렌더링 포렌식·PDF 증거설명서"),
    ("fastapi", "fastapi", "통합 비동기 REST API 서버"),
    ("uvicorn", "uvicorn", "통합 API 서비스용 ASGI 서버"),
]

EXTERNAL_TOOLS = ["ffmpeg", "ffprobe"]

# Modules each runtime imports before it can score a file — mirrors the
# importlib.import_module calls in model_runtimes.py (and the install hints
# in model_runtimes._runtime_install_hint). A runtime is "의존성 OK" only when
# every module here actually imports.
RUNTIME_MODULES: dict[str, tuple[str, ...]] = {
    "aide": ("torch", "torchvision", "timm", "PIL", "numpy"),
    "aasist": ("torch", "numpy"),
    "clip-linear": ("torch", "transformers", "PIL"),
    "hf-text-classifier": ("torch", "transformers"),
    "hf-image-classifier": ("torch", "transformers", "PIL"),
    "hf-audio-classifier": ("torch", "transformers", "numpy"),
    "causal-lm-ppl": ("torch", "transformers"),
    "binoculars": ("torch", "transformers"),
    "torchvision": ("torch", "torchvision", "PIL", "numpy"),
    "torchscript": ("torch", "PIL", "numpy"),
    "onnx": ("onnxruntime", "PIL", "numpy"),
    "onnx-audio": ("onnxruntime", "numpy"),
    # video-frames: cv2 for frame sampling, plus the inner runtime's modules.
    "video-frames": ("cv2",),
}

# Profiles that score from data carried in the profile itself (no weights).
_WEIGHTLESS_TYPES = {"score-sidecar-v1", "deepfake-lens-portable-threshold-v1"}

# Column states.
OK = "ok"
MISSING = "missing"
MISMATCH = "mismatch"
NOT_APPLICABLE = "n/a"


@dataclass
class Check:
    name: str
    status: str  # ok | missing | warn
    detail: str


@dataclass
class ProfileStatus:
    """One runtime profile's three-column readiness (G29)."""

    name: str
    file: str
    runtime: str
    supported: bool
    pin: str          # ok | missing | n/a
    pin_detail: str
    runtime_deps: str  # ok | missing | n/a
    runtime_deps_detail: str
    checkpoint: str   # ok | missing | mismatch | n/a
    checkpoint_detail: str

    @property
    def runnable(self) -> bool:
        return self.supported and all(
            state in (OK, NOT_APPLICABLE) for state in (self.pin, self.runtime_deps, self.checkpoint)
        )

    def to_json(self) -> dict[str, object]:
        payload = asdict(self)
        payload["runnable"] = self.runnable
        return payload

    def summary_check(self) -> Check:
        """Legacy one-line Check (``profiles`` section) derived from the columns."""
        if not self.supported:
            return Check(self.name, "warn", "supported:false — 측정 게이트 미충족, 검사에서 건너뜀")
        columns = f"pin {self.pin_detail} | 의존성 {self.runtime_deps_detail} | 체크포인트 {self.checkpoint_detail}"
        if self.runnable:
            return Check(self.name, "ok", columns)
        if MISSING in (self.checkpoint, self.runtime_deps):
            return Check(self.name, "missing", columns)
        return Check(self.name, "warn", columns)


@dataclass
class DoctorReport:
    profiles: list[Check] = field(default_factory=list)
    accelerators: list[Check] = field(default_factory=list)
    dependencies: list[Check] = field(default_factory=list)
    tools: list[Check] = field(default_factory=list)
    model_profiles: list[ProfileStatus] = field(default_factory=list)

    @property
    def runnable_profiles(self) -> list[str]:
        return [status.name for status in self.model_profiles if status.runnable]

    def to_json(self) -> dict[str, object]:
        return {
            "profiles": [asdict(check) for check in self.profiles],
            "accelerators": [asdict(check) for check in self.accelerators],
            "dependencies": [asdict(check) for check in self.dependencies],
            "tools": [asdict(check) for check in self.tools],
            "model_profiles": [status.to_json() for status in self.model_profiles],
            "runnable": {
                "count": len(self.runnable_profiles),
                "total": len(self.model_profiles),
                "names": self.runnable_profiles,
            },
        }


def _check_thresholds(root: Path) -> Check:
    """Report whether decision thresholds are measured, provisional or in-sample.

    No thresholds.json means every scan ran on the builtin heuristic
    literals — honest deployments should know that. A present-but-
    provisional profile (n < MIN_CALIBRATION_SAMPLES) is better than
    defaults but still unvalidated; an in-sample fit (G28) is reference only.
    """
    from .calibration import load_threshold_profile, threshold_display_label

    path = root / "thresholds.json"
    if not path.is_file():
        return Check(
            "thresholds.json",
            "warn",
            "없음 — 검사는 측정되지 않은 내장 휴리스틱 임계값을 씁니다 "
            "(라벨 코퍼스에서 experiments/eval_seam_thresholds.py로 맞추십시오)",
        )
    profile = load_threshold_profile(path)
    if profile is None:
        return Check("thresholds.json", "warn", "읽을 수 없거나 스키마 버전이 맞지 않습니다")
    label = threshold_display_label(profile)
    if profile.provisional:
        return Check(
            "thresholds.json",
            "warn",
            f"{label} — 잠정값: {profile.provisional_reason}; 검증되지 않은 임계값",
        )
    fp = profile.dataset_fingerprint[:16]
    detail = f"{label} — 측정 표본 n={profile.samples}"
    if fp:
        detail += f", 코퍼스 지문 {fp}"
    if profile.in_sample:
        # G28: fitted and evaluated on the same rows — not evidence-grade.
        detail += " — 적합에 쓴 같은 표본에서 평가된 값이라 감정 근거가 아닌 참고값입니다"
        return Check("thresholds.json", "warn", detail)
    return Check("thresholds.json", "ok", detail)


def _module_importable(name: str, cache: dict[str, str | None]) -> str | None:
    """None when ``name`` imports; otherwise a short failure reason."""
    if name not in cache:
        try:
            importlib.import_module(name)
            cache[name] = None
        except ImportError as exc:
            cache[name] = f"{name} 미설치" if getattr(exc, "name", None) in (name, None) else f"{name}: {exc}"
        except Exception as exc:  # noqa: BLE001 - a broken install (CUDA libs, ABI) is "not importable"
            cache[name] = f"{name}: {type(exc).__name__}"
    return cache[name]


def _weight_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """The profile whose weights load (video-frames: inner + the outer pin)."""
    inner = pin_target(profile)
    target = dict(inner)
    if inner is not profile and PIN_FIELD not in target and isinstance(profile.get(PIN_FIELD), Mapping):
        target[PIN_FIELD] = profile[PIN_FIELD]
    return target


def _pin_column(target: Mapping[str, Any]) -> tuple[str, str]:
    keys = required_pin_keys(target)
    if not keys:
        return NOT_APPLICABLE, "불필요(가중치 없음)"
    if not isinstance(target.get(PIN_FIELD), Mapping):
        return MISSING, "없음 (pin 객체 없음)"
    problems: list[str] = []
    for key in keys:
        if key == "sha256":
            value = declared_sha256(target)
            if not is_sha256(value):
                problems.append("pin.sha256 " + ("비어 있음" if not value else "형식 오류"))
        else:
            value = declared_revision(target, key)
            if not is_commit_sha(value):
                problems.append(f"pin.{key} " + ("비어 있음" if not value else "커밋 SHA 아님"))
    if problems:
        return MISSING, "없음 (" + ", ".join(problems) + ")"
    return OK, "있음"


def _deps_column(runtime: str, inner_runtime: str | None, cache: dict[str, str | None]) -> tuple[str, str]:
    if not runtime:
        return NOT_APPLICABLE, "불필요"
    modules = list(RUNTIME_MODULES.get(runtime, ()))
    if runtime not in RUNTIME_MODULES:
        return MISSING, f"알 수 없는 런타임 '{runtime}'"
    if runtime == "video-frames":
        if not inner_runtime or inner_runtime not in RUNTIME_MODULES:
            return MISSING, f"inner 런타임 미지정/알 수 없음 ({inner_runtime or '없음'})"
        modules += [m for m in RUNTIME_MODULES[inner_runtime] if m not in modules]
    failures = [reason for name in modules if (reason := _module_importable(name, cache))]
    if failures:
        return MISSING, "import 불가: " + ", ".join(failures)
    return OK, "import 가능 (" + ", ".join(modules) + ")"


def _checkpoint_column(target: Mapping[str, Any], base_dir: Path) -> tuple[str, str]:
    runtime = str(target.get("runtime") or "").lower()
    hub = str(target.get("hub_model") or "")
    if runtime in HUB_ONLY_RUNTIMES:
        revision = declared_revision(target)
        rev = f"@{revision[:12]}" if revision else ""
        return NOT_APPLICABLE, f"허브 모델 {hub or 'n/a'}{rev} — 로컬 파일 없음, 첫 실행 시 네트워크 필요 (폐쇄망은 사전 캐시)"
    if runtime not in LOCAL_RUNTIMES and runtime not in LOCAL_AND_HUB_RUNTIMES:
        return NOT_APPLICABLE, "불필요"
    raw = str(target.get("checkpoint") or target.get("path") or "")
    if not raw:
        return MISSING, "checkpoint 필드 없음"
    path = Path(raw)
    if not path.is_absolute():
        path = base_dir / path
    if not path.is_file():
        return MISSING, f"없음: {path.name}"
    size_mb = path.stat().st_size / (1024 * 1024)
    expected = declared_sha256(target)
    if not is_sha256(expected):
        return MISMATCH, f"{path.name} {size_mb:.1f} MB — 해시 미고정(일치 확인 불가)"
    try:
        actual = file_sha256(path)
    except OSError as exc:
        return MISMATCH, f"{path.name} 해시 계산 실패: {type(exc).__name__}"
    if actual != expected:
        return MISMATCH, f"sha256 불일치 (기대 {expected[:12]}…, 실제 {actual[:12]}…)"
    return OK, f"{path.name} {size_mb:.1f} MB, sha256 일치"


def profile_status(profile_path: Path, *, import_cache: dict[str, str | None] | None = None) -> ProfileStatus:
    """Three-column readiness of one runtime profile."""
    cache = import_cache if import_cache is not None else {}
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        reason = f"프로필 JSON 읽기 실패: {type(exc).__name__}"
        return ProfileStatus(profile_path.stem, profile_path.name, "", True, MISSING, reason, MISSING, reason, MISSING, reason)
    if not isinstance(profile, dict):
        reason = "프로필이 JSON 객체가 아님"
        return ProfileStatus(profile_path.stem, profile_path.name, "", True, MISSING, reason, MISSING, reason, MISSING, reason)
    name = str(profile.get("name") or profile.get("model") or profile_path.stem)
    runtime = str(profile.get("runtime") or "").lower()
    supported = profile.get("supported") is not False
    target = _weight_profile(profile)
    inner_runtime = str(target.get("runtime") or "").lower() if runtime == "video-frames" else None
    if not runtime and not (isinstance(profile.get("score_map"), dict) or profile.get("type") in _WEIGHTLESS_TYPES):
        reason = "runtime/score_map 없음 — 실행할 수 있는 검출기가 아님"
        return ProfileStatus(name, profile_path.name, runtime, supported, NOT_APPLICABLE, "불필요", MISSING, reason, NOT_APPLICABLE, "불필요")
    pin_state, pin_detail = _pin_column(target)
    deps_state, deps_detail = _deps_column(runtime, inner_runtime, cache)
    ckpt_state, ckpt_detail = _checkpoint_column(target, profile_path.parent)
    return ProfileStatus(
        name=name,
        file=profile_path.name,
        runtime=runtime or "score-map",
        supported=supported,
        pin=pin_state,
        pin_detail=pin_detail,
        runtime_deps=deps_state,
        runtime_deps_detail=deps_detail,
        checkpoint=ckpt_state,
        checkpoint_detail=ckpt_detail,
    )


def _check_profile(profile_path: Path) -> Check:
    """One-line verdict for a profile (``ok`` only when actually runnable)."""
    return profile_status(profile_path).summary_check()


def _check_accelerators() -> list[Check]:
    checks: list[Check] = []
    try:
        torch = importlib.import_module("torch")
        cuda = bool(torch.cuda.is_available())
        mps = bool(hasattr(torch, "backends") and hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
        detail = f"torch {torch.__version__}, CUDA {'사용 가능' if cuda else '사용 불가'}, MPS {'사용 가능' if mps else '사용 불가'}"
        if cuda:
            detail += f" ({torch.cuda.get_device_name(0)})"
        elif mps:
            detail += " (Apple Silicon GPU)"
        checks.append(Check("torch", "ok" if (cuda or mps) else "warn", detail))
        dml = getattr(torch, "directml", None) or importlib.util.find_spec("torch_directml")
        checks.append(Check("directml", "ok" if dml else "warn", "DirectML " + ("감지됨" if dml else "설치되지 않음")))
    except ImportError:
        checks.append(Check("torch", "missing", "torch 미설치 — 신경망 런타임 비활성"))
    try:
        ort = importlib.import_module("onnxruntime")
        providers = ", ".join(ort.get_available_providers())
        checks.append(Check("onnxruntime", "ok", f"providers: {providers}"))
    except ImportError:
        checks.append(Check("onnxruntime", "missing", "onnxruntime 미설치"))
    return checks


def run_diagnostics(models_dir: Path | None = None) -> DoctorReport:
    report = DoctorReport()
    root = Path(models_dir) if models_dir is not None else default_models_dir()
    import_cache: dict[str, str | None] = {}
    if root.is_dir():
        # Same glob as analysis_api.default_engine_profiles: doctor reports
        # on exactly the engine set a default scan runs.
        for profile_path in sorted(root.glob("*-runtime.json")):
            status = profile_status(profile_path, import_cache=import_cache)
            report.model_profiles.append(status)
            report.profiles.append(status.summary_check())
    else:
        report.profiles.append(Check(str(root), "warn", "모델 디렉터리를 찾을 수 없습니다"))
    report.profiles.append(_check_thresholds(root))
    report.accelerators = _check_accelerators()
    for import_name, package, purpose in OPTIONAL_DEPS:
        try:
            module = importlib.import_module(import_name)
            version = getattr(module, "__version__", "?")
            report.dependencies.append(Check(package, "ok", f"v{version} — {purpose}"))
        except ImportError:
            report.dependencies.append(Check(package, "missing", f"미설치 — {purpose}"))
    for tool in EXTERNAL_TOOLS:
        found = shutil.which(tool)
        report.tools.append(Check(tool, "ok" if found else "missing", found or "PATH에 없음"))
    return report


_COLUMN_MARK = {OK: "OK", NOT_APPLICABLE: "n/a", MISSING: "MISS", MISMATCH: "불일치"}


def format_report(report: DoctorReport) -> str:
    lines: list[str] = []
    icon = {"ok": " OK  ", "missing": " MISS", "warn": " WARN"}
    runnable = report.runnable_profiles
    total = len(report.model_profiles)
    lines.append(
        f"실행 가능: {len(runnable)}/{total} 프로필 — supported:true 이고 "
        "pin·런타임 의존성·체크포인트(해시 일치) 세 열이 모두 OK인 것만 셉니다"
    )
    if not runnable:
        lines.append(
            "!! 실행 가능한 모델 프로필 없음 — 검사 결과는 결정적 근거(메타데이터·C2PA)와 참고 신호뿐이며 "
            "모델 검사는 coverage에 skipped/failed로 기록됩니다 !!"
        )
    lines.append("\n== Model profiles ==")
    lines.append(f"{'':7} {'프로필':<34} {'pin':<6} {'의존성':<6} {'체크포인트':<8} 실행 가능")
    for status in report.model_profiles:
        check = status.summary_check()
        lines.append(
            f"[{icon.get(check.status, '????')}] {status.name[:34]:<34} "
            f"{_COLUMN_MARK.get(status.pin, status.pin):<6} "
            f"{_COLUMN_MARK.get(status.runtime_deps, status.runtime_deps):<6} "
            f"{_COLUMN_MARK.get(status.checkpoint, status.checkpoint):<8} "
            + ("예" if status.runnable else ("아니오 (supported:false)" if not status.supported else "아니오"))
        )
        lines.append(f"         pin: {status.pin_detail}")
        lines.append(f"         의존성: {status.runtime_deps_detail}")
        lines.append(f"         체크포인트: {status.checkpoint_detail}")
    for check in report.profiles:
        if check.name == "thresholds.json" or not report.model_profiles:
            lines.append(f"[{icon.get(check.status, '????')}] {check.name}: {check.detail}")
    for section, checks in (
        ("Accelerators", report.accelerators),
        ("Dependencies", report.dependencies),
        ("External tools", report.tools),
    ):
        lines.append(f"\n== {section} ==")
        for check in checks:
            lines.append(f"[{icon.get(check.status, '????')}] {check.name}: {check.detail}")
    missing = sum(1 for c in (*report.profiles, *report.dependencies, *report.tools) if c.status == "missing")
    warned = sum(1 for c in (*report.profiles, *report.accelerators, *report.dependencies) if c.status == "warn")
    lines.append(f"\nsummary: 실행 가능 {len(runnable)}/{total}, {missing} missing, {warned} warnings")
    return "\n".join(lines)

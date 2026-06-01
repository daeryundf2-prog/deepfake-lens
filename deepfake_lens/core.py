from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable

from .model_adapter import ExternalModelAnalysis, analyze_external_model
from .pixel import DEFAULT_PIXEL_MAX_SIDE, PixelAnalysis, analyze_image_pixels
from .pixel import PixelExpertResult
from .png import read_png_dimensions, read_png_metadata


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md"}
DEFAULT_MAX_FILES = 1000
DEFAULT_TEXT_BYTES = 64 * 1024
DEFAULT_METADATA_BYTES = 4 * 1024 * 1024


class RiskBand(str, Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


RISK_LABELS = {
    RiskBand.UNKNOWN: "판단 어려움",
    RiskBand.LOW: "낮음",
    RiskBand.MEDIUM: "주의",
    RiskBand.HIGH: "높음",
}


class SourceConfidence(str, Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


SOURCE_CONFIDENCE_LABELS = {
    SourceConfidence.UNKNOWN: "알 수 없음",
    SourceConfidence.LOW: "낮음",
    SourceConfidence.MEDIUM: "중간",
    SourceConfidence.HIGH: "높음",
}


@dataclass(frozen=True)
class SourceGuess:
    label: str
    confidence: SourceConfidence
    reasons: list[str] = field(default_factory=list)

    @classmethod
    def unknown(cls, reason: str = "출처를 판단할 메타데이터나 명시적 단서가 없습니다.") -> "SourceGuess":
        return cls("출처 단서 없음", SourceConfidence.UNKNOWN, [reason])


@dataclass(frozen=True)
class EvidenceSignal:
    title: str
    detail: str
    weight: int


@dataclass(frozen=True)
class ClassificationResult:
    score: int
    band: RiskBand
    band_label: str
    verdict: str
    signals: list[EvidenceSignal]
    limitations: list[str]
    source_guess: SourceGuess
    next_checks: list[str]
    pixel_analysis: PixelAnalysis | None = None
    model_analysis: ExternalModelAnalysis | None = None
    ai_score: int = 0
    source_attribution_label: str = ""

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        data["band"] = self.band.value
        data["source_guess"]["confidence"] = self.source_guess.confidence.value
        return data


@dataclass(frozen=True)
class ScanItem:
    path: str
    name: str
    kind: str
    status: str
    size_bytes: int
    result: ClassificationResult | None = None
    error: str | None = None
    duplicate_of: str | None = None

    def to_json(self) -> dict[str, object]:
        data = asdict(self)
        data["result"] = self.result.to_json() if self.result else None
        return data


@dataclass(frozen=True)
class BatchScanSummary:
    total: int
    analyzed: int
    high: int
    medium: int
    unknown: int
    low: int
    unsupported_or_failed: int
    capped: bool
    cached: int = 0
    duplicates: int = 0
    skipped: int = 0

    def to_json(self) -> dict[str, object]:
        return asdict(self)


AI_IDENTITY_PHRASES = [
    "as an ai",
    "language model",
    "i cannot browse",
    "ai assistant",
    "인공지능으로서",
    "언어 모델",
    "제가 직접 경험할 수는",
    "실시간으로 확인할 수는",
]

SYNTHETIC_WRITING_PHRASES = [
    "결론적으로",
    "요약하자면",
    "다음과 같습니다",
    "중요한 것은",
    "다양한 관점",
    "균형 잡힌",
    "전반적으로",
    "필수적입니다",
    "도움이 됩니다",
    "it is important to note",
    "in conclusion",
    "overall",
    "from multiple perspectives",
    "balanced approach",
]

PERSONAL_ANCHORS = [
    "나",
    "저",
    "우리",
    "오늘",
    "어제",
    "내일",
    "엄마",
    "아빠",
    "친구",
    "학교",
    "회사",
    "집",
    "i",
    "me",
    "my",
    "we",
    "today",
    "yesterday",
    "tomorrow",
]


def scan_directory(
    directory: Path | str,
    *,
    recursive: bool = False,
    max_files: int = DEFAULT_MAX_FILES,
    text_bytes: int = DEFAULT_TEXT_BYTES,
    metadata_bytes: int = DEFAULT_METADATA_BYTES,
    pixel_mode: str = "off",
    pixel_max_side: int = DEFAULT_PIXEL_MAX_SIDE,
    heatmaps: bool = False,
    heatmap_dir: Path | None = None,
    model_path: Path | None = None,
    cache_path: Path | None = None,
    workers: int = 1,
    max_file_bytes: int | None = None,
    allow_symlinks: bool = False,
    dedupe: bool = False,
    hash_db_path: Path | None = None,
) -> tuple[BatchScanSummary, list[ScanItem]]:
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    paths: list[Path] = []
    capped = False
    for path in _iter_files(root, recursive=recursive, allow_symlinks=allow_symlinks):
        if len(paths) >= max_files:
            capped = True
            break
        paths.append(path)

    duplicates = _duplicate_map(paths, root=root, max_file_bytes=max_file_bytes, hash_db_path=hash_db_path) if dedupe or hash_db_path else {}
    cache = _load_scan_cache(cache_path)
    cache_items = cache.setdefault("items", {}) if cache is not None else {}

    def analyze_one(path: Path) -> tuple[ScanItem, str | None, bool]:
        if path in duplicates:
            display_path = _display_path(path, root=root)
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            return ScanItem(display_path, path.name, "duplicate", "duplicate", size, error="duplicate content", duplicate_of=duplicates[path]), None, False
        if max_file_bytes is not None:
            try:
                size = path.stat().st_size
            except OSError as exc:
                return ScanItem(_display_path(path, root=root), path.name, "unknown", "failed", 0, error=str(exc)), None, False
            if size > max_file_bytes:
                return ScanItem(_display_path(path, root=root), path.name, "unknown", "skipped", size, error=f"file exceeds --max-file-bytes ({max_file_bytes})"), None, False
        key = _cache_key(
            path,
            root=root,
            text_bytes=text_bytes,
            metadata_bytes=metadata_bytes,
            pixel_mode=pixel_mode,
            pixel_max_side=pixel_max_side,
            heatmaps=heatmaps,
            model_path=model_path,
        )
        cached = cache_items.get(key) if isinstance(cache_items, dict) else None
        if isinstance(cached, dict):
            return _scan_item_from_json(cached), key, True
        item = analyze_file(
            path,
            root=root,
            text_bytes=text_bytes,
            metadata_bytes=metadata_bytes,
            pixel_mode=pixel_mode,
            pixel_max_side=pixel_max_side,
            heatmaps=heatmaps,
            heatmap_dir=heatmap_dir,
            model_path=model_path,
        )
        return item, key, False

    if workers > 1 and len(paths) > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            analyzed = list(executor.map(analyze_one, paths))
    else:
        analyzed = [analyze_one(path) for path in paths]

    cached_count = sum(1 for _, _, was_cached in analyzed if was_cached)
    items = [item for item, _, _ in analyzed]
    if cache is not None:
        for item, key, _ in analyzed:
            if key:
                cache_items[key] = item.to_json()
        _write_scan_cache(cache_path, cache)

    sorted_items = sort_items(items)
    return summarize(sorted_items, capped=capped, cached=cached_count), sorted_items


def analyze_file(
    path: Path | str,
    *,
    root: Path | None = None,
    text_bytes: int = DEFAULT_TEXT_BYTES,
    metadata_bytes: int = DEFAULT_METADATA_BYTES,
    pixel_mode: str = "off",
    pixel_max_side: int = DEFAULT_PIXEL_MAX_SIDE,
    heatmaps: bool = False,
    heatmap_dir: Path | None = None,
    model_path: Path | None = None,
) -> ScanItem:
    file_path = Path(path)
    display_path = _display_path(file_path, root=root)
    try:
        size = file_path.stat().st_size
    except OSError as exc:
        return ScanItem(display_path, file_path.name, "unknown", "failed", 0, error=str(exc))
    extension = file_path.suffix.lower()

    if extension in SUPPORTED_TEXT_EXTENSIONS:
        try:
            text = _read_prefix(file_path, text_bytes).decode("utf-8", errors="replace")
            return ScanItem(display_path, file_path.name, "text", "analyzed", size, analyze_text(text))
        except OSError as exc:
            return ScanItem(display_path, file_path.name, "text", "failed", size, error=str(exc))

    if extension in SUPPORTED_IMAGE_EXTENSIONS:
        try:
            metadata, dimensions = read_image_metadata(file_path, metadata_bytes=metadata_bytes)
            pixel_analysis = None
            if pixel_mode != "off":
                pixel_analysis = analyze_image_pixels(
                    file_path,
                    mode=pixel_mode,
                    max_side=pixel_max_side,
                    heatmap_path=_heatmap_path_for(file_path, root=root, heatmap_dir=heatmap_dir) if heatmaps else None,
                )
            model_analysis = analyze_external_model(file_path, model_path)
            result = analyze_image_metadata(metadata, dimensions=dimensions, pixel_analysis=pixel_analysis, model_analysis=model_analysis)
            return ScanItem(display_path, file_path.name, "image", "analyzed", size, result)
        except OSError as exc:
            return ScanItem(display_path, file_path.name, "image", "failed", size, error=str(exc))

    return ScanItem(display_path, file_path.name, "unsupported", "unsupported", size, error="지원 형식이 아닙니다.")


def analyze_text(text: str) -> ClassificationResult:
    trimmed = text.strip()
    if not trimmed:
        return ClassificationResult(
            score=0,
            band=RiskBand.UNKNOWN,
            band_label=RISK_LABELS[RiskBand.UNKNOWN],
            verdict="글에서 판단할 단서가 부족합니다.",
            signals=[],
            limitations=["분석할 원문이 비어 있습니다."],
            source_guess=SourceGuess.unknown(),
            next_checks=["분석할 원문을 더 길게 확보하세요."],
        )

    normalized = re.sub(r"\s+", " ", trimmed.lower())
    lines = [line.strip() for line in trimmed.splitlines() if line.strip()]
    sentences = [part.strip() for part in re.split(r"[.!?。！？\n]+", trimmed) if len(part.strip()) >= 8]
    words = re.findall(r"[\w']+", normalized, flags=re.UNICODE)
    signals: list[EvidenceSignal] = []

    identity_hits = sum(1 for phrase in AI_IDENTITY_PHRASES if phrase in normalized)
    if identity_hits:
        signals.append(EvidenceSignal("AI 자기표현 문구", f"AI 또는 언어 모델임을 암시하는 표현이 {identity_hits}개 발견되었습니다.", 35))

    phrase_hits = sum(1 for phrase in SYNTHETIC_WRITING_PHRASES if phrase in normalized)
    if phrase_hits >= 4:
        signals.append(EvidenceSignal("템플릿형 문장 전개", "요약/균형/결론형 연결 문구가 반복됩니다.", 22))
    elif phrase_hits >= 2:
        signals.append(EvidenceSignal("정형화된 연결 문구", f"자동 생성 글에서 자주 보이는 연결 표현이 {phrase_hits}개 보입니다.", 12))

    list_markers = sum(1 for line in lines if re.match(r"^(\d+[\).]|[-*•])\s+.+", line))
    if list_markers >= 6:
        signals.append(EvidenceSignal("과도하게 균일한 목록 구조", f"목록 항목이 {list_markers}개 이어집니다.", 18))
    elif list_markers >= 3:
        signals.append(EvidenceSignal("목록 중심 구성", "번호/불릿 구조가 두드러집니다.", 10))

    sentence_signal = _sentence_uniformity_signal(sentences)
    if sentence_signal:
        signals.append(sentence_signal)
    repeat_signal = _repeated_shingle_signal(words)
    if repeat_signal:
        signals.append(repeat_signal)
    generic_signal = _generic_text_signal(normalized, words)
    if generic_signal:
        signals.append(generic_signal)

    source_guess = guess_text_source(normalized, identity_hits)
    limitations = ["휴리스틱 기반 선별 결과이며 진위 판단이 아니라 검토 우선순위입니다."]
    if len(trimmed) < 240:
        limitations.append("짧은 글은 문체 통계가 불안정합니다.")
    if len(sentences) < 4:
        limitations.append("문장 수가 적어 반복도와 문장 길이 신호가 제한적입니다.")

    return _build_result(
        signals,
        subject="글",
        source_guess=source_guess,
        limitations=limitations,
        force_unknown=len(trimmed) < 24 and not signals and source_guess.confidence == SourceConfidence.UNKNOWN,
    )


def analyze_image_metadata(
    metadata: dict[str, str],
    *,
    dimensions: tuple[int, int] | None = None,
    pixel_analysis: PixelAnalysis | None = None,
    model_analysis: ExternalModelAnalysis | None = None,
) -> ClassificationResult:
    signals: list[EvidenceSignal] = []
    source_guess = guess_image_source(metadata)
    if source_guess.confidence in {SourceConfidence.MEDIUM, SourceConfidence.HIGH}:
        signals.append(
            EvidenceSignal(
                "생성 도구 메타데이터",
                source_guess.reasons[0] if source_guess.reasons else "생성 도구 단서가 발견되었습니다.",
                67 if source_guess.confidence == SourceConfidence.HIGH else 36,
            )
        )

    if dimensions:
        width, height = dimensions
        if width == height and width >= 512 and width % 64 == 0:
            signals.append(EvidenceSignal("생성 모델에 흔한 정사각 해상도", f"{width}x{height} 해상도는 생성 이미지 워크플로에서 자주 쓰입니다.", 9))

    pixel_signal = _pixel_evidence_signal(pixel_analysis)
    if pixel_signal:
        signals.append(pixel_signal)
    model_signal = _model_evidence_signal(model_analysis)
    if model_signal:
        signals.append(model_signal)

    limitations = ["기본 분석은 메타데이터와 파일 헤더 중심의 빠른 선별 도구입니다."]
    if not metadata:
        limitations.append("메타데이터가 없거나 읽지 못했습니다. 이는 사람이 만든 파일이라는 뜻이 아닙니다.")
    if not dimensions:
        limitations.append("이미지 크기를 파일 헤더에서 확인하지 못했습니다.")
    if pixel_analysis:
        limitations.extend(pixel_analysis.limitations)
    if model_analysis:
        limitations.extend(model_analysis.limitations)

    return _build_result(
        signals,
        subject="사진",
        source_guess=source_guess,
        limitations=limitations,
        force_unknown=not metadata and not dimensions and not (pixel_analysis and pixel_analysis.available),
        pixel_analysis=pixel_analysis,
        model_analysis=model_analysis,
    )


def read_image_metadata(path: Path, *, metadata_bytes: int = DEFAULT_METADATA_BYTES) -> tuple[dict[str, str], tuple[int, int] | None]:
    data = _read_prefix(path, metadata_bytes)
    metadata: dict[str, str] = {}
    dimensions: tuple[int, int] | None = None

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        metadata.update(read_png_metadata(data))
        dimensions = read_png_dimensions(data)
    elif data.startswith(b"\xff\xd8"):
        dimensions = _read_jpeg_dimensions(data)
    elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        dimensions = _read_webp_dimensions(data)

    header_text = _extract_header_text(data)
    if header_text:
        metadata["header.text"] = header_text
    return metadata, dimensions


def _heatmap_path_for(path: Path, *, root: Path | None, heatmap_dir: Path | None) -> Path:
    output_root = heatmap_dir or path.parent / "deepfake_lens_heatmaps"
    try:
        relative = path.relative_to(root) if root else Path(path.name)
    except ValueError:
        relative = Path(path.name)
    safe_parts = [part.replace("/", "_").replace("\\", "_") for part in relative.parts]
    output_name = "__".join(safe_parts) + ".heatmap.png"
    return output_root / output_name


def guess_image_source(metadata: dict[str, str]) -> SourceGuess:
    blob = "\n".join(f"{key}: {value}" for key, value in metadata.items())
    normalized = blob.lower()
    if not normalized.strip():
        return SourceGuess.unknown()

    if _looks_like_comfyui(normalized):
        return SourceGuess("ComfyUI 추정", SourceConfidence.HIGH, ["ComfyUI workflow/prompt 구조가 발견되었습니다."])
    if _looks_like_a1111(normalized):
        return SourceGuess("Stable Diffusion / A1111 추정", SourceConfidence.HIGH, ["프롬프트, steps, sampler, CFG, seed 같은 A1111 생성 파라미터가 발견되었습니다."])

    direct = _direct_tool_guess(normalized)
    if direct:
        return direct

    if _contains_generation_fields(normalized):
        return SourceGuess("AI 생성 메타데이터 추정", SourceConfidence.MEDIUM, ["prompt/model/seed/CFG 계열 필드가 발견되었습니다."])
    return SourceGuess.unknown()


def guess_text_source(normalized_text: str, ai_identity_hits: int) -> SourceGuess:
    if "chatgpt" in normalized_text or "openai" in normalized_text:
        return SourceGuess("ChatGPT/OpenAI 단서 있음", SourceConfidence.MEDIUM, ["원문에 ChatGPT 또는 OpenAI가 직접 언급되었습니다."])
    if "claude" in normalized_text or "anthropic" in normalized_text:
        return SourceGuess("Claude 단서 있음", SourceConfidence.MEDIUM, ["원문에 Claude 또는 Anthropic이 직접 언급되었습니다."])
    if "gemini" in normalized_text or "bard" in normalized_text:
        return SourceGuess("Gemini/Bard 단서 있음", SourceConfidence.MEDIUM, ["원문에 Gemini 또는 Bard가 직접 언급되었습니다."])
    if ai_identity_hits:
        return SourceGuess("AI 어시스턴트 문체 추정", SourceConfidence.MEDIUM, ["AI 또는 언어 모델임을 직접 암시하는 문구가 있습니다."])
    return SourceGuess.unknown()


def sort_items(items: list[ScanItem]) -> list[ScanItem]:
    return sorted(items, key=lambda item: (_sort_bucket(item), -(item.result.score if item.result else -1), item.path.lower()))


def summarize(items: list[ScanItem], *, capped: bool, cached: int = 0) -> BatchScanSummary:
    analyzed = [item for item in items if item.status == "analyzed" and item.result]
    return BatchScanSummary(
        total=len(items),
        analyzed=len(analyzed),
        high=sum(1 for item in analyzed if item.result and item.result.band == RiskBand.HIGH),
        medium=sum(1 for item in analyzed if item.result and item.result.band == RiskBand.MEDIUM),
        unknown=sum(1 for item in analyzed if item.result and item.result.band == RiskBand.UNKNOWN),
        low=sum(1 for item in analyzed if item.result and item.result.band == RiskBand.LOW),
        unsupported_or_failed=sum(1 for item in items if item.status not in {"analyzed", "duplicate", "skipped"}),
        capped=capped,
        cached=cached,
        duplicates=sum(1 for item in items if item.status == "duplicate"),
        skipped=sum(1 for item in items if item.status == "skipped"),
    )


def scan_to_json(summary: BatchScanSummary, items: list[ScanItem]) -> dict[str, object]:
    return {"summary": summary.to_json(), "items": [item.to_json() for item in items]}


def scan_to_json_text(summary: BatchScanSummary, items: list[ScanItem]) -> str:
    return json.dumps(scan_to_json(summary, items), ensure_ascii=False, indent=2)


def _iter_files(root: Path, *, recursive: bool, allow_symlinks: bool = False) -> Iterable[Path]:
    iterator = root.rglob("*") if recursive else root.iterdir()
    for path in iterator:
        if path.is_symlink() and not allow_symlinks:
            continue
        if path.is_file():
            if path.name.endswith((".ivy.json", ".model.json")):
                continue
            yield path


def _read_prefix(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        return handle.read(max(0, limit))


def _display_path(path: Path, *, root: Path | None) -> str:
    try:
        return str(path.relative_to(root)) if root else str(path)
    except ValueError:
        return str(path)


def _duplicate_map(paths: list[Path], *, root: Path, max_file_bytes: int | None, hash_db_path: Path | None) -> dict[Path, str]:
    hash_db = _load_hash_db(hash_db_path)
    seen = hash_db.setdefault("hashes", {}) if hash_db is not None else {}
    if not isinstance(seen, dict):
        seen = {}
        if hash_db is not None:
            hash_db["hashes"] = seen
    duplicates: dict[Path, str] = {}
    for path in paths:
        if max_file_bytes is not None:
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
        fingerprint = _file_fingerprint(path)
        if not fingerprint:
            continue
        display_path = _display_path(path, root=root)
        if fingerprint in seen:
            duplicates[path] = str(seen[fingerprint])
        else:
            seen[fingerprint] = display_path
    if hash_db is not None:
        _write_hash_db(hash_db_path, hash_db)
    return duplicates


def _file_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _load_scan_cache(cache_path: Path | None) -> dict[str, object] | None:
    if cache_path is None:
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "items": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "items": {}}
    payload.setdefault("version", 1)
    payload.setdefault("items", {})
    return payload


def _write_scan_cache(cache_path: Path | None, cache: dict[str, object]) -> None:
    if cache_path is None:
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_hash_db(hash_db_path: Path | None) -> dict[str, object] | None:
    if hash_db_path is None:
        return None
    try:
        payload = json.loads(hash_db_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "hashes": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "hashes": {}}
    payload.setdefault("version", 1)
    payload.setdefault("hashes", {})
    return payload


def _write_hash_db(hash_db_path: Path | None, hash_db: dict[str, object]) -> None:
    if hash_db_path is None:
        return
    hash_db_path.parent.mkdir(parents=True, exist_ok=True)
    hash_db_path.write_text(json.dumps(hash_db, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _cache_key(
    path: Path,
    *,
    root: Path,
    text_bytes: int,
    metadata_bytes: int,
    pixel_mode: str,
    pixel_max_side: int,
    heatmaps: bool,
    model_path: Path | None,
) -> str:
    try:
        stat = path.stat()
        relative = str(path.relative_to(root))
    except OSError:
        return str(path)
    model_marker = str(model_path.resolve()) if model_path else ""
    return "|".join(
        [
            relative,
            str(stat.st_size),
            str(int(stat.st_mtime_ns)),
            str(text_bytes),
            str(metadata_bytes),
            pixel_mode,
            str(pixel_max_side),
            str(bool(heatmaps)),
            model_marker,
        ]
    )


def _scan_item_from_json(data: dict[str, object]) -> ScanItem:
    result_data = data.get("result")
    result = _classification_result_from_json(result_data) if isinstance(result_data, dict) else None
    return ScanItem(
        str(data.get("path", "")),
        str(data.get("name", "")),
        str(data.get("kind", "unknown")),
        str(data.get("status", "failed")),
        int(data.get("size_bytes", 0) or 0),
        result,
        str(data.get("error")) if data.get("error") is not None else None,
        str(data.get("duplicate_of")) if data.get("duplicate_of") is not None else None,
    )


def _classification_result_from_json(data: dict[str, object]) -> ClassificationResult:
    source_data = data.get("source_guess") if isinstance(data.get("source_guess"), dict) else {}
    source_guess = SourceGuess(
        str(source_data.get("label", "출처 단서 없음")),
        SourceConfidence(str(source_data.get("confidence", SourceConfidence.UNKNOWN.value))),
        [str(item) for item in source_data.get("reasons", [])] if isinstance(source_data.get("reasons"), list) else [],
    )
    pixel_data = data.get("pixel_analysis") if isinstance(data.get("pixel_analysis"), dict) else None
    model_data = data.get("model_analysis") if isinstance(data.get("model_analysis"), dict) else None
    score = int(data.get("score", 0) or 0)
    return ClassificationResult(
        score=score,
        band=RiskBand(str(data.get("band", RiskBand.UNKNOWN.value))),
        band_label=str(data.get("band_label", RISK_LABELS[RiskBand.UNKNOWN])),
        verdict=str(data.get("verdict", "")),
        signals=[EvidenceSignal(str(item.get("title", "")), str(item.get("detail", "")), int(item.get("weight", 0) or 0)) for item in data.get("signals", []) if isinstance(item, dict)],
        limitations=[str(item) for item in data.get("limitations", [])] if isinstance(data.get("limitations"), list) else [],
        source_guess=source_guess,
        next_checks=[str(item) for item in data.get("next_checks", [])] if isinstance(data.get("next_checks"), list) else [],
        pixel_analysis=_pixel_analysis_from_json(pixel_data) if pixel_data else None,
        model_analysis=_model_analysis_from_json(model_data) if model_data else None,
        ai_score=int(data.get("ai_score", score) or score),
        source_attribution_label=str(data.get("source_attribution_label", source_guess.label)),
    )


def _pixel_analysis_from_json(data: dict[str, object]) -> PixelAnalysis:
    experts = [
        PixelExpertResult(
            name=str(item.get("name", "")),
            family=str(item.get("family", "")),
            score=int(item.get("score", 0) or 0),
            weight=float(item.get("weight", 0.0) or 0.0),
            available=bool(item.get("available", False)),
            detail=str(item.get("detail", "")),
            reference=str(item.get("reference", "")),
            implementation=str(item.get("implementation", "local")),
        )
        for item in data.get("experts", [])
        if isinstance(item, dict)
    ]
    return PixelAnalysis(
        mode=str(data.get("mode", "off")),
        available=bool(data.get("available", False)),
        score=int(data.get("score", 0) or 0),
        confidence=str(data.get("confidence", "unknown")),
        model=str(data.get("model", "")),
        experts=experts,
        signals=[str(item) for item in data.get("signals", [])] if isinstance(data.get("signals"), list) else [],
        limitations=[str(item) for item in data.get("limitations", [])] if isinstance(data.get("limitations"), list) else [],
        fusion=str(data.get("fusion", "weighted_mean")),
        evidence_chain=[str(item) for item in data.get("evidence_chain", [])] if isinstance(data.get("evidence_chain"), list) else [],
        implemented_references=[str(item) for item in data.get("implemented_references", [])] if isinstance(data.get("implemented_references"), list) else [],
        heatmap_path=str(data.get("heatmap_path")) if data.get("heatmap_path") else None,
    )


def _model_analysis_from_json(data: dict[str, object]) -> ExternalModelAnalysis:
    return ExternalModelAnalysis(
        available=bool(data.get("available", False)),
        score=int(data.get("score", 0) or 0),
        confidence=str(data.get("confidence", "unknown")),
        model=str(data.get("model", "")),
        detail=str(data.get("detail", "")),
        limitations=[str(item) for item in data.get("limitations", [])] if isinstance(data.get("limitations"), list) else [],
    )


def _build_result(
    signals: list[EvidenceSignal],
    *,
    subject: str,
    source_guess: SourceGuess,
    limitations: list[str],
    force_unknown: bool = False,
    pixel_analysis: PixelAnalysis | None = None,
    model_analysis: ExternalModelAnalysis | None = None,
) -> ClassificationResult:
    sorted_signals = sorted(signals, key=lambda signal: signal.weight, reverse=True)
    score = min(100, sum(signal.weight for signal in sorted_signals))
    if force_unknown:
        band = RiskBand.UNKNOWN
    elif score >= 67:
        band = RiskBand.HIGH
    elif score >= 35:
        band = RiskBand.MEDIUM
    else:
        band = RiskBand.LOW

    verdict = {
        RiskBand.UNKNOWN: f"{subject}에서 판단할 단서가 부족합니다.",
        RiskBand.HIGH: f"{subject}에서 의심 신호가 강합니다.",
        RiskBand.MEDIUM: f"{subject}에서 몇 가지 의심 신호가 보여 추가 확인이 필요합니다.",
        RiskBand.LOW: f"{subject}에서 뚜렷한 의심 신호는 적습니다.",
    }[band]
    next_checks = (
        ["원본 파일을 확보해 메타데이터를 확인하세요.", "역이미지 검색이나 원본 촬영본을 비교하세요.", "게시 계정의 반복 패턴과 업로드 맥락을 함께 보세요."]
        if subject == "사진"
        else ["작성자의 초안이나 편집 이력을 확인하세요.", "짧은 문단보다 전체 글의 맥락을 함께 보세요.", "특정 AI 도구명이 직접 언급되었는지 확인하세요."]
    )
    return ClassificationResult(
        score,
        band,
        RISK_LABELS[band],
        verdict,
        sorted_signals,
        limitations,
        source_guess,
        next_checks,
        pixel_analysis,
        model_analysis,
        score,
        source_guess.label,
    )


def _pixel_evidence_signal(pixel_analysis: PixelAnalysis | None) -> EvidenceSignal | None:
    if not pixel_analysis or not pixel_analysis.available:
        return None
    if pixel_analysis.score >= 82:
        weight = 61
        title = "픽셀 앙상블 강한 의심"
    elif pixel_analysis.score >= 68:
        weight = 47
        title = "픽셀 앙상블 의심"
    elif pixel_analysis.score >= 48:
        weight = 35
        title = "픽셀 앙상블 약한 의심"
    elif pixel_analysis.score >= 32:
        weight = 18
        title = "픽셀 통계 확인 필요"
    else:
        return None

    top_details = pixel_analysis.signals[:2] or ["일부 픽셀 전문가 모델에서 약한 이상 신호가 있습니다."]
    detail = f"{pixel_analysis.model} score={pixel_analysis.score}, confidence={pixel_analysis.confidence}. " + " / ".join(top_details)
    return EvidenceSignal(title, detail, weight)


def _model_evidence_signal(model_analysis: ExternalModelAnalysis | None) -> EvidenceSignal | None:
    if not model_analysis or not model_analysis.available:
        return None
    if model_analysis.score >= 82:
        weight = 67
        title = "외부 모델 강한 의심"
    elif model_analysis.score >= 65:
        weight = 42
        title = "외부 모델 의심"
    elif model_analysis.score >= 45:
        weight = 24
        title = "외부 모델 약한 의심"
    else:
        return None
    return EvidenceSignal(title, f"{model_analysis.model}: {model_analysis.detail}", weight)


def _sentence_uniformity_signal(sentences: list[str]) -> EvidenceSignal | None:
    if len(sentences) < 5:
        return None
    lengths = [max(1, len(re.findall(r"[\w']+", sentence, flags=re.UNICODE))) for sentence in sentences]
    average = sum(lengths) / len(lengths)
    variance = sum((length - average) ** 2 for length in lengths) / len(lengths)
    coefficient = (variance**0.5) / max(1.0, average)
    if average >= 18.0 and coefficient < 0.28:
        return EvidenceSignal("문장 길이 균일성", "여러 문장이 비슷한 길이로 이어집니다.", 15)
    if average >= 14.0 and coefficient < 0.38:
        return EvidenceSignal("낮은 문장 변주", "문장 길이 변화가 작습니다.", 9)
    return None


def _repeated_shingle_signal(words: list[str]) -> EvidenceSignal | None:
    if len(words) < 80:
        return None
    shingles = [" ".join(words[index : index + 3]) for index in range(len(words) - 2)]
    repeated = len(shingles) - len(set(shingles))
    ratio = repeated / max(1, len(shingles))
    if ratio >= 0.1:
        return EvidenceSignal("반복 어구", f"3단어 구문 반복률이 {int(ratio * 100)}% 입니다.", 16)
    if ratio >= 0.055:
        return EvidenceSignal("약한 반복 패턴", "비슷한 구문이 여러 번 재사용됩니다.", 8)
    return None


def _generic_text_signal(normalized: str, words: list[str]) -> EvidenceSignal | None:
    if len(words) < 70:
        return None
    has_number_or_date = re.search(r"\d{1,4}([./:-]\d{1,2})?", normalized) is not None
    personal_anchor_count = sum(1 for anchor in PERSONAL_ANCHORS if re.search(rf"\b{re.escape(anchor)}\b", normalized))
    if not has_number_or_date and personal_anchor_count == 0:
        return EvidenceSignal("개인 맥락 부족", "긴 글인데 날짜, 수치, 구체적 경험 단서가 거의 없습니다.", 8)
    return None


def _direct_tool_guess(normalized: str) -> SourceGuess | None:
    rules = [
        ("Midjourney/Niji 추정", ["midjourney", "niji"], "Midjourney/Niji 단서가 메타데이터에 있습니다."),
        ("Flux / Black Forest Labs 추정", ["flux", "black forest labs", "bfl"], "Flux 또는 Black Forest Labs 단서가 메타데이터에 있습니다."),
        ("Stable Diffusion 추정", ["stable diffusion", "stablediffusion", "automatic1111", "a1111", "sd-webui"], "Stable Diffusion 계열 단서가 메타데이터에 있습니다."),
        ("DALL-E/OpenAI 추정", ["dall-e", "dalle", "openai", "chatgpt"], "DALL-E/OpenAI 단서가 메타데이터에 있습니다."),
        ("Google Imagen/Gemini 추정", ["imagen", "gemini", "google ai studio", "nano banana"], "Google Imagen/Gemini 계열 단서가 메타데이터에 있습니다."),
        ("Adobe Firefly 추정", ["adobe firefly", "firefly"], "Adobe Firefly 단서가 메타데이터에 있습니다."),
        ("Ideogram 추정", ["ideogram"], "Ideogram 단서가 메타데이터에 있습니다."),
        ("Runway 추정", ["runway"], "Runway 단서가 메타데이터에 있습니다."),
        ("Leonardo.ai 추정", ["leonardo.ai", "leonardo ai"], "Leonardo.ai 단서가 메타데이터에 있습니다."),
        ("NovelAI 추정", ["novelai", "novel ai"], "NovelAI 단서가 메타데이터에 있습니다."),
        ("Recraft 추정", ["recraft"], "Recraft 단서가 메타데이터에 있습니다."),
        ("Canva AI 추정", ["canva ai", "magic media"], "Canva AI/Magic Media 단서가 메타데이터에 있습니다."),
        ("Grok/xAI 추정", ["grok", "xai"], "Grok/xAI 단서가 메타데이터에 있습니다."),
    ]
    for label, markers, reason in rules:
        if any(re.search(rf"(?:^|\s){re.escape(marker)}(?:\s|$)", normalized) for marker in markers):
            return SourceGuess(label, SourceConfidence.HIGH, [reason])
    return None


def _looks_like_a1111(normalized: str) -> bool:
    has_prompt_block = "negative prompt" in normalized or "png.parameters" in normalized
    hits = sum(1 for marker in ["steps:", "sampler:", "cfg scale", "seed:", "model hash", "model:"] if marker in normalized)
    return has_prompt_block and hits >= 2


def _looks_like_comfyui(normalized: str) -> bool:
    has_workflow = "png.workflow" in normalized or "png.prompt" in normalized or '"workflow"' in normalized or "comfyui" in normalized
    hits = sum(1 for marker in ["ksampler", "checkpointloadersimple", "loraloader", '"class_type"', '"inputs"', '"widgets_values"'] if marker in normalized)
    return has_workflow and hits >= 1


def _contains_generation_fields(normalized: str) -> bool:
    fields = ["prompt", "negative prompt", "seed", "cfg", "sampler", "model hash", "model_name", "lora", "checkpoint"]
    return sum(1 for field in fields if field in normalized) >= 2


def _extract_header_text(data: bytes) -> str:
    text = data.decode("latin-1", errors="ignore")
    strings = re.findall(r"[ -~]{4,}", text)
    useful = [item for item in strings if any(marker in item.lower() for marker in ["prompt", "seed", "sampler", "stable", "comfy", "midjourney", "openai", "firefly", "runway", "novelai", "leonardo"])]
    return "\n".join(useful[:80])


def _read_jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        return None
    offset = 2
    sof_markers = set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0))
    while offset + 9 < len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            return None
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            return None
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return width, height
        offset += segment_length
    return None


def _read_webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        return None
    offset = 12
    while offset + 8 <= len(data):
        chunk_type = data[offset : offset + 4]
        chunk_size = int.from_bytes(data[offset + 4 : offset + 8], "little")
        chunk_start = offset + 8
        if chunk_start + chunk_size > len(data):
            return None
        chunk = data[chunk_start : chunk_start + chunk_size]
        if chunk_type == b"VP8X" and len(chunk) >= 10:
            width = 1 + int.from_bytes(chunk[4:7], "little")
            height = 1 + int.from_bytes(chunk[7:10], "little")
            return width, height
        offset = chunk_start + chunk_size + (chunk_size % 2)
    return None


def _sort_bucket(item: ScanItem) -> int:
    if item.status != "analyzed" or not item.result:
        return 4
    return {
        RiskBand.HIGH: 0,
        RiskBand.MEDIUM: 1,
        RiskBand.UNKNOWN: 2,
        RiskBand.LOW: 3,
    }[item.result.band]

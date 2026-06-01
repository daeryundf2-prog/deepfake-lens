"""Test result tracking module.

Records and manages test results for continuous evaluation
of detection capabilities across different file types and models.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class TestResult:
    test_id: str
    timestamp: str
    file_path: str
    file_extension: str
    file_type: str
    model_name: str
    model_category: str
    detection_method: str
    detected: bool
    score: int
    confidence: str
    details: dict[str, Any]
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TestTracker:
    """Track and manage test results."""
    
    def __init__(self, results_path: Path | None = None):
        self.results_path = results_path or Path("test_results.json")
        self.results: list[TestResult] = []
        self._load_results()
    
    def _load_results(self) -> None:
        """Load existing results from file."""
        if self.results_path.exists():
            try:
                data = json.loads(self.results_path.read_text(encoding="utf-8"))
                self.results = [TestResult(**item) for item in data]
            except (json.JSONDecodeError, TypeError):
                self.results = []
    
    def _save_results(self) -> None:
        """Save results to file."""
        self.results_path.parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in self.results]
        self.results_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8"
        )
    
    def record_test(
        self,
        file_path: str,
        file_extension: str,
        file_type: str,
        model_name: str,
        model_category: str,
        detection_method: str,
        detected: bool,
        score: int,
        confidence: str,
        details: dict[str, Any] | None = None,
        notes: str = "",
    ) -> TestResult:
        """Record a test result."""
        result = TestResult(
            test_id=f"test_{int(time.time())}_{len(self.results)}",
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            file_path=file_path,
            file_extension=file_extension,
            file_type=file_type,
            model_name=model_name,
            model_category=model_category,
            detection_method=detection_method,
            detected=detected,
            score=score,
            confidence=confidence,
            details=details or {},
            notes=notes,
        )
        self.results.append(result)
        self._save_results()
        return result
    
    def get_statistics(self) -> dict[str, Any]:
        """Get overall statistics."""
        if not self.results:
            return {"total": 0}
        
        total = len(self.results)
        detected = sum(1 for r in self.results if r.detected)
        
        # By category
        by_category = {}
        for r in self.results:
            cat = r.model_category
            if cat not in by_category:
                by_category[cat] = {"total": 0, "detected": 0}
            by_category[cat]["total"] += 1
            if r.detected:
                by_category[cat]["detected"] += 1
        
        # By extension
        by_extension = {}
        for r in self.results:
            ext = r.file_extension
            if ext not in by_extension:
                by_extension[ext] = {"total": 0, "detected": 0}
            by_extension[ext]["total"] += 1
            if r.detected:
                by_extension[ext]["detected"] += 1
        
        # By model
        by_model = {}
        for r in self.results:
            model = r.model_name
            if model not in by_model:
                by_model[model] = {"total": 0, "detected": 0, "scores": []}
            by_model[model]["total"] += 1
            if r.detected:
                by_model[model]["detected"] += 1
            by_model[model]["scores"].append(r.score)
        
        return {
            "total": total,
            "detected": detected,
            "detection_rate": detected / total if total > 0 else 0,
            "by_category": by_category,
            "by_extension": by_extension,
            "by_model": by_model,
        }
    
    def get_category_report(self, category: str) -> dict[str, Any]:
        """Get detailed report for a specific category."""
        category_results = [r for r in self.results if r.model_category == category]
        
        if not category_results:
            return {"category": category, "total": 0}
        
        detected = sum(1 for r in category_results if r.detected)
        avg_score = sum(r.score for r in category_results) / len(category_results)
        
        # Group by model
        models = {}
        for r in category_results:
            if r.model_name not in models:
                models[r.model_name] = {"total": 0, "detected": 0, "scores": []}
            models[r.model_name]["total"] += 1
            if r.detected:
                models[r.model_name]["detected"] += 1
            models[r.model_name]["scores"].append(r.score)
        
        return {
            "category": category,
            "total": len(category_results),
            "detected": detected,
            "detection_rate": detected / len(category_results),
            "average_score": avg_score,
            "models": models,
        }
    
    def export_report(self, output_path: Path) -> None:
        """Export comprehensive report to file."""
        report = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "summary": self.get_statistics(),
            "detailed_results": [r.to_dict() for r in self.results],
        }
        
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8"
        )


def run_tracked_test(
    tracker: TestTracker,
    file_path: Path,
    model_name: str,
    model_category: str,
    detection_method: str,
) -> TestResult:
    """Run a detection test and record the result."""
    from .classifier import classify_metadata
    from .c2pa import analyze_metadata_forensic
    from .ai_agent import analyze_agent_content
    from .threed import analyze_3d_content
    from .avatar import analyze_avatar
    
    file_ext = file_path.suffix.lower()
    file_type = _determine_file_type(file_ext)
    
    detected = False
    score = 0
    confidence = "low"
    details = {}
    
    # Run appropriate detection method
    if detection_method == "classify":
        # Check if it's a text file
        text_extensions = {'.txt', '.md', '.py', '.js', '.json', '.csv', '.log'}
        if file_path.suffix.lower() in text_extensions:
            # Text file - classify text content
            from .classifier import classify_text_content
            text = file_path.read_text(encoding="utf-8", errors="ignore")
            result = classify_text_content(text)
        else:
            # Binary file - extract metadata and classify
            metadata = _extract_metadata(file_path)
            result = classify_metadata(metadata)
        detected = result.primary_match is not None
        # Convert confidence string to score
        confidence_map = {"high": 80, "medium": 50, "low": 20}
        score = confidence_map.get(result.confidence, 0)
        confidence = result.confidence
        details = {"category": result.category, "primary": result.primary_match.name if result.primary_match else None}
    
    elif detection_method == "forensic":
        result = analyze_metadata_forensic(file_path)
        detected = result.score > 0
        score = result.score
        confidence = "high" if score >= 50 else "medium" if score >= 20 else "low"
        details = {"c2pa": result.has_c2pa, "synthid": result.has_synthid, "watermark": result.has_watermark}
    
    elif detection_method == "agent":
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        result = analyze_agent_content(text=text)
        detected = result.score > 0
        score = result.score
        confidence = result.confidence
        details = {"agent_type": result.agent_type}
    
    elif detection_method == "3d":
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        result = analyze_3d_content(text=text)
        detected = result.score > 0
        score = result.score
        confidence = "high" if score >= 50 else "medium" if score >= 20 else "low"
        details = {"content_type": result.content_type}
    
    elif detection_method == "avatar":
        result = analyze_avatar(file_path=str(file_path))
        detected = result.score > 0
        score = result.score
        confidence = "high" if score >= 50 else "medium" if score >= 20 else "low"
        details = {"avatar_type": result.avatar_type}
    
    return tracker.record_test(
        file_path=str(file_path),
        file_extension=file_ext,
        file_type=file_type,
        model_name=model_name,
        model_category=model_category,
        detection_method=detection_method,
        detected=detected,
        score=score,
        confidence=confidence,
        details=details,
    )


def _determine_file_type(ext: str) -> str:
    """Determine file type from extension."""
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    audio_exts = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
    text_exts = {".txt", ".md", ".py", ".js", ".json"}
    model_3d_exts = {".glb", ".gltf", ".obj", ".fbx", ".ply"}
    
    if ext in image_exts:
        return "image"
    elif ext in video_exts:
        return "video"
    elif ext in audio_exts:
        return "audio"
    elif ext in text_exts:
        return "text"
    elif ext in model_3d_exts:
        return "3d"
    return "unknown"


def _extract_metadata(file_path: Path) -> dict[str, str]:
    """Extract metadata from file."""
    import struct
    
    metadata = {}
    try:
        data = file_path.read_bytes()
        
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            # PNG - extract text chunks
            offset = 8
            while offset + 8 <= len(data):
                length = struct.unpack(">I", data[offset:offset+4])[0]
                chunk_type = data[offset+4:offset+8]
                if chunk_type in (b"tEXt", b"iTXt"):
                    chunk_data = data[offset+8:offset+8+length]
                    if b"\x00" in chunk_data:
                        key, value = chunk_data.split(b"\x00", 1)
                        metadata[key.decode("latin-1", errors="ignore")] = value.decode("utf-8", errors="ignore")
                offset += 12 + length
                if chunk_type == b"IEND":
                    break
        elif data[:2] == b"\xff\xd8":
            metadata["format"] = "jpeg"
            metadata["size"] = str(len(data))
    except Exception:
        pass
    
    return metadata

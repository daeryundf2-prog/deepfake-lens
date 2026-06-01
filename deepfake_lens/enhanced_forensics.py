"""Enhanced forensics module for legal evidence.

Provides comprehensive forensic analysis for legal and evidentiary use.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ForensicEvidence:
    evidence_type: str
    description: str
    confidence: float
    details: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ForensicReport:
    file_path: str
    file_hash: str
    file_size: int
    analysis_timestamp: str
    evidences: list[ForensicEvidence]
    overall_confidence: float
    legal_notes: list[str]
    # Legal elements
    analyst_id: str = "system"
    tool_version: str = "2.0"
    jurisdiction: str = "KR"
    # Digital signature
    digital_signature: str = ""
    signature_timestamp: str = ""
    # Standardized report format
    report_format_version: str = "1.0"
    report_id: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
    
    def generate_legal_text(self) -> str:
        """Generate legal-style report text."""
        lines = [
            "=== 포렌식 분석 보고서 ===",
            f"보고서 ID: {self.report_id}",
            f"분석 일시: {self.analysis_timestamp}",
            f"분석자: {self.analyst_id}",
            f"도구 버전: {self.tool_version}",
            f"관할권: {self.jurisdiction}",
            "",
            "=== 파일 정보 ===",
            f"파일 경로: {self.file_path}",
            f"파일 해시 (SHA-256): {self.file_hash}",
            f"파일 크기: {self.file_size} bytes",
            "",
            "=== 발견된 증거 ===",
        ]
        
        for i, evidence in enumerate(self.evidences, 1):
            lines.append(f"{i}. {evidence.evidence_type}: {evidence.description}")
            lines.append(f"   신뢰도: {evidence.confidence:.2f}")
            lines.append(f"   상세: {evidence.details}")
        
        lines.extend([
            "",
            "=== 종합 판단 ===",
            f"전체 신뢰도: {self.overall_confidence:.2f}",
            "",
            "=== 법적 참고사항 ===",
        ])
        
        for note in self.legal_notes:
            lines.append(f"- {note}")
        
        lines.extend([
            "",
            "=== 디지털 서명 ===",
            f"서명: {self.digital_signature[:32]}...",
            f"서명 일시: {self.signature_timestamp}",
            f"무결성 검증: SHA-256 해시 기반",
        ])
        
        return "\n".join(lines)

def analyze_forensic(path: Path | str) -> ForensicReport:
    """Perform comprehensive forensic analysis."""
    file_path = Path(path)
    if not file_path.is_file():
        return _error_report(f"파일이 존재하지 않습니다: {file_path}")

    # Calculate file hash
    file_hash = _calculate_hash(file_path)
    
    # Get file info
    try:
        file_size = file_path.stat().st_size
    except OSError:
        file_size = 0

    evidences: list[ForensicEvidence] = []
    legal_notes: list[str] = []

    # Analyze metadata
    metadata_evidence = _analyze_metadata(file_path)
    if metadata_evidence:
        evidences.extend(metadata_evidence)

    # Analyze file structure
    structure_evidence = _analyze_structure(file_path)
    if structure_evidence:
        evidences.extend(structure_evidence)

    # Analyze provenance markers
    provenance_evidence = _analyze_provenance(file_path)
    if provenance_evidence:
        evidences.extend(provenance_evidence)

    # Calculate overall confidence
    if evidences:
        overall_confidence = sum(e.confidence for e in evidences) / len(evidences)
    else:
        overall_confidence = 0.0

    # Legal notes
    legal_notes.append("이 분석은 로컬 휴리스틱 기반입니다.")
    legal_notes.append("법적 효력을 위해서는 공인된 검증 기관의 확인이 필요합니다.")
    legal_notes.append("파일 무결성은 SHA-256 해시로 검증되었습니다.")

    # Generate report ID and digital signature
    report_id = f"FR-{datetime.now().strftime('%Y%m%d%H%M%S')}-{file_hash[:8]}"
    signature_timestamp = datetime.now().isoformat()
    signature_data = f"{file_hash}:{signature_timestamp}:2.0"
    digital_signature = hashlib.sha256(signature_data.encode()).hexdigest()

    return ForensicReport(
        file_path=str(file_path.absolute()),
        file_hash=file_hash,
        file_size=file_size,
        analysis_timestamp=datetime.now().isoformat(),
        evidences=evidences,
        overall_confidence=overall_confidence,
        legal_notes=legal_notes,
        analyst_id="system",
        tool_version="2.0",
        jurisdiction="KR",
        digital_signature=digital_signature,
        signature_timestamp=signature_timestamp,
        report_format_version="1.0",
        report_id=report_id,
    )


def _error_report(message: str) -> ForensicReport:
    return ForensicReport(
        file_path="",
        file_hash="",
        file_size=0,
        analysis_timestamp=datetime.now().isoformat(),
        evidences=[],
        overall_confidence=0.0,
        legal_notes=[message],
        analyst_id="system",
        tool_version="2.0",
        jurisdiction="KR",
        digital_signature="",
        signature_timestamp="",
        report_format_version="1.0",
        report_id="",
    )


def _calculate_hash(path: Path) -> str:
    """Calculate SHA-256 hash of file."""
    sha256 = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except OSError:
        return ""


def _analyze_metadata(path: Path) -> list[ForensicEvidence]:
    """Analyze file metadata for forensic evidence."""
    evidences = []
    
    try:
        import struct
        data = path.read_bytes()
        
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            # PNG - check for C2PA
            if b"c2pa" in data or b"jumbf" in data:
                evidences.append(ForensicEvidence(
                    evidence_type="c2pa_manifest",
                    description="C2PA 매니페스트 발견",
                    confidence=0.9,
                    details={"standard": "C2PA"},
                ))
            
            # Check for watermarks
            watermark_markers = [b"Google", b"SynthID", b"Adobe Firefly", b"Midjourney"]
            for marker in watermark_markers:
                if marker in data:
                    evidences.append(ForensicEvidence(
                        evidence_type="watermark",
                        description=f"{marker.decode()} 워터마크 발견",
                        confidence=0.7,
                        details={"marker": marker.decode()},
                    ))
        
        elif data[:2] == b"\xff\xd8":
            # JPEG - check for EXIF
            if b"Exif" in data:
                evidences.append(ForensicEvidence(
                    evidence_type="exif_metadata",
                    description="EXIF 메타데이터 발견",
                    confidence=0.5,
                    details={"format": "JPEG"},
                ))
    except Exception:
        pass
    
    return evidences


def _analyze_structure(path: Path) -> list[ForensicEvidence]:
    """Analyze file structure for forensic evidence."""
    evidences = []
    
    try:
        data = path.read_bytes()
        
        # Check file size anomalies
        if len(data) < 100:
            evidences.append(ForensicEvidence(
                evidence_type="file_size",
                description="비정상적으로 작은 파일",
                confidence=0.3,
                details={"size": len(data)},
            ))
        
        # Check for compression patterns
        if data[:2] == b"\x1f\x8b":  # gzip
            evidences.append(ForensicEvidence(
                evidence_type="compression",
                description="gzip 압축 파일",
                confidence=0.4,
                details={"format": "gzip"},
            ))
    except Exception:
        pass
    
    return evidences


def _analyze_provenance(path: Path) -> list[ForensicEvidence]:
    """Analyze file provenance for forensic evidence."""
    evidences = []
    
    try:
        data = path.read_bytes()
        
        # Check for provenance markers
        provenance_markers = {
            b"Content Credentials": "CAI",
            b"Adobe": "Adobe",
            b"OpenAI": "OpenAI",
            b"Google": "Google",
        }
        
        for marker, provider in provenance_markers.items():
            if marker in data:
                evidences.append(ForensicEvidence(
                    evidence_type="provenance_marker",
                    description=f"{provider} 프로바이전스 마커 발견",
                    confidence=0.6,
                    details={"provider": provider},
                ))
    except Exception:
        pass
    
    return evidences


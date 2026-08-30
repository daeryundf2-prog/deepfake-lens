"""Forensic evidence chain management module.

Provides evidence chain tracking, audit trails, and integrity verification
for legal and forensic use cases.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class EvidenceChain:
    file_hash: str
    file_path: str
    file_size: int
    analysis_timestamp: str
    analyst_id: str
    tool_version: str
    parameters: dict[str, str]
    results: dict[str, object]
    integrity_verified: bool

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AuditTrail:
    action: str
    timestamp: str
    user: str
    details: dict[str, str]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ForensicReport:
    evidence_chains: list[EvidenceChain]
    audit_trail: list[AuditTrail]
    report_date: str
    total_files: int
    verified_count: int
    integrity_score: float

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def create_evidence_chain(
    file_path: Path | str,
    results: dict[str, object],
    *,
    analyst_id: str = "system",
    tool_version: str = "0.1.0",
    parameters: dict[str, str] | None = None,
) -> EvidenceChain:
    """Create an evidence chain for a file analysis.

    ``integrity_verified`` is measured, not assumed: the chain is marked
    verified only when an immediate re-hash of the file still matches the
    recorded hash.
    """
    path = Path(file_path)

    # Calculate file hash
    file_hash = _calculate_hash(path)

    # Get file info
    try:
        file_size = path.stat().st_size
    except OSError:
        file_size = 0

    chain = EvidenceChain(
        file_hash=file_hash,
        file_path=str(path.absolute()),
        file_size=file_size,
        analysis_timestamp=datetime.now().isoformat(),
        analyst_id=analyst_id,
        tool_version=tool_version,
        parameters=parameters or {},
        results=results,
        integrity_verified=False,
    )
    return replace(chain, integrity_verified=verify_integrity(chain))


def verify_integrity(chain: EvidenceChain) -> bool:
    """Verify evidence chain integrity by re-hashing the file."""
    path = Path(chain.file_path)
    
    if not path.exists():
        return False
    
    current_hash = _calculate_hash(path)
    return current_hash == chain.file_hash


def create_audit_trail(
    action: str,
    user: str,
    details: dict[str, str] | None = None,
) -> AuditTrail:
    """Create an audit trail entry."""
    return AuditTrail(
        action=action,
        timestamp=datetime.now().isoformat(),
        user=user,
        details=details or {},
    )


def generate_forensic_report(
    evidence_chains: list[EvidenceChain],
    audit_trail: list[AuditTrail],
) -> ForensicReport:
    """Generate a comprehensive forensic report."""
    verified_count = sum(1 for chain in evidence_chains if verify_integrity(chain))
    integrity_score = verified_count / max(1, len(evidence_chains))
    
    return ForensicReport(
        evidence_chains=evidence_chains,
        audit_trail=audit_trail,
        report_date=datetime.now().isoformat(),
        total_files=len(evidence_chains),
        verified_count=verified_count,
        integrity_score=integrity_score,
    )


def save_evidence_chains(chains: list[EvidenceChain], path: Path) -> None:
    """Save evidence chains to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [chain.to_json() for chain in chains]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_evidence_chains(path: Path | str) -> tuple[list[EvidenceChain], list[str]]:
    """Load evidence chains, reporting anything that had to be skipped.

    A forensic chain-of-custody loader must never silently present a
    damaged file as an empty set, so this returns ``(chains, issues)``:
    well-formed chains plus one issue line per skipped entry (or file-level
    problem). Callers that only want the chains can ignore the second
    element — but should not, for evidence records.
    """
    evidence_path = Path(path)
    if not evidence_path.exists():
        return [], [f"file not found: {evidence_path}"]

    try:
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [], [f"invalid JSON: {exc}"]
    if not isinstance(data, list):
        return [], ["unexpected top-level structure (expected a list of chains)"]

    chains: list[EvidenceChain] = []
    issues: list[str] = []
    for index, item in enumerate(data):
        try:
            chains.append(EvidenceChain(**item))
        except TypeError as exc:
            issues.append(f"entry {index} skipped: {exc}")
    return chains, issues


def _calculate_hash(path: Path) -> str:
    """Calculate SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except OSError:
        return ""

"""Batch processing module for large-scale file analysis.

Provides efficient batch processing with progress tracking,
parallel execution, and result aggregation.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class BatchJob:
    job_id: str
    status: str
    total_files: int
    processed_files: int
    failed_files: int
    start_time: str
    end_time: str | None
    results: list[dict[str, Any]]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BatchResult:
    file_path: str
    status: str
    result: dict[str, Any] | None
    error: str | None
    processing_time: float

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class BatchProcessor:
    """Process files in batch with parallel execution."""
    
    def __init__(
        self,
        max_workers: int = 4,
        timeout: int = 300,
    ) -> None:
        self.max_workers = max_workers
        self.timeout = timeout
        self.jobs: dict[str, BatchJob] = {}
    
    def process_batch(
        self,
        files: list[Path],
        processor: Callable[[Path], dict[str, Any]],
        job_id: str | None = None,
    ) -> BatchJob:
        """Process a batch of files."""
        if job_id is None:
            job_id = f"batch_{int(time.time())}"
        
        start_time = time.time()
        results: list[BatchResult] = []
        processed = 0
        failed = 0
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_file = {
                executor.submit(self._process_single, file, processor): file
                for file in files
            }
            
            for future in as_completed(future_to_file, timeout=self.timeout):
                file = future_to_file[future]
                try:
                    result = future.result()
                    results.append(result)
                    if result.status == "success":
                        processed += 1
                    else:
                        failed += 1
                except Exception as exc:
                    failed += 1
                    results.append(BatchResult(
                        file_path=str(file),
                        status="error",
                        result=None,
                        error=str(exc),
                        processing_time=0.0,
                    ))
        
        end_time = time.time()
        
        job = BatchJob(
            job_id=job_id,
            status="completed",
            total_files=len(files),
            processed_files=processed,
            failed_files=failed,
            start_time=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(start_time)),
            end_time=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(end_time)),
            results=[r.to_json() for r in results],
        )
        
        self.jobs[job_id] = job
        return job
    
    def _process_single(
        self,
        file: Path,
        processor: Callable[[Path], dict[str, Any]],
    ) -> BatchResult:
        """Process a single file."""
        start_time = time.time()
        try:
            result = processor(file)
            processing_time = time.time() - start_time
            return BatchResult(
                file_path=str(file),
                status="success",
                result=result,
                error=None,
                processing_time=processing_time,
            )
        except Exception as exc:
            processing_time = time.time() - start_time
            return BatchResult(
                file_path=str(file),
                status="error",
                result=None,
                error=str(exc),
                processing_time=processing_time,
            )
    
    def get_job(self, job_id: str) -> BatchJob | None:
        """Get a batch job by ID."""
        return self.jobs.get(job_id)
    
    def get_summary(self, job_id: str) -> dict[str, Any]:
        """Get summary statistics for a batch job."""
        job = self.jobs.get(job_id)
        if job is None:
            return {"error": "Job not found"}
        
        processing_times = []
        for result_json in job.results:
            if isinstance(result_json, dict):
                processing_times.append(result_json.get("processing_time", 0.0))
        
        avg_time = sum(processing_times) / max(1, len(processing_times))
        
        return {
            "job_id": job.job_id,
            "total_files": job.total_files,
            "processed_files": job.processed_files,
            "failed_files": job.failed_files,
            "success_rate": job.processed_files / max(1, job.total_files),
            "average_processing_time": avg_time,
            "total_processing_time": sum(processing_times),
        }


def save_batch_results(job: BatchJob, path: Path) -> None:
    """Save batch results to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_batch_results(path: Path) -> BatchJob | None:
    """Load batch results from a JSON file."""
    if not path.exists():
        return None
    
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return BatchJob(**data)
    except (json.JSONDecodeError, TypeError):
        return None

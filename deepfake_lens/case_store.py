from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .error_text import read_error_ko
from .json_text import json_dumps
from .output_files import write_output_text
from .scan_jobs import JOB_ID_PATTERN
from .user_data import (
    USER_DATA_READ_FAILED,
    UserDataRefused,
    UserDataWriteFailed,
    check_outside,
    record_path,
    write_failed,
)

CASES_FILE = "cases.json"
CASES_ENV = "DEEPFAKE_LENS_CASES"
CASE_KEYS = ("case_no", "case_name", "plaintiff", "defendant", "court", "law_firm", "center", "contact", "side")
MAX_VALUE_CHARS = 200
SIDES = ("", "plaintiff", "defendant")


class CaseInputInvalid(ValueError):
    pass


def _job_id(job_id: object) -> str:
    if not isinstance(job_id, str) or not JOB_ID_PATTERN.match(job_id):
        raise CaseInputInvalid(f"작업 ID가 올바르지 않습니다: 「{str(job_id)[:64]}」")
    return job_id


def _clean_fields(fields: object) -> dict[str, str]:
    if not isinstance(fields, dict):
        raise CaseInputInvalid("사건 정보는 항목과 값의 객체여야 합니다")
    clean: dict[str, str] = {}
    for key, value in fields.items():
        if key not in CASE_KEYS:
            raise CaseInputInvalid(f"알 수 없는 사건 항목입니다: 「{str(key)[:50]}」")
        if not isinstance(value, str):
            raise CaseInputInvalid(f"「{key}」 값은 문자열이어야 합니다")
        text = value.strip()
        if len(text) > MAX_VALUE_CHARS:
            raise CaseInputInvalid(f"「{key}」 값은 {MAX_VALUE_CHARS}자를 넘을 수 없습니다")
        if key == "side" and text not in SIDES:
            raise CaseInputInvalid(f"「side」 값은 plaintiff, defendant 또는 비워 두어야 합니다: 「{text[:50]}」")
        if text:
            clean[key] = text
    return clean


def _path(evidence: list[Path] | tuple[Path, ...]) -> Path:
    path = record_path(CASES_FILE, CASES_ENV)
    check_outside(path, evidence, CASES_ENV)
    return path


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UserDataRefused(USER_DATA_READ_FAILED.format(path=path, reason=read_error_ko(exc), env=CASES_ENV)) from exc
    if not isinstance(data, dict):
        raise UserDataRefused(USER_DATA_READ_FAILED.format(path=path, reason="JSON 객체가 아닙니다", env=CASES_ENV))
    return data


def _persist(path: Path, store: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_output_text(path, json_dumps(store, ensure_ascii=False, indent=2))
        os.chmod(path, 0o600)
    except OSError as exc:
        raise write_failed(path, exc, CASES_ENV) from exc


def read_all(evidence: list[Path] | tuple[Path, ...] = ()) -> dict[str, dict[str, str]]:
    return {job: dict(fields) for job, fields in _load(_path(evidence)).items() if isinstance(fields, dict)}


def read_case(job_id: str, evidence: list[Path] | tuple[Path, ...] = ()) -> dict[str, str]:
    return read_all(evidence).get(_job_id(job_id), {})


def write_case(job_id: str, fields: dict[str, str], evidence: list[Path] | tuple[Path, ...] = ()) -> dict[str, str]:
    job = _job_id(job_id)
    clean = _clean_fields(fields)
    path = _path(evidence)
    try:
        store = _load(path)
    except UserDataRefused as exc:
        raise UserDataWriteFailed(str(exc)) from exc
    if clean:
        store[job] = clean
    else:
        store.pop(job, None)
    _persist(path, store)
    return clean


def delete_case(job_id: str, evidence: list[Path] | tuple[Path, ...] = ()) -> bool:
    job = _job_id(job_id)
    path = _path(evidence)
    store = _load(path)
    if job not in store:
        return False
    del store[job]
    _persist(path, store)
    return True


def prune_cases(keep_job_ids: Iterable[str], evidence: list[Path] | tuple[Path, ...] = ()) -> int:
    keep = set(keep_job_ids)
    path = _path(evidence)
    store = _load(path)
    stale = [job for job in store if job not in keep]
    if not stale:
        return 0
    for job in stale:
        del store[job]
    _persist(path, store)
    return len(stale)

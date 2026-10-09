"""Report timestamps: ISO 8601 with the UTC offset (R12-9).

Before R12-9 the legal report's ``generated_at`` / "분석 일시", the PDF
report's "감정 일시" (labelled "(KST)" whatever the machine's zone), the
forensic/evidence-chain records, the vendor-weights report and batch jobs
wrote local wall-clock time without a zone — ambiguous once a report leaves
the machine (Asia/Seoul and UTC readings differ by nine hours). Every
report timestamp is now :func:`report_timestamp`: ISO 8601, seconds, with
the local offset — ``2026-10-10T16:04:22+09:00``.

``deepfake_lens/tests/test_report_time.py`` checks the format under several
``TZ`` settings and that no other module builds a report time from a naive
``datetime.now()`` or ``time.localtime``.
"""

from __future__ import annotations

from datetime import datetime

# Seconds: sub-second digits add nothing to a report and differ between runs.
REPORT_TIMESPEC = "seconds"
# Compact form for identifiers built from the same moment ("LR-20261010160422-…").
REPORT_ID_STAMP = "%Y%m%d%H%M%S"


def report_moment(moment: datetime | float | None = None) -> datetime:
    """``moment`` (now when None; a POSIX timestamp; a naive value is local time) as an aware local datetime."""
    if moment is None:
        return datetime.now().astimezone()
    if isinstance(moment, (int, float)):
        return datetime.fromtimestamp(moment).astimezone()
    return moment.astimezone()


def report_timestamp(moment: datetime | float | None = None) -> str:
    """ISO 8601 with the UTC offset, to the second: ``2026-10-10T16:04:22+09:00``."""
    return report_moment(moment).isoformat(timespec=REPORT_TIMESPEC)

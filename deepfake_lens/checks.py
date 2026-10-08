"""Fail-closed check runner (G1).

Every analyzer call in the scan pipeline goes through :func:`run_check` so
that a crash can never read as a clean result. The outcome of each call is
a :class:`~deepfake_lens.result_types.CoverageEntry`:

- the call returned               -> ``ran``
- it raised ``ImportError``       -> ``skipped`` "의존성 부재: <module>"
- it raised ``CheckSkipped``      -> ``skipped`` with the given reason
- it raised anything else         -> ``failed`` "<ExcType>: <message>"

A failed entry makes ``decision.decide`` return ``undetermined`` unless
strong deterministic synthetic evidence exists. The traceback is logged so
the failure can be investigated; it is never swallowed.
"""

from __future__ import annotations

import logging
from typing import Callable, TypeVar

from .result_types import CoverageEntry, CoverageStatus

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Upper bound on the exception message kept in a coverage reason — enough
# for the examiner to identify the failure, short enough for a report cell.
FAILURE_MESSAGE_MAX_CHARS = 200


class CheckSkipped(Exception):
    """Raised inside a check to record a deliberate, explained skip.

    Used for "not applicable" outcomes that are not errors, e.g. no face
    detected or input outside the measured range.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AnalyzerError(RuntimeError):
    """An analyzer reported a failure through its return value.

    Several legacy analyzers return an "error analysis" object instead of
    raising; ``core`` converts those into this exception inside the check
    so they are recorded as ``failed`` rather than as a quiet empty result.
    """


def skipped(check: str, reason: str) -> CoverageEntry:
    return CoverageEntry(check, CoverageStatus.SKIPPED, reason)


def failed(check: str, exc: BaseException) -> CoverageEntry:
    return CoverageEntry(check, CoverageStatus.FAILED, failure_reason(exc))


def failure_reason(exc: BaseException) -> str:
    message = str(exc)[:FAILURE_MESSAGE_MAX_CHARS]
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def dependency_reason(exc: ImportError) -> str:
    module = exc.name or str(exc) or type(exc).__name__
    return f"의존성 부재: {module}"


def run_check(
    name: str,
    fn: Callable[[], T],
    *,
    reraise: tuple[type[BaseException], ...] = (),
) -> tuple[T | None, CoverageEntry]:
    """Run one analyzer call and record what happened to it.

    ``reraise`` lists exception types the caller handles itself (e.g.
    ``OSError`` when an unreadable input must fail the whole item rather
    than one check).
    """
    try:
        value = fn()
    except reraise:
        raise
    except CheckSkipped as exc:
        return None, skipped(name, exc.reason)
    except ImportError as exc:  # ModuleNotFoundError is a subclass
        logger.info("check %s skipped: missing dependency %s", name, exc.name or exc)
        return None, skipped(name, dependency_reason(exc))
    except Exception as exc:
        logger.exception("check %s failed", name)
        return None, failed(name, exc)
    return value, CoverageEntry(name, CoverageStatus.RAN)

"""Run a local F08 collection without replacing the previous complete generation on failure."""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import sys

from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from time import sleep
from uuid import uuid4

from benefitfinder.ingestion.collect import CollectionError, CollectionResult
from benefitfinder.ingestion.response_diagnostics import (
    CONTENT_TYPES,
    IDENTIFIER_SOURCES,
    RESPONSE_FORMATS,
    bounded_code,
)
from benefitfinder.jobs.refresh_lock import acquire_refresh_lock

STATUS_FILE = "status.json"
_RUNS_DIR = "runs"
_LOCK_FILE = ".refresh-lock"
_RETRY_DELAY_SECONDS = 5
_ERROR_CODES = {
    "fetch_failed",
    "unsafe_response",
    "http_status",
    "parse_failed",
    "missing_external_id",
    "conflicting_duplicate",
    "total_changed",
    "unexpected_empty_page",
    "short_page",
    "page_count_mismatch",
    "record_count_mismatch",
    "page_limit",
    "refresh_attempt_failed",
    "refresh_incomplete",
}


class RefreshError(ValueError):
    """Fixed error code safe to print without file or source contents."""


def refresh_notification(root: Path, code: int, started_at: datetime) -> str:
    """Allow only aggregate counts and fixed codes into the outbound status message."""
    lines = [
        "[F08] 온통청년 갱신 " + ("성공" if code == 0 else "실패"),
        f"실행 시각: {started_at.isoformat()}",
        f"종료 코드: {code}",
    ]
    try:
        state = json.loads((root / STATUS_FILE).read_text(encoding="utf-8"))
        attempt = state["last_attempt"]
        attempt_at = datetime.fromisoformat(attempt["started_at"])
        if attempt_at < started_at:
            return "\n".join(lines)
        expected = "success" if code == 0 else "failed"
        if attempt["status"] != expected:
            return "\n".join(lines)
        if type(attempt.get("attempts")) is int and attempt["attempts"] in (1, 2):
            lines.append(f"시도: {attempt['attempts']}회")
        if code == 0:
            count = state["last_success"]["record_count"]
            if type(count) is int and count >= 0:
                lines.append(f"수집: {count}건")
            changes = attempt.get("changes", {})
            for name, label in (("new", "신규"), ("changed", "변경"), ("missing", "누락")):
                count = changes.get(name, 0)
                if type(count) is int and count >= 0:
                    lines.append(f"{label}: {count}건")
        else:
            errors = attempt.get("error_codes", [])
            if isinstance(errors, list):
                codes = sorted(
                    {item for item in errors if isinstance(item, str) and item in _ERROR_CODES}
                )
                if codes:
                    lines.append("오류 코드: " + ", ".join(codes))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return "\n".join(lines)


def _failure_diagnostic(error: CollectionError, attempt: int) -> dict[str, object]:
    """Allowlist metadata before writing to stderr or the local status file."""
    return {
        "attempt": attempt,
        "page": error.page if type(error.page) is int and error.page > 0 else None,
        "error_code": error.code if error.code in _ERROR_CODES else "unknown",
        "http_status": (
            error.http_status
            if type(error.http_status) is int and 100 <= error.http_status <= 599 else None
        ),
        "api_code": bounded_code(error.api_code),
        "response_format": (
            error.response_format if isinstance(error.response_format, str)
            and error.response_format in RESPONSE_FORMATS else None
        ),
        "content_type": (
            error.content_type if isinstance(error.content_type, str)
            and error.content_type in CONTENT_TYPES else None
        ),
        "response_bytes": (
            error.response_bytes if type(error.response_bytes) is int
            and 0 <= error.response_bytes < 2**63 else None
        ),
        "response_sha256": (
            error.response_sha256 if isinstance(error.response_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", error.response_sha256) else None
        ),
        "error_identifier": bounded_code(error.error_identifier),
        "error_identifier_source": (
            error.error_identifier_source if isinstance(error.error_identifier_source, str)
            and error.error_identifier_source in IDENTIFIER_SOURCES else None
        ),
    }


def _last_success(root: Path) -> tuple[dict[str, object] | None, Path | None]:
    try:
        raw = (root / STATUS_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except UnicodeError:
        raise RefreshError("refresh_state_invalid") from None
    try:
        state = json.loads(raw)
    except (ValueError, RecursionError):
        raise RefreshError("refresh_state_invalid") from None
    if (
        not isinstance(state, dict)
        or state.get("format_version") != 1
        or "last_success" not in state
    ):
        raise RefreshError("refresh_state_invalid")
    success = state["last_success"]
    if success is None:
        return None, None
    if not isinstance(success, dict):
        raise RefreshError("refresh_state_invalid")
    run_id = success.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise RefreshError("refresh_state_invalid")
    previous = root / _RUNS_DIR / run_id
    if not previous.is_dir():
        raise RefreshError("refresh_previous_run_missing")
    return success, previous


def _write_status(path: Path, state: dict[str, object]) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".status-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write((json.dumps(state, indent=2) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _retryable(result: CollectionResult) -> bool:
    return bool(result.errors) and all(
        error.code in {"fetch_failed", "total_changed"}
        or (error.code == "http_status" and error.detail in {"500", "502", "503", "504"})
        for error in result.errors
    )


def _discard_unselected_run(root: Path, generation: Path) -> None:
    """Remove only this attempt's generation when the current pointer rules it out."""
    try:
        selected, _ = _last_success(root)
    except (OSError, RefreshError):
        # A failed replace can leave its outcome uncertain. Preserve data in that case.
        return
    if selected is None or selected["run_id"] != generation.name:
        shutil.rmtree(generation)


def _run_locked(
    root: Path,
    build: Callable[[Path, Path | None], CollectionResult],
    clock: Callable[[], datetime],
) -> bool:
    last_success, previous = _last_success(root)
    runs = root / _RUNS_DIR
    runs.mkdir(exist_ok=True)
    started_at = clock().isoformat()
    changes: dict[str, int] = {}
    error_codes: list[str] = []
    failures: list[dict[str, object]] = []
    complete = False
    generation: Path | None = None

    for attempts in (1, 2):
        try:
            with TemporaryDirectory(dir=runs, prefix=".attempt-") as temp:
                staging = Path(temp)
                result = build(staging, previous)
                if result.complete:
                    run_id = uuid4().hex
                    generation = staging.rename(runs / run_id)
        except (OSError, ValueError, KeyError, TypeError):
            error_codes = ["refresh_attempt_failed"]
            break
        for error in result.errors:
            diagnostic = _failure_diagnostic(error, attempts)
            failures.append(diagnostic)
            print("온통청년 수집 오류: " + json.dumps(diagnostic), file=sys.stderr, flush=True)
        if result.complete:
            last_success = {
                "run_id": run_id,
                "completed_at": clock().isoformat(),
                "record_count": len(result.records),
            }
            changes = dict(Counter(change.kind.value for change in result.changes))
            error_codes = []
            complete = True
            break
        error_codes = sorted({error.code for error in result.errors}) or ["refresh_incomplete"]
        if attempts == 1 and _retryable(result):
            sleep(_RETRY_DELAY_SECONDS)
        else:
            break

    try:
        _write_status(
            root / STATUS_FILE,
            {
                "format_version": 1,
                "last_success": last_success,
                "last_attempt": {
                    "status": "success" if complete else "failed",
                    "started_at": started_at,
                    "finished_at": clock().isoformat(),
                    "attempts": attempts,
                    "error_codes": error_codes,
                    "errors": failures,
                    "changes": changes,
                },
            },
        )
    except OSError:
        if generation is not None:
            _discard_unselected_run(root, generation)
        raise
    return complete


def run_youthcenter_refresh(
    root: Path,
    build: Callable[[Path, Path | None], CollectionResult],
    clock: Callable[[], datetime],
    *,
    ingest_complete: Callable[[], bool] | None = None,
) -> bool:
    """Keep collection and optional DB handoff under the same source lock.

    Retry the previous complete generation before collecting another one, so a
    failed DB handoff cannot be skipped when the local pointer moves forward.
    """
    root.mkdir(parents=True, exist_ok=True)
    lock = root / _LOCK_FILE
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            acquire_refresh_lock(descriptor)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise RefreshError("refresh_already_running") from None
            raise
        if ingest_complete is not None:
            previous, _ = _last_success(root)
            if previous is not None and not ingest_complete():
                raise RefreshError("refresh_database_ingest_failed")
        complete = _run_locked(root, build, clock)
        if complete and ingest_complete is not None and not ingest_complete():
            raise RefreshError("refresh_database_ingest_failed")
        return complete
    finally:
        os.close(descriptor)

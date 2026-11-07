"""Run a local F08 collection without replacing the previous complete generation on failure."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from time import sleep
from uuid import uuid4

from benefitfinder.ingestion.collect import CollectionResult

STATUS_FILE = "status.json"
_RUNS_DIR = "runs"
_RETRY_DELAY_SECONDS = 5


class RefreshError(ValueError):
    """Fixed error code safe to print without file or source contents."""


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
    complete = False

    for attempts in (1, 2):
        try:
            with TemporaryDirectory(dir=runs, prefix=".attempt-") as temp:
                staging = Path(temp)
                result = build(staging, previous)
                if result.complete:
                    run_id = uuid4().hex
                    staging.rename(runs / run_id)
        except (OSError, ValueError, KeyError, TypeError):
            error_codes = ["refresh_attempt_failed"]
            break
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
                "changes": changes,
            },
        },
    )
    return complete


def run_youthcenter_refresh(
    root: Path,
    build: Callable[[Path, Path | None], CollectionResult],
    clock: Callable[[], datetime],
) -> bool:
    """Publish a complete local generation by replacing only its status pointer."""
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".refresh-lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise RefreshError("refresh_already_running") from None
    try:
        return _run_locked(root, build, clock)
    finally:
        lock.rmdir()

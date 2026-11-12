"""Run independent F08 sources once and persist only aggregate run metadata."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import errno
import fcntl
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

STATUS_FILE = "status.json"
_MAX_STATE_BYTES = 65_536


class DailyRefreshError(ValueError):
    """A fixed preparation error; stages and notifications have not started."""


def _last_success(path: Path) -> dict[str, str] | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_STATE_BYTES + 1)
    except FileNotFoundError:
        return None
    try:
        if len(raw) > _MAX_STATE_BYTES:
            raise ValueError
        state = json.loads(raw)
        if (
            not isinstance(state, dict)
            or type(state.get("format_version")) is not int
            or state["format_version"] != 1
        ):
            raise ValueError
        success = state["last_success"]
        if success is None:
            return None
        if not isinstance(success, dict) or set(success) != {"run_id", "finished_at"}:
            raise ValueError
        run_id, finished_at = success["run_id"], success["finished_at"]
        if (
            not isinstance(run_id, str)
            or len(run_id) != 32
            or any(c not in "0123456789abcdef" for c in run_id)
        ):
            raise ValueError
        if (
            not isinstance(finished_at, str)
            or datetime.fromisoformat(finished_at).utcoffset() is None
        ):
            raise ValueError
        # Retain only fields verified here; never propagate arbitrary file contents.
        return {"run_id": run_id, "finished_at": finished_at}
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise DailyRefreshError("daily_refresh_state_invalid") from None


def _write_status(path: Path, state: dict[str, object]) -> None:
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix=".daily-status-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write((json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _notification(attempt: dict[str, object], *, state_failed: bool) -> str:
    stages = attempt["stages"]
    assert isinstance(stages, dict)
    lines = ["[F08] 정책·기준금리 갱신", f"실행 시각: {attempt['started_at']}"]
    for name, label in (("youthcenter", "온통청년"), ("ecos", "ECOS 기준금리")):
        stage = stages[name]
        assert isinstance(stage, dict)
        # Stage names and codes are constructed in this process, never from raw data.
        lines.append(f"{label}: {'성공' if stage['exit_code'] == 0 else '실패'}")
    if state_failed:
        lines.append("실행 기록 저장 실패: daily_refresh_io_error")
    return "\n".join(lines)


def _run_locked(
    root: Path,
    *,
    refresh_policies: Callable[[], int],
    refresh_rate: Callable[[], int],
    clock: Callable[[], datetime],
    notify: Callable[[str], None] | None,
) -> int:
    path = root / STATUS_FILE
    previous = _last_success(path)
    run_id = uuid4().hex
    attempt: dict[str, object] = {
        "run_id": run_id,
        "started_at": clock().isoformat(),
        "finished_at": None,
        "data_status": "running",
        "notification_status": "pending" if notify is not None else "not_requested",
        "stages": {},
    }
    state: dict[str, object] = {
        "format_version": 1,
        "last_success": previous,
        "last_attempt": attempt,
    }
    # If preparation fails, do not start unrecorded work. A killed job leaves a
    # running marker, never a fabricated completed attempt.
    _write_status(path, state)
    stages = attempt["stages"]
    assert isinstance(stages, dict)
    state_failed = False

    def run_source(refresh: Callable[[], int]) -> tuple[int, str]:
        try:
            code = refresh()
            if type(code) is not int or code not in (0, 1, 2):
                code = 1
        except Exception:
            # HTTP/DB exceptions may contain credentials or original data.
            code = 1
        return code, clock().isoformat()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="f08-source") as executor:
        futures = {}
        for name, refresh in (("youthcenter", refresh_policies), ("ecos", refresh_rate)):
            stages[name] = {
                "started_at": clock().isoformat(),
                "finished_at": None,
                "status": "running",
                "exit_code": None,
            }
            try:
                _write_status(path, state)
            except OSError:
                # Continue independent sources even if a later journal write fails.
                state_failed = True
            futures[executor.submit(run_source, refresh)] = name
        for future in as_completed(futures):
            code, finished_at = future.result()
            stages[futures[future]].update(
                finished_at=finished_at,
                exit_code=code,
                status="success" if code == 0 else "failed",
            )
            try:
                _write_status(path, state)
            except OSError:
                state_failed = True
    succeeded = all(stage["exit_code"] == 0 for stage in stages.values())
    attempt["finished_at"] = clock().isoformat()
    attempt["data_status"] = "success" if succeeded else "failed"
    if succeeded:
        state["last_success"] = {"run_id": run_id, "finished_at": attempt["finished_at"]}
    try:
        _write_status(path, state)
    except OSError:
        state_failed = True
    if notify is not None:
        try:
            notify(_notification(attempt, state_failed=state_failed))
            attempt["notification_status"] = "sent"
        except Exception:
            attempt["notification_status"] = "failed"
    attempt["record_status"] = "failed" if state_failed else "success"
    try:
        _write_status(path, state)
    except OSError:
        state_failed = True
    return 0 if succeeded and not state_failed and attempt["notification_status"] != "failed" else 1


def run_daily_refresh(
    root: Path,
    *,
    refresh_policies: Callable[[], int],
    refresh_rate: Callable[[], int],
    clock: Callable[[], datetime],
    notify: Callable[[str], None] | None = None,
) -> int:
    """Attempt both sources; preserve each source's own atomic last-success state."""
    descriptor = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(root / ".refresh-lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN}:
                raise DailyRefreshError("daily_refresh_already_running") from None
            raise
        return _run_locked(
            root,
            refresh_policies=refresh_policies,
            refresh_rate=refresh_rate,
            clock=clock,
            notify=notify,
        )
    except OSError:
        raise DailyRefreshError("daily_refresh_io_error") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)

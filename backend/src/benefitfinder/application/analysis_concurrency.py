"""Bound the number of concurrently active analysis workers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Lock
from typing import Any, TypeVar

T = TypeVar("T")


class AnalysisConcurrencyBusy(RuntimeError):
    """Raised when no analysis worker slot is available."""


class AnalysisRequestLease:
    """Keep one request slot until closed and all workers have finished."""

    def __init__(self, guard: "AnalysisConcurrencyGuard") -> None:
        self._guard = guard
        self._closed = False
        self._released = False
        self._pending_workers = 0
        self._lock = Lock()

    def close(self) -> None:
        release = False
        with self._lock:
            self._closed = True
            if self._pending_workers == 0 and not self._released:
                self._released = True
                release = True

        if release:
            self._guard.release()

    def _worker_done(self) -> None:
        release = False
        with self._lock:
            self._pending_workers -= 1
            if (
                self._closed
                and self._pending_workers == 0
                and not self._released
            ):
                self._released = True
                release = True

        if release:
            self._guard.release()

    async def run_in_thread(
        self,
        function: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Track the real worker even if its awaiting task is cancelled."""
        with self._lock:
            if self._closed:
                raise RuntimeError("analysis_request_closed")
            self._pending_workers += 1

        try:
            future = self._guard._executor.submit(
                function, *args, **kwargs
            )
        except BaseException:
            self._worker_done()
            raise

        future.add_done_callback(
            lambda completed: self._worker_done()
        )

        return await asyncio.wrap_future(future)

class AnalysisConcurrencyGuard:
    """Thread-safe nonblocking admission for analysis work."""

    def __init__(self, *, max_concurrent: int) -> None:
        if (
            isinstance(max_concurrent, bool)
            or not isinstance(max_concurrent, int)
            or max_concurrent < 1
        ):
            raise ValueError("invalid_max_concurrent")

        self._slots = BoundedSemaphore(max_concurrent)
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent,
            thread_name_prefix="benefitfinder-analysis",
        )

    def acquire_request(self) -> AnalysisRequestLease:
        """Reserve one slot for a complete analysis request."""
        if not self.try_acquire():
            raise AnalysisConcurrencyBusy("analysis_worker_busy")
        return AnalysisRequestLease(self)
    def try_acquire(self) -> bool:
        return self._slots.acquire(blocking=False)

    def release(self) -> None:
        self._slots.release()

    async def run_in_thread(
        self,
        function: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Release the slot when the worker finishes or is cancelled before start."""
        if not self.try_acquire():
            raise AnalysisConcurrencyBusy("analysis_worker_busy")

        try:
            future = self._executor.submit(function, *args, **kwargs)
        except BaseException:
            self.release()
            raise

        future.add_done_callback(lambda completed: self.release())

        return await asyncio.wrap_future(future)

    def shutdown(self) -> None:
        """Stop accepting new work while allowing active workers to finish."""
        self._executor.shutdown(wait=False, cancel_futures=False)

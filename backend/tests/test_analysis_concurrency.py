"""Regression tests for bounded concurrent analysis work."""

from unittest import TestCase

from benefitfinder.application.analysis_concurrency import AnalysisConcurrencyGuard


class AnalysisConcurrencyGuardTests(TestCase):
    def test_one_active_worker_blocks_another_until_release(self):
        guard = AnalysisConcurrencyGuard(max_concurrent=1)

        self.assertTrue(guard.try_acquire())
        self.assertFalse(guard.try_acquire())

        guard.release()

        self.assertTrue(guard.try_acquire())
        guard.release()
    def test_worker_keeps_slot_until_thread_finishes(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        guard = AnalysisConcurrencyGuard(max_concurrent=1)
        entered = threading.Event()
        release_worker = threading.Event()

        def work():
            try:
                entered.set()
                self.assertTrue(release_worker.wait(2))
            finally:
                guard.release()

        self.assertTrue(guard.try_acquire())

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(work)
            try:
                self.assertTrue(entered.wait(1))
                self.assertFalse(guard.try_acquire())
            finally:
                release_worker.set()

            future.result(timeout=3)

        self.assertTrue(guard.try_acquire())
        guard.release()
    def test_cancelled_await_keeps_slot_until_worker_finishes(self):
        import asyncio
        import threading

        guard = AnalysisConcurrencyGuard(max_concurrent=1)
        entered = threading.Event()
        finish_worker = threading.Event()
        exited = threading.Event()

        def work():
            try:
                entered.set()
                if not finish_worker.wait(3):
                    raise TimeoutError("test worker did not finish")
            finally:
                guard.release()
                exited.set()

        async def scenario():
            self.assertTrue(guard.try_acquire())
            task = asyncio.create_task(asyncio.to_thread(work))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(guard.try_acquire())
            finally:
                finish_worker.set()
                self.assertTrue(await asyncio.to_thread(exited.wait, 2))

            self.assertTrue(guard.try_acquire())
            guard.release()

        asyncio.run(scenario())
    def test_run_in_thread_releases_slot_after_worker_finishes(self):
        import asyncio
        import threading

        guard = AnalysisConcurrencyGuard(max_concurrent=1)
        entered = threading.Event()
        finish_worker = threading.Event()

        def work():
            entered.set()
            if not finish_worker.wait(3):
                raise TimeoutError("test worker did not finish")
            return "completed"

        async def scenario():
            task = asyncio.create_task(guard.run_in_thread(work))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 1))

                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

                # Cancellation must not release the active worker's slot.
                self.assertFalse(guard.try_acquire())
            finally:
                finish_worker.set()

            # Wait until the worker's own cleanup has released the slot.
            for _ in range(100):
                if guard.try_acquire():
                    guard.release()
                    return
                await asyncio.sleep(0.01)

            self.fail("worker slot was not released")

        asyncio.run(scenario())
    def test_cancel_before_worker_starts_does_not_leak_slot(self):
        import asyncio

        guard = AnalysisConcurrencyGuard(max_concurrent=1)
        called = []

        def work():
            called.append(True)

        async def scenario():
            task = asyncio.create_task(guard.run_in_thread(work))
            task.cancel()

            with self.assertRaises(asyncio.CancelledError):
                await task

            self.assertEqual(called, [])
            self.assertTrue(guard.try_acquire())
            guard.release()

        asyncio.run(scenario())
    def test_cancel_after_slot_acquired_before_worker_starts(self):
        import asyncio
        import threading

        guard = AnalysisConcurrencyGuard(max_concurrent=1)
        occupied = threading.Event()
        release_pool = threading.Event()
        called = threading.Event()

        def blocker():
            occupied.set()
            if not release_pool.wait(3):
                raise TimeoutError("blocker did not finish")

        def work():
            called.set()
            return "completed"

        async def scenario():
            blocking_future = guard._executor.submit(blocker)
            task = None

            try:
                for _ in range(100):
                    if occupied.is_set():
                        break
                    await asyncio.sleep(0.01)

                self.assertTrue(occupied.is_set())

                task = asyncio.create_task(guard.run_in_thread(work))
                await asyncio.sleep(0.02)

                self.assertFalse(
                    guard.try_acquire(),
                    "worker slot was not acquired",
                )

                task.cancel()

                with self.assertRaises(asyncio.CancelledError):
                    await task

                self.assertFalse(called.is_set())

                self.assertTrue(
                    guard.try_acquire(),
                    "slot leaked when queued worker was cancelled",
                )
                guard.release()
            finally:
                release_pool.set()
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await asyncio.wrap_future(blocking_future)
                guard._executor.shutdown(wait=True)

        asyncio.run(scenario())
    def test_shutdown_rejects_new_work_without_leaking_slot(self):
        import asyncio

        guard = AnalysisConcurrencyGuard(max_concurrent=1)
        guard.shutdown()

        async def scenario():
            with self.assertRaises(RuntimeError):
                await guard.run_in_thread(lambda: "unexpected")

        asyncio.run(scenario())

        self.assertTrue(
            guard.try_acquire(),
            "slot leaked after executor rejected new work",
        )
        guard.release()
    def test_request_lease_holds_slot_until_closed(self):
        guard = AnalysisConcurrencyGuard(max_concurrent=1)

        try:
            lease = guard.acquire_request()

            self.assertFalse(
                guard.try_acquire(),
                "request lease must occupy the shared slot",
            )

            lease.close()

            self.assertTrue(
                guard.try_acquire(),
                "request lease did not return its slot",
            )
            guard.release()
        finally:
            guard.shutdown()
    def test_request_lease_waits_for_cancelled_worker_before_releasing(self):
        import asyncio
        import threading

        guard = AnalysisConcurrencyGuard(max_concurrent=1)
        entered = threading.Event()
        finish = threading.Event()
        lease = guard.acquire_request()

        def work():
            entered.set()
            if not finish.wait(3):
                raise TimeoutError("worker did not finish")
            return "completed"

        async def scenario():
            task = asyncio.create_task(lease.run_in_thread(work))
            try:
                for _ in range(100):
                    if entered.is_set():
                        break
                    await asyncio.sleep(0.01)

                self.assertTrue(entered.is_set())

                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

                lease.close()

                self.assertFalse(
                    guard.try_acquire(),
                    "slot released while cancelled worker is still running",
                )
            finally:
                finish.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

            for _ in range(100):
                if guard.try_acquire():
                    guard.release()
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("request slot was not released after worker finished")

        try:
            asyncio.run(scenario())
        finally:
            finish.set()
            lease.close()
            guard.shutdown()
    def test_request_lease_releases_slot_after_worker_exception(self):
        import asyncio

        guard = AnalysisConcurrencyGuard(max_concurrent=1)
        lease = guard.acquire_request()

        def fail():
            raise ValueError("expected_worker_failure")

        async def scenario():
            with self.assertRaisesRegex(
                ValueError, "expected_worker_failure"
            ):
                await lease.run_in_thread(fail)

            lease.close()

            self.assertTrue(
                guard.try_acquire(),
                "slot leaked after worker exception",
            )
            guard.release()

        try:
            asyncio.run(scenario())
        finally:
            lease.close()
            guard.shutdown()
    def test_request_lease_waits_for_all_workers_before_releasing(self):
        import asyncio
        import threading

        guard = AnalysisConcurrencyGuard(max_concurrent=1)
        lease = guard.acquire_request()
        first_done = threading.Event()
        second_started = threading.Event()
        finish_second = threading.Event()

        def first():
            first_done.set()
            return "first"

        def second():
            second_started.set()
            if not finish_second.wait(3):
                raise TimeoutError("second worker did not finish")
            return "second"

        async def scenario():
            tasks = [
                asyncio.create_task(lease.run_in_thread(first)),
                asyncio.create_task(lease.run_in_thread(second)),
            ]
            try:
                for _ in range(100):
                    if first_done.is_set() and second_started.is_set():
                        break
                    await asyncio.sleep(0.01)

                self.assertTrue(first_done.is_set())
                self.assertTrue(second_started.is_set())

                lease.close()

                self.assertFalse(
                    guard.try_acquire(),
                    "slot released while second worker is still running",
                )

                finish_second.set()
                self.assertEqual(
                    await asyncio.gather(*tasks),
                    ["first", "second"],
                )

                self.assertTrue(
                    guard.try_acquire(),
                    "slot not released after all workers finished",
                )
                guard.release()
            finally:
                finish_second.set()
                await asyncio.gather(*tasks, return_exceptions=True)

        try:
            asyncio.run(scenario())
        finally:
            finish_second.set()
            lease.close()
            guard.shutdown()
    def test_request_lease_cancel_queued_worker_releases_slot(self):
        import asyncio
        import threading

        guard = AnalysisConcurrencyGuard(max_concurrent=1)
        lease = guard.acquire_request()
        occupied = threading.Event()
        release_pool = threading.Event()
        called = threading.Event()

        def blocker():
            occupied.set()
            if not release_pool.wait(3):
                raise TimeoutError("blocker did not finish")

        def work():
            called.set()
            return "unexpected"

        async def scenario():
            blocking_future = guard._executor.submit(blocker)
            task = None

            try:
                for _ in range(100):
                    if occupied.is_set():
                        break
                    await asyncio.sleep(0.01)

                self.assertTrue(occupied.is_set())

                task = asyncio.create_task(lease.run_in_thread(work))
                await asyncio.sleep(0.02)

                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

                lease.close()

                self.assertFalse(called.is_set())
                self.assertTrue(
                    guard.try_acquire(),
                    "request slot leaked after queued worker cancellation",
                )
                guard.release()
            finally:
                release_pool.set()
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await asyncio.wrap_future(blocking_future)

        try:
            asyncio.run(scenario())
        finally:
            release_pool.set()
            lease.close()
            guard.shutdown()
    def test_request_lease_double_close_does_not_release_twice(self):
        guard = AnalysisConcurrencyGuard(max_concurrent=1)
        lease = guard.acquire_request()

        try:
            lease.close()
            lease.close()

            self.assertTrue(
                guard.try_acquire(),
                "request slot was not returned",
            )

            self.assertFalse(
                guard.try_acquire(),
                "double close released an extra slot",
            )

            guard.release()
        finally:
            lease.close()
            guard.shutdown()

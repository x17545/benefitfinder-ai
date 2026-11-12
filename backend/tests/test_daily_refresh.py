"""F08 sources fail independently; the journal and notification reveal no raw data."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import fcntl
from io import StringIO
import json
import multiprocessing
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
import unittest
from unittest.mock import Mock, patch

from benefitfinder.jobs.__main__ import main
from benefitfinder.jobs.daily_refresh import DailyRefreshError, run_daily_refresh

AT = datetime(2026, 11, 12, tzinfo=timezone.utc)


def _blocked_run(root: str, entered, release) -> None:
    def blocked() -> int:
        entered.set()
        release.wait(30)
        return 0

    run_daily_refresh(
        Path(root), refresh_policies=blocked, refresh_rate=lambda: 0, clock=lambda: AT
    )


class DailyRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.root = self.data / "processed" / "f08"

    def run_sources(self, policy=0, rate=0, notify=None) -> int:
        return run_daily_refresh(
            self.root,
            refresh_policies=lambda: policy,
            refresh_rate=lambda: rate,
            clock=lambda: AT,
            notify=notify,
        )

    def state(self) -> dict:
        return json.loads((self.root / "status.json").read_text())

    def test_configured_gov24_is_independent_and_reported_once(self) -> None:
        calls = []
        sent = []
        self.assertEqual(run_daily_refresh(self.root, refresh_policies=lambda: calls.append('youth') or 0,
            refresh_rate=lambda: calls.append('ecos') or 0,
            refresh_gov24=lambda: calls.append('gov24') or 0, clock=lambda: AT,
            notify=sent.append), 0)
        previous = self.state()['last_success']
        self.assertEqual(set(calls), {'youth', 'ecos', 'gov24'})
        self.assertEqual(len(sent), 1)
        self.assertIn('보조금24: 성공', sent[0])
        self.assertEqual(run_daily_refresh(self.root, refresh_policies=lambda: 0,
            refresh_rate=lambda: 0, refresh_gov24=lambda: 1, clock=lambda: AT,
            notify=sent.append), 1)
        self.assertEqual(self.state()['last_success'], previous)
        self.assertIn('보조금24: 실패', sent[1])
        self.assertEqual(self.state()['last_attempt']['stages']['ecos']['status'], 'success')

    def test_cli_prepares_both_sources_and_rejects_duplicate_canonical_policy(self) -> None:
        from backend.tests.gov24_fixtures import gov24_plan
        from backend.tests.test_policy_preparation import plan_data

        youth = self.data / 'youth.json'
        gov = self.data / 'gov.json'
        youth.write_text(json.dumps(plan_data()))
        for duplicate in (False, True):
            gov.write_text(json.dumps(gov24_plan('A' if duplicate else 'B')))
            with self.subTest(duplicate=duplicate), patch.dict(os.environ, {
                'F12_WRITE_DATABASE_URL': 'fictional', 'F12_PREPARATION_PLAN': str(youth),
                'GOV24_PREPARATION_PLAN': str(gov),
            }, clear=True), patch('benefitfinder.jobs.__main__._refresh_youthcenter', return_value=0), \
                 patch('benefitfinder.jobs.__main__.refresh_ecos_rate_command', return_value=0), \
                 patch('benefitfinder.jobs.__main__.refresh_gov24_command', return_value=0) as collect, \
                 patch('benefitfinder.jobs.__main__.prepare_f12_inputs', return_value={
                    'targets': 1, 'registered': 1, 'replayed': 0, 'failed': 0,
                 }) as prepare, patch('benefitfinder.jobs.__main__.run_f12_queue', return_value={
                    'attempted': 0, 'needs_review': 0, 'stale': 0, 'failed': 0,
                    'source_records_awaiting_scope_or_documents': 0,
                 }), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                code = main(['refresh-data', '--data-dir', str(self.data)])
                self.assertEqual(code, 1 if duplicate else 0)
                collect.assert_called_once_with(self.data, gov)
                self.assertEqual(prepare.call_count, 0 if duplicate else 2)
                if not duplicate:
                    stage = self.state()['last_attempt']['stages']['f12_preparation']
                    self.assertEqual(stage['counts']['registered'], 2)

    def test_records_source_results_without_receiving_profiles_or_source_payloads(self) -> None:
        sent = []
        self.assertEqual(self.run_sources(notify=sent.append), 0)
        state = self.state()
        attempt = state["last_attempt"]
        self.assertEqual(attempt["data_status"], "success")
        self.assertEqual(attempt["notification_status"], "sent")
        self.assertEqual(state["last_success"]["run_id"], attempt["run_id"])
        self.assertEqual(set(attempt["stages"]), {"youthcenter", "ecos"})
        self.assertEqual(len(sent), 1)
        self.assertIn("온통청년: 성공", sent[0])
        self.assertIn("ECOS 기준금리: 성공", sent[0])
        self.assertEqual((self.root / "status.json").stat().st_mode & 0o777, 0o600)

    def test_each_failure_preserves_last_full_success_and_still_attempts_the_other_source(
        self,
    ) -> None:
        self.assertEqual(self.run_sources(), 0)
        previous = self.state()["last_success"]
        for failed in ("youthcenter", "ecos"):
            with self.subTest(failed=failed):
                policies = Mock(return_value=1 if failed == "youthcenter" else 0)
                rates = Mock(return_value=2 if failed == "ecos" else 0)
                self.assertEqual(
                    run_daily_refresh(
                        self.root,
                        refresh_policies=policies,
                        refresh_rate=rates,
                        clock=lambda: AT,
                    ),
                    1,
                )
                policies.assert_called_once_with()
                rates.assert_called_once_with()
                self.assertEqual(self.state()["last_success"], previous)
                self.assertEqual(self.state()["last_attempt"]["stages"][failed]["status"], "failed")

    def test_exception_text_and_invalid_exit_codes_are_not_reported_or_serialized(self) -> None:
        sent = []
        policies = Mock(side_effect=RuntimeError("PRIVATE KEY source text profile"))
        rates = Mock(return_value=True)
        self.assertEqual(
            run_daily_refresh(
                self.root,
                refresh_policies=policies,
                refresh_rate=rates,
                clock=lambda: AT,
                notify=sent.append,
            ),
            1,
        )
        self.assertNotIn("PRIVATE", (self.root / "status.json").read_text() + sent[0])
        self.assertEqual(self.state()["last_attempt"]["stages"]["ecos"]["exit_code"], 1)
        rates.assert_called_once_with()
        self.assertIsNone(self.state()["last_success"])

    def test_slow_policy_source_does_not_delay_rate_source_start(self) -> None:
        rate_started = Event()

        def policy() -> int:
            self.assertTrue(rate_started.wait(2), "rate source was blocked by policy collection")
            return 0

        def rate() -> int:
            rate_started.set()
            return 0

        self.assertEqual(
            run_daily_refresh(
                self.root, refresh_policies=policy, refresh_rate=rate, clock=lambda: AT
            ),
            0,
        )

    def test_preparation_runs_after_sources_and_before_pending_analysis(self) -> None:
        events = []
        completed = set()
        lock = Lock()

        def source(name: str, code: int):
            def refresh() -> int:
                with lock:
                    events.append(f"{name}_start")
                    events.append(f"{name}_end")
                    completed.add(name)
                return code

            return refresh

        def prepare() -> dict[str, int]:
            with lock:
                self.assertEqual(completed, {"youthcenter", "ecos"})
                events.append("prepare")
            return {"targets": 2, "registered": 1, "replayed": 1, "failed": 0}

        def analyze() -> int:
            with lock:
                self.assertEqual(events[-1], "prepare")
                events.append("analyze")
            return 0

        self.assertEqual(
            run_daily_refresh(
                self.root,
                refresh_policies=source("youthcenter", 0),
                refresh_rate=source("ecos", 0),
                clock=lambda: AT,
                prepare_analysis=prepare,
                analyze_pending=analyze,
            ),
            0,
        )
        self.assertEqual(events[-1], "analyze")
        self.assertEqual(
            self.state()["last_attempt"]["stages"]["f12_preparation"]["counts"],
            {"targets": 2, "registered": 1, "replayed": 1, "failed": 0},
        )

    def test_source_failure_does_not_skip_preparation_or_pending_analysis(self) -> None:
        self.assertEqual(self.run_sources(), 0)
        previous = self.state()["last_success"]
        sent = []
        prepare = Mock(
            return_value={"targets": 2, "registered": 1, "replayed": 1, "failed": 0}
        )
        analyze = Mock(return_value=0)
        self.assertEqual(
            run_daily_refresh(
                self.root,
                refresh_policies=lambda: 1,
                refresh_rate=lambda: 2,
                clock=lambda: AT,
                notify=sent.append,
                prepare_analysis=prepare,
                analyze_pending=analyze,
            ),
            1,
        )
        prepare.assert_called_once_with()
        analyze.assert_called_once_with()
        state = self.state()
        self.assertEqual(
            state["last_attempt"]["stages"]["f12_preparation"]["status"], "success"
        )
        self.assertEqual(state["last_attempt"]["stages"]["f12"]["status"], "success")
        self.assertEqual(state["last_success"], previous)
        self.assertEqual(len(sent), 1)
        self.assertIn("F12 입력 준비: 성공", sent[0])

    def test_preparation_error_is_sanitized_and_does_not_skip_pending_analysis(self) -> None:
        self.assertEqual(self.run_sources(), 0)
        previous = self.state()["last_success"]
        sent = []
        prepare = Mock(side_effect=RuntimeError("PRIVATE https://example.test 원문"))
        analyze = Mock(return_value=0)
        self.assertEqual(
            run_daily_refresh(
                self.root,
                refresh_policies=lambda: 0,
                refresh_rate=lambda: 0,
                clock=lambda: AT,
                notify=sent.append,
                prepare_analysis=prepare,
                analyze_pending=analyze,
            ),
            1,
        )
        analyze.assert_called_once_with()
        state = self.state()
        stage = state["last_attempt"]["stages"]["f12_preparation"]
        self.assertEqual(stage["status"], "failed")
        self.assertEqual(stage["exit_code"], 1)
        self.assertNotIn("counts", stage)
        self.assertEqual(state["last_success"], previous)
        self.assertEqual(len(sent), 1)
        combined = (self.root / "status.json").read_text() + sent[0]
        for secret in ("PRIVATE", "https://", "원문"):
            self.assertNotIn(secret, combined)

    def test_partial_preparation_denies_success_but_still_runs_pending_analysis(self) -> None:
        self.assertEqual(self.run_sources(), 0)
        previous = self.state()["last_success"]
        sent = []
        counts = {"targets": 4, "registered": 2, "replayed": 1, "failed": 1}
        analysis_counts = {
            "attempted": 3,
            "needs_review": 1,
            "stale": 0,
            "failed": 0,
            "source_records_awaiting_scope_or_documents": 2,
        }
        prepare = Mock(return_value=counts)
        analyze = Mock(return_value=analysis_counts)
        self.assertEqual(
            run_daily_refresh(
                self.root,
                refresh_policies=lambda: 0,
                refresh_rate=lambda: 0,
                clock=lambda: AT,
                notify=sent.append,
                prepare_analysis=prepare,
                analyze_pending=analyze,
            ),
            1,
        )
        analyze.assert_called_once_with()
        state = self.state()
        stages = state["last_attempt"]["stages"]
        self.assertEqual(stages["f12_preparation"]["status"], "failed")
        self.assertEqual(stages["f12_preparation"]["counts"], counts)
        self.assertEqual(stages["f12"]["status"], "success")
        self.assertEqual(stages["f12"]["counts"], analysis_counts)
        self.assertEqual(state["last_success"], previous)
        self.assertEqual(len(sent), 1)
        self.assertIn("F12 입력 준비: 실패", sent[0])
        self.assertIn("F12 입력 준비 대상: 4건", sent[0])
        self.assertIn("F12 입력 준비 등록: 2건", sent[0])
        self.assertIn("F12 입력 준비 재사용: 1건", sent[0])
        self.assertIn("F12 입력 준비 실패: 1건", sent[0])

    def test_invalid_preparation_counts_are_fixed_failures_without_leaking_values(self) -> None:
        self.assertEqual(self.run_sources(), 0)
        previous = self.state()["last_success"]
        invalid_results = (
            None,
            {"targets": 2, "registered": 1, "replayed": 0},
            {"targets": 3, "registered": 1, "replayed": 1, "failed": 0},
            {"targets": True, "registered": 1, "replayed": 0, "failed": 0},
            {"targets": -1, "registered": -1, "replayed": 0, "failed": 0},
            {"targets": 2**63, "registered": 2**63, "replayed": 0, "failed": 0},
            {
                "targets": 1,
                "registered": 1,
                "replayed": 0,
                "failed": 0,
                "PRIVATE": "https://example.test 원문",
            },
        )
        for invalid in invalid_results:
            with self.subTest(invalid=invalid):
                sent = []
                analyze = Mock(return_value=0)
                self.assertEqual(
                    run_daily_refresh(
                        self.root,
                        refresh_policies=lambda: 0,
                        refresh_rate=lambda: 0,
                        clock=lambda: AT,
                        notify=sent.append,
                        prepare_analysis=Mock(return_value=invalid),
                        analyze_pending=analyze,
                    ),
                    1,
                )
                analyze.assert_called_once_with()
                state = self.state()
                stage = state["last_attempt"]["stages"]["f12_preparation"]
                self.assertEqual(stage["status"], "failed")
                self.assertNotIn("counts", stage)
                self.assertEqual(state["last_success"], previous)
                self.assertEqual(len(sent), 1)
                combined = (self.root / "status.json").read_text() + sent[0]
                for secret in ("PRIVATE", "https://", "원문"):
                    self.assertNotIn(secret, combined)

    def test_notification_failure_does_not_roll_back_data_or_restart_sources(self) -> None:
        notifier = Mock(side_effect=RuntimeError("PRIVATE WEBHOOK"))
        self.assertEqual(self.run_sources(notify=notifier), 1)
        state = self.state()
        self.assertEqual(state["last_attempt"]["data_status"], "success")
        self.assertEqual(state["last_attempt"]["notification_status"], "failed")
        self.assertIsNotNone(state["last_success"])
        notifier.assert_called_once()
        self.assertNotIn("PRIVATE", (self.root / "status.json").read_text())

    def test_lock_rejects_duplicate_execution_without_overwriting_journal(self) -> None:
        self.assertEqual(self.run_sources(), 0)
        before = (self.root / "status.json").read_bytes()
        with (self.root / ".refresh-lock").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            policy = Mock()
            with self.assertRaisesRegex(DailyRefreshError, "^daily_refresh_already_running$"):
                run_daily_refresh(
                    self.root, refresh_policies=policy, refresh_rate=lambda: 0, clock=lambda: AT
                )
            policy.assert_not_called()
        self.assertEqual((self.root / "status.json").read_bytes(), before)
        self.assertEqual(self.run_sources(), 0)

    def test_killed_job_leaves_running_evidence_and_releases_os_lock(self) -> None:
        context = multiprocessing.get_context("spawn")
        entered = context.Event()
        release = context.Event()
        process = context.Process(target=_blocked_run, args=(str(self.root), entered, release))
        process.start()
        try:
            self.assertTrue(entered.wait(5), "test child did not start")
            self.assertEqual(self.state()["last_attempt"]["data_status"], "running")
            self.assertIsNone(self.state()["last_success"])
        finally:
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
        self.assertEqual(self.run_sources(), 0)

    def test_corrupt_or_oversized_previous_journal_blocks_unrecorded_work(self) -> None:
        self.root.mkdir(parents=True)
        path = self.root / "status.json"
        for content in ('{"format_version":1,"last_success":{"PRIVATE":"secret"}}', "x" * 65_537):
            path.write_text(content)
            policy = Mock()
            with self.assertRaisesRegex(DailyRefreshError, "^daily_refresh_state_invalid$"):
                run_daily_refresh(
                    self.root, refresh_policies=policy, refresh_rate=lambda: 0, clock=lambda: AT
                )
            policy.assert_not_called()
            self.assertEqual(path.read_text(), content)

    def test_failed_initial_journal_write_does_not_start_sources(self) -> None:
        self.assertEqual(self.run_sources(), 0)
        before = (self.root / "status.json").read_bytes()
        policies = Mock()
        with patch(
            "benefitfinder.jobs.daily_refresh.os.replace", side_effect=OSError("PRIVATE path")
        ):
            with self.assertRaisesRegex(DailyRefreshError, "^daily_refresh_io_error$"):
                run_daily_refresh(
                    self.root, refresh_policies=policies, refresh_rate=lambda: 0, clock=lambda: AT
                )
        policies.assert_not_called()
        self.assertEqual((self.root / "status.json").read_bytes(), before)
        self.assertEqual(list(self.root.glob(".daily-status-*")), [])

    def test_final_journal_failure_is_reported_without_repeating_successful_sources(self) -> None:
        sent = []
        policies, rates = Mock(return_value=0), Mock(return_value=0)
        replace = os.replace
        fail_once = True

        def fail_first_completion(source, target) -> None:
            nonlocal fail_once
            if (
                fail_once
                and json.loads(Path(source).read_text())["last_attempt"]["data_status"] == "success"
            ):
                fail_once = False
                raise OSError("PRIVATE disk")
            replace(source, target)

        with patch(
            "benefitfinder.jobs.daily_refresh.os.replace", side_effect=fail_first_completion
        ):
            code = run_daily_refresh(
                self.root,
                refresh_policies=policies,
                refresh_rate=rates,
                clock=lambda: AT,
                notify=sent.append,
            )
        self.assertEqual(code, 1)
        policies.assert_called_once_with()
        rates.assert_called_once_with()
        self.assertEqual(len(sent), 1)
        self.assertIn("daily_refresh_io_error", sent[0])
        self.assertEqual(self.state()["last_attempt"]["record_status"], "failed")

    def test_cli_connects_both_sources_and_sends_one_aggregate_notification(self) -> None:
        output = StringIO()
        with (
            patch("benefitfinder.jobs.__main__._refresh_youthcenter", return_value=0) as policies,
            patch(
                "benefitfinder.jobs.__main__.refresh_ecos_rate_command",
                return_value=2,
            ) as rates,
            patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "PRIVATE WEBHOOK"}),
            patch(
                "benefitfinder.jobs.__main__.DiscordWebhook",
            ) as webhook,
            redirect_stdout(output),
            redirect_stderr(output),
        ):
            self.assertEqual(
                main(["refresh-data", "--data-dir", str(self.data), "--notify-discord"]), 1
            )
        policies.assert_called_once_with(self.data)
        rates.assert_called_once_with()
        webhook.return_value.send.assert_called_once()
        message = webhook.return_value.send.call_args.args[0]
        self.assertIn("ECOS 기준금리: 실패", message)
        self.assertNotIn("PRIVATE", message + output.getvalue())

    def test_cli_notification_is_optional_and_missing_webhook_is_an_explicit_failure(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch(
                "benefitfinder.jobs.__main__._refresh_youthcenter",
                return_value=0,
            ),
            patch("benefitfinder.jobs.__main__.refresh_ecos_rate_command", return_value=0),
            patch(
                "benefitfinder.jobs.__main__.DiscordWebhook",
            ) as webhook,
            redirect_stdout(StringIO()),
            redirect_stderr(StringIO()),
        ):
            self.assertEqual(main(["refresh-data", "--data-dir", str(self.data)]), 0)
            webhook.assert_not_called()
            self.assertEqual(
                main(["refresh-data", "--data-dir", str(self.data), "--notify-discord"]), 1
            )
        self.assertEqual(self.state()["last_attempt"]["notification_status"], "failed")

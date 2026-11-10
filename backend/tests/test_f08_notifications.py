"""F08 status notifications use aggregate metadata, never source text or credentials."""

import json
import os
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from benefitfinder.infrastructure.discord_webhook import DiscordWebhook, DiscordWebhookError
from benefitfinder.jobs.__main__ import refresh_youthcenter_command
from benefitfinder.jobs.youthcenter_refresh import refresh_notification

AT = datetime(2026, 11, 7, tzinfo=timezone.utc)
WEBHOOK = "https://discord.com/api/webhooks/123456/virtual-token-not-real"


class DiscordWebhookTests(TestCase):
    def test_uses_confirmed_delivery_and_disables_mentions(self) -> None:
        connection = Mock()
        connection.getresponse.return_value.status = 200
        with patch(
            "benefitfinder.infrastructure.discord_webhook.HTTPSConnection", return_value=connection
        ):
            DiscordWebhook(WEBHOOK).send("가상 갱신 결과")
        method, path, body, headers = connection.request.call_args.args
        self.assertEqual(method, "POST")
        self.assertTrue(path.endswith("?wait=true"))
        self.assertEqual(
            json.loads(body), {"content": "가상 갱신 결과", "allowed_mentions": {"parse": []}}
        )
        connection.close.assert_called_once()

    def test_rejects_blank_and_repeated_thread_ids(self) -> None:
        for query in (
            "thread_id=",
            "thread_id=123&thread_id=456",
            "thread_id=&thread_id=123",
        ):
            with self.subTest(query=query):
                with self.assertRaisesRegex(
                    DiscordWebhookError, "^discord_webhook_invalid$"
                ):
                    DiscordWebhook(WEBHOOK + "?" + query)

    def test_refuses_other_hosts_and_does_not_expose_webhook_on_delivery_failure(self) -> None:
        with self.assertRaisesRegex(DiscordWebhookError, "^discord_webhook_invalid$"):
            DiscordWebhook(WEBHOOK.replace("discord.com", "example.invalid"))
        for status in (302, 429, 500):
            with self.subTest(status=status):
                connection = Mock()
                connection.getresponse.return_value.status = status
                with (
                    patch(
                        "benefitfinder.infrastructure.discord_webhook.HTTPSConnection",
                        return_value=connection,
                    ),
                    self.assertRaisesRegex(DiscordWebhookError, "^discord_notification_failed$"),
                ):
                    DiscordWebhook(WEBHOOK).send("가상 갱신 결과")
        connection = Mock()
        connection.request.side_effect = OSError(WEBHOOK)
        with (
            patch(
                "benefitfinder.infrastructure.discord_webhook.HTTPSConnection",
                return_value=connection,
            ),
            self.assertRaisesRegex(DiscordWebhookError, "^discord_notification_failed$"),
        ):
            DiscordWebhook(WEBHOOK).send("가상 갱신 결과")


class NotificationCommandTests(TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.root = self.data / "processed" / "f08-youthcenter"
        self.root.mkdir(parents=True)

    def test_summary_drops_source_data_and_reports_current_success_or_failure(self) -> None:
        for code, outcome in ((0, "success"), (1, "failed")):
            with self.subTest(outcome=outcome):
                payload = {
                    "last_success": {"record_count": 2},
                    "last_attempt": {
                        "started_at": AT.isoformat(),
                        "status": outcome,
                        "attempts": 1,
                        "changes": {"new": 1, "changed": 1, "virtual-secret": 2},
                        "error_codes": ["http_status", "virtual-secret"],
                        "body": "virtual-secret",
                    },
                }
                (self.root / "status.json").write_text(json.dumps(payload), encoding="utf-8")
                message = refresh_notification(self.root, code, AT)
                self.assertIn("성공" if code == 0 else "실패", message)
                self.assertNotIn("virtual-secret", message)
                if code == 0:
                    self.assertIn("수집: 2건", message)
                else:
                    self.assertIn("http_status", message)
                    self.assertNotIn("수집: 2건", message)

    def test_missing_api_key_still_sends_failure_and_keeps_nonzero_exit(self) -> None:
        stderr = StringIO()
        with (
            patch.dict(os.environ, {"YOUTHCENTER_API_KEY": "", "DISCORD_WEBHOOK_URL": WEBHOOK}),
            patch("benefitfinder.jobs.__main__.DiscordWebhook") as notifier,
            redirect_stdout(StringIO()),
            redirect_stderr(stderr),
        ):
            code = refresh_youthcenter_command(self.data, notify_discord=True)
        self.assertEqual(code, 2)
        self.assertIn("실패", notifier.return_value.send.call_args.args[0])
        self.assertNotIn("virtual-token-not-real", stderr.getvalue())

    def test_stale_success_is_not_reported_as_current_failure_details(self) -> None:
        state = {
            "last_success": {"record_count": 999},
            "last_attempt": {
                "started_at": "2026-11-06T00:00:00+00:00",
                "status": "success",
                "attempts": 2,
            },
        }
        (self.root / "status.json").write_text(json.dumps(state), encoding="utf-8")
        message = refresh_notification(self.root, 1, AT)
        self.assertIn("실패", message)
        self.assertNotIn("999", message)
        self.assertNotIn("시도:", message)

    def test_notification_failure_does_not_turn_collection_into_another_attempt(self) -> None:
        stderr = StringIO()
        with (
            patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": WEBHOOK}),
            patch("benefitfinder.jobs.__main__._refresh_youthcenter", return_value=0) as refresh,
            patch("benefitfinder.jobs.__main__.DiscordWebhook") as notifier,
            redirect_stderr(stderr),
        ):
            notifier.return_value.send.side_effect = DiscordWebhookError(
                "discord_notification_failed"
            )
            code = refresh_youthcenter_command(self.data, notify_discord=True)
        self.assertEqual(code, 1)
        refresh.assert_called_once()
        self.assertIn("discord_notification_failed", stderr.getvalue())

"""Offline refresh commits only successful observations and prints no credentials."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
import io
import unittest
from unittest.mock import patch

from benefitfinder.application.base_rates import BaseRateObservation, BaseRateUnavailable
from benefitfinder.jobs.__main__ import main


class BaseRateJobTest(unittest.TestCase):
    def run_refresh(self) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stderr(out), redirect_stdout(out):
            code = main(["refresh-ecos-rate"])
        return code, out.getvalue()

    def test_missing_settings_do_not_fetch_or_write(self) -> None:
        with patch.dict("os.environ", {}, clear=True), patch(
            "benefitfinder.jobs.__main__.EcosBaseRate",
        ) as source:
            code, output = self.run_refresh()
        self.assertEqual(code, 2)
        source.assert_not_called()
        self.assertIn("마지막 성공값은 유지", output)

    def test_success_saves_exact_observation_and_failure_does_not_touch_storage(self) -> None:
        observation = BaseRateObservation(0.025, date(2026, 11, 1),
                                          datetime(2026, 11, 2, tzinfo=timezone.utc))
        with patch.dict("os.environ", {"ECOS_API_KEY": "PRIVATEKEY", "BASE_RATE_WRITE_DATABASE_URL": "PRIVATEDSN"}), patch(
            "benefitfinder.jobs.__main__.EcosBaseRate",
        ) as source, patch("benefitfinder.jobs.__main__.PostgresBaseRates") as storage:
            source.return_value.fetch.return_value = observation
            code, output = self.run_refresh()
            self.assertEqual(code, 0)
            storage.return_value.save.assert_called_once_with(observation)
            source.return_value.fetch.side_effect = BaseRateUnavailable("PRIVATEKEY PRIVATEDSN")
            storage.reset_mock()
            code, failure = self.run_refresh()
            self.assertEqual(code, 2)
            storage.assert_not_called()
            self.assertNotIn("PRIVATE", output + failure)

    def test_storage_failure_is_reported_with_fixed_safe_message(self) -> None:
        with patch.dict("os.environ", {"ECOS_API_KEY": "PRIVATEKEY", "BASE_RATE_WRITE_DATABASE_URL": "PRIVATEDSN"}), patch(
            "benefitfinder.jobs.__main__.EcosBaseRate",
        ), patch("benefitfinder.jobs.__main__.PostgresBaseRates") as storage:
            storage.return_value.save.side_effect = BaseRateUnavailable("PRIVATEDSN")
            code, output = self.run_refresh()
        self.assertEqual(code, 2)
        self.assertNotIn("PRIVATE", output)

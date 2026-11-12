"""Unit tests for the bounded ECOS base-rate adapter and its application model."""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest import TestCase
from unittest.mock import Mock, patch

from benefitfinder.application.base_rates import BaseRateObservation, BaseRateUnavailable
from benefitfinder.infrastructure.ecos_base_rate import MAX_RESPONSE_BYTES, EcosBaseRate

TODAY = date(2026, 11, 12)
CHECKED_AT = datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)
EXPECTED_PATH = "/api/StatisticSearch/sample/json/kr/1/1000/722Y001/D/20261013/20261112/0101000"
MODULE = "benefitfinder.infrastructure.ecos_base_rate"


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = io.BytesIO(body)
        self.read_amounts: list[int] = []

    def read(self, amount: int) -> bytes:
        self.read_amounts.append(amount)
        return self._body.read(amount)


def _row(day: str = "20261110", value: str = "3", **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "STAT_CODE": "722Y001",
        "ITEM_CODE1": "0101000",
        "ITEM_CODE2": None,
        "ITEM_CODE3": None,
        "ITEM_CODE4": None,
        "UNIT_NAME": "연%",
        "TIME": day,
        "DATA_VALUE": value,
    }
    row.update(changes)
    return row


def _payload(rows: list[dict[str, object]], *, total: int | None = None) -> dict[str, object]:
    return {
        "StatisticSearch": {
            "list_total_count": len(rows) if total is None else total,
            "row": rows,
        }
    }


def _raw(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class EcosBaseRateTests(TestCase):
    @contextmanager
    def _source(self, raw: bytes, *, status: int = 200):
        response = _Response(status, raw)
        connection = Mock()
        connection.getresponse.return_value = response
        source = EcosBaseRate("sample", today=lambda: TODAY, now=lambda: CHECKED_AT)
        with patch(f"{MODULE}.HTTPSConnection", return_value=connection) as constructor:
            yield source, constructor, connection, response

    def _assert_failure(self, raw: bytes, *, status: int = 200) -> None:
        with self._source(raw, status=status) as (source, constructor, connection, _response):
            with self.assertRaisesRegex(
                BaseRateUnavailable, "^ecos_base_rate_unavailable$"
            ) as raised:
                source.fetch()
        self.assertNotIn("sample", str(raised.exception))
        self.assertNotIn("ecos.bok.or.kr", str(raised.exception))
        constructor.assert_called_once_with("ecos.bok.or.kr", timeout=10)
        connection.close.assert_called_once()

    def test_fetch_uses_fixed_path_timeout_and_latest_ecos_date(self) -> None:
        raw = _raw(
            _payload(
                [
                    _row("20261110", "3.25"),
                    _row("20261112", "0"),
                    _row("20261111", "2.75"),
                ]
            )
        )
        with self._source(raw) as (source, constructor, connection, response):
            observation = source.fetch()

        self.assertEqual(
            observation,
            BaseRateObservation(
                annual_rate=0.0, observed_on=date(2026, 11, 12), checked_at=CHECKED_AT
            ),
        )
        constructor.assert_called_once_with("ecos.bok.or.kr", timeout=10)
        connection.request.assert_called_once_with(
            "GET",
            EXPECTED_PATH,
            headers={"Accept": "application/json", "User-Agent": "benefitfinder-ai/0.1"},
        )
        self.assertEqual(response.read_amounts, [MAX_RESPONSE_BYTES + 1])
        connection.close.assert_called_once()

    def test_every_row_must_be_the_base_rate_daily_series(self) -> None:
        missing_item_code = _row()
        missing_item_code.pop("ITEM_CODE2")
        cases = {
            "wrong_stat": _payload([_row(STAT_CODE="0000000")]),
            "wrong_item": _payload([_row(ITEM_CODE1="9999999")]),
            "missing_null_item_code": _payload([missing_item_code]),
            "wrong_unit": _payload([_row(UNIT_NAME="%")]),
            "wrong_cycle": _payload([_row(CYCLE="M")]),
            "future_value": _payload([_row("20261113")]),
            "outside_window": _payload([_row("20261012")]),
            "one_invalid_row": _payload([_row("20261111"), _row("20261112", UNIT_NAME="won")]),
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                self._assert_failure(_raw(payload))

    def test_missing_rows_total_mismatch_conflicts_and_result_errors_fail(self) -> None:
        cases = {
            "empty": _payload([], total=0),
            "missing_rows": {"StatisticSearch": {"list_total_count": 1}},
            "total_too_large": _payload([_row()], total=2),
            "total_too_small": _payload([_row("20261110"), _row("20261111")], total=1),
            "conflicting_duplicate_date": _payload(
                [_row("20261111", "2.5"), _row("20261111", "2.75")]
            ),
            "quoted_nan": _payload([_row(value="NaN")]),
            "quoted_infinity": _payload([_row(value="Infinity")]),
            "result_error": {"RESULT": {"CODE": "ERROR-100", "MESSAGE": "virtual response body"}},
        }
        for name, payload in cases.items():
            with self.subTest(name=name):
                self._assert_failure(_raw(payload))
        self._assert_failure(_raw(_payload([_row()])), status=503)

    def test_duplicate_keys_and_nonfinite_json_numbers_fail(self) -> None:
        valid = _raw(_payload([_row(value="0")]))
        duplicate = valid.replace(
            b'"StatisticSearch":',
            b'"StatisticSearch":{"duplicate":1},"StatisticSearch":',
            1,
        )
        nan = valid.replace(b'"DATA_VALUE": "0"', b'"DATA_VALUE": NaN', 1)
        infinity = valid.replace(b'"DATA_VALUE": "0"', b'"DATA_VALUE": Infinity', 1)
        for name, raw in {
            "duplicate_key": duplicate,
            "nan": nan,
            "infinity": infinity,
        }.items():
            with self.subTest(name=name):
                self._assert_failure(raw)

    def test_oversized_response_is_rejected_and_connection_is_closed(self) -> None:
        with self._source(b"x" * (MAX_RESPONSE_BYTES + 1)) as (
            source,
            _constructor,
            connection,
            response,
        ):
            with self.assertRaisesRegex(BaseRateUnavailable, "^ecos_base_rate_unavailable$"):
                source.fetch()
        self.assertEqual(response.read_amounts, [MAX_RESPONSE_BYTES + 1])
        connection.close.assert_called_once()

    def test_timeout_is_fixed_and_connection_is_closed(self) -> None:
        connection = Mock()
        connection.request.side_effect = TimeoutError(
            "sample https://ecos.bok.or.kr/api body=virtual-response"
        )
        source = EcosBaseRate("sample", today=lambda: TODAY, now=lambda: CHECKED_AT)
        with patch(f"{MODULE}.HTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(
                BaseRateUnavailable, "^ecos_base_rate_unavailable$"
            ) as raised:
                source.fetch()
        message = str(raised.exception)
        self.assertNotIn("sample", message)
        self.assertNotIn("ecos.bok.or.kr", message)
        self.assertNotIn("virtual-response", message)
        connection.close.assert_called_once()

    def test_repr_and_key_validation_do_not_expose_the_key(self) -> None:
        source = EcosBaseRate("sample", today=lambda: TODAY, now=lambda: CHECKED_AT)
        self.assertEqual(repr(source), "EcosBaseRate(api_key=***)")
        self.assertNotIn("sample", repr(source))

        invalid_key = "sample-secret"
        with self.assertRaisesRegex(BaseRateUnavailable, "^ecos_base_rate_unavailable$") as raised:
            EcosBaseRate(invalid_key)
        self.assertNotIn(invalid_key, str(raised.exception))


class BaseRateObservationTests(TestCase):
    def test_rate_rejects_bool_nonfinite_negative_and_above_one(self) -> None:
        for annual_rate in (True, False, float("nan"), float("inf"), -0.01, 1.01):
            with self.subTest(annual_rate=annual_rate):
                with self.assertRaisesRegex(ValueError, "^base_rate_annual_rate_invalid$"):
                    BaseRateObservation(annual_rate, TODAY, CHECKED_AT)

    def test_checked_at_requires_a_timezone(self) -> None:
        with self.assertRaisesRegex(ValueError, "^base_rate_checked_at_invalid$"):
            BaseRateObservation(0.03, TODAY, datetime(2026, 11, 12, 3, 0))

    def test_observed_date_is_compared_in_kst(self) -> None:
        with self.assertRaisesRegex(ValueError, "^base_rate_observed_on_future$"):
            BaseRateObservation(
                0.03,
                date(2026, 11, 13),
                datetime(2026, 11, 12, 14, 59, tzinfo=timezone.utc),
            )
        self.assertEqual(
            BaseRateObservation(
                0.03,
                date(2026, 11, 13),
                datetime(2026, 11, 12, 15, 0, tzinfo=timezone.utc),
            ).observed_on,
            date(2026, 11, 13),
        )

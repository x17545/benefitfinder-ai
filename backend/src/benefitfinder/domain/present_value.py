
"""검수된 지급·적립 일정의 기간 금액과 현재가치 계산."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def calculate_present_value(
    payments: list[tuple[int, int]],
    annual_rate: float,
) -> int:
    """(지급 월, 지급액) 목록의 현재가치를 원 단위로 계산한다."""

    if annual_rate < 0:
        raise ValueError("annual_rate must be non-negative")

    total = Decimal("0")
    rate = Decimal(str(annual_rate))

    for month, amount in payments:
        if month < 0 or amount < 0:
            raise ValueError("Payment month and amount must be non-negative")

        # 월 단위 지급 시점을 연 단위 할인 기간으로 환산
        years = Decimal(month) / Decimal(12)

        discounted = Decimal(amount) / (
            (Decimal(1) + rate) ** years
        )

        total += discounted

    return int(
        total.quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def calculate_period_value(
    payments: list[tuple[int, int]],
    period: str,
    annual_rate: float | None = None,
) -> int:
    """선택 기간 안에 지급되는 금액만 계산한다.

    annual_rate=None이면 할인 전 금액을 반환한다.
    """

    period_months = {
        "1y": 12,
        "2y": 24,
        "all": None,
    }

    if period not in period_months:
        raise ValueError("Unsupported period")

    cutoff = period_months[period]

    selected = [
        (month, amount)
        for month, amount in payments
        if cutoff is None or month <= cutoff
    ]

    if annual_rate is None:
        return sum(amount for _, amount in selected)

    return calculate_present_value(selected, annual_rate)


def calculate_support_value(
    payments: list[tuple[int, int]],
    period: str,
    annual_rate: float | None = None,
    *,
    maturity_accruals: list[tuple[int, int]] | None = None,
) -> int:
    """명목 합계는 검수된 적립분, PV 합계는 기간 내 실제 지급만 반영한다."""
    if maturity_accruals is not None and annual_rate is None:
        return calculate_period_value(maturity_accruals, period)
    return calculate_period_value(payments, period, annual_rate)


def maturity_breakdown(
    payments: list[tuple[int, int]],
    accruals: list[tuple[int, int]],
    period: str,
) -> dict[str, int]:
    """현금 지급 예정액(할인 전)과 아직 지급되지 않은 예상 적립액을 분리한다.

    게시 검증은 단일 만기 지급과 같은 총액의 적립 일정만 허용한다.
    """
    cash = calculate_period_value(payments, period)
    accrued = calculate_period_value(accruals, period)
    if cash > accrued:
        raise ValueError("maturity_cash_exceeds_accrual")
    return {"cash_in_period_won": cash, "expected_accrual_won": accrued - cash}

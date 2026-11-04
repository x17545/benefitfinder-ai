
"""가상 정책 지원금의 현재가치 계산."""

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

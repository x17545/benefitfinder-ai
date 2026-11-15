"""Fictional, uneven support accruals; never real policy or user data."""

import hashlib

from backend.tests.catalog_fixtures import release_data


def maturity_release(key: str = "M") -> tuple[dict, str]:
    data = release_data(key)
    quote = (
        "[가상] 본인 납입금을 제외한 기관 지원금은 1개월에 10만원, 12개월에 20만원, "
        "24개월에 30만원, 36개월에 40만원씩 적립되고 36개월 만기에 합계 100만원을 지급합니다."
    )
    text = "[가상] 만 19~34세를 지원합니다.\n" + quote
    data["benefit"]["benefit_amount"].update(
        type="maturity", pay_timing="at_maturity", period_months=36,
    )
    data["payments"] = [{"month": 36, "amount_won": 1_000_000}]
    evidence = [{
        "quote": quote, "location": "가상 지원 적립·지급 일정",
        "source": data["benefit"]["source"].copy(),
    }]
    data["payment_evidence"] = evidence
    data["maturity_accruals"] = [
        {"month": month, "amount_won": amount}
        for month, amount in [(1, 100_000), (12, 200_000), (24, 300_000), (36, 400_000)]
    ]
    data["maturity_accrual_evidence"] = evidence
    data["source_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    return data, text

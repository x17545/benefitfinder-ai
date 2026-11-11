"""Fictional employment and housing offers for the MVP API."""

from datetime import date
from typing import Any

from benefitfinder.application.service_catalog import (
    ServiceCondition,
    ServiceOffer,
    ServicePrice,
    Verdict,
)
from benefitfinder.domains.youth.eligibility import check_age, check_job_seeker, check_monthly_rent


def _age(profile: Any) -> Verdict:
    return check_age(profile.age, 19, 34)


def _employment(profile: Any) -> Verdict:
    return check_job_seeker(profile.employment_status)


def _housing(profile: Any) -> Verdict:
    contract = profile.domain_data.contract if profile.domain_data is not None else None
    return check_monthly_rent(contract.type if contract is not None else None)


def example_services() -> tuple[ServiceOffer, ...]:
    age = ServiceCondition("age", "[가상 예제] 만 19~34세 청년이 대상입니다.", _age)
    employment = ServiceCondition(
        "employment", "[가상 예제] 구직활동 중인 청년을 대상으로 합니다.", _employment,
    )
    housing = ServiceCondition(
        "monthly_rent", "[가상 예제] 월세 계약을 한 청년이 대상입니다.", _housing,
    )
    offers = (
        ("S1", "청년 이사 차량 지원", "housing", "1톤 이사 차량과 인력 지원", 1, 250_000, housing),
        ("S2", "청년 면접 정장 대여", "employment", "면접 정장 한 벌 대여 (3박 4일)", 3, 40_000,
         employment),
        ("S3", "청년 주거 상담", "housing", "임대차 계약 상담", 1, None, housing),
        ("S4", "청년 1:1 취업 컨설팅", "employment", "전문 상담사와 취업 상담", 5, None, employment),
    )
    return tuple(
        ServiceOffer(
            policy_id=f"youth:example:{key}",
            title=f"[가상] {title}",
            summary="기능 검증용 가상 서비스예요. 실제 신청이나 지원을 뜻하지 않아요.",
            provider="가상 정책기관",
            category=category,
            source={
                "url": f"https://policy.example/youth/service-{key.lower()}",
                "checked_at": "2026-11-01T09:00:00+09:00",
                "document_version": "fictional-service-v1",
            },
            application={"status": "open", "starts_on": None, "ends_on": None, "url": None},
            item=item,
            unit="회",
            quantity=quantity,
            price=(
                ServicePrice(
                    value, "[가상] 검증용 단가 기준표",
                    f"https://policy.example/youth/price-{key.lower()}", date(2026, 11, 1),
                    reviewed=True,
                )
                if value is not None else None
            ),
            conditions=(age, specific),
            virtual_data=True,
        )
        for key, title, category, item, quantity, value, specific in offers
    )

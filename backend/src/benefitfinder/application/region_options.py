"""지역 선택 목록의 인터페이스와 명세의 금액 구간. 원천 HTTP와 무관하다."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DistrictOption:
    code: str
    name: str


@dataclass(frozen=True)
class RegionOption:
    sido_code: str
    sido_name: str
    sigungu: tuple[DistrictOption, ...]


class RegionOptionsUnavailable(Exception):
    """공식 지역 목록을 확인하지 못했다. 가상 목록으로 대체하지 않는다."""


class RegionCatalog(Protocol):
    def get_regions(self) -> tuple[RegionOption, ...]:
        """현재 유효한 시도와 시군구를 반환하거나 불가 오류를 낸다."""
        ...


# REQUIREMENTS 4.3의 선택 구간. 정책 자격·금액 판정값이 아니다.
OPTION_BANDS: dict[str, tuple[tuple[str, str], ...]] = {
    "income": (
        ("none", "없음"),
        ("under_15m", "1,500만 미만"),
        ("between_15m_30m", "1,500만 ~ 3,000만"),
        ("between_30m_50m", "3,000만 ~ 5,000만"),
        ("over_50m", "5,000만 이상"),
        ("unknown", "모름"),
    ),
    "asset": (
        ("under_122m", "1억 2천만 이하"),
        ("between_122m_345m", "1억 2천만 ~ 3억 4,500만"),
        ("over_345m", "3억 4,500만 초과"),
        ("unknown", "모름"),
    ),
    "deposit": (
        ("under_50m", "5천만 원 이하"),
        ("between_50m_100m", "5천만 ~ 1억"),
        ("between_100m_300m", "1억 ~ 3억"),
        ("over_300m", "3억 초과"),
    ),
    "rent": (
        ("under_40", "40만 원 이하"),
        ("between_40_60", "40만 ~ 60만"),
        ("between_60_70", "60만 ~ 70만"),
        ("over_70", "70만 초과"),
    ),
    "parent_income": (
        ("under_3m", "300만 이하"),
        ("between_3m_5m", "300만 ~ 500만"),
        ("between_5m_7m", "500만 ~ 700만"),
        ("over_7m", "700만 초과"),
    ),
    "parent_asset": (
        ("under_300m", "3억 이하"),
        ("between_300m_500m", "3억 ~ 5억"),
        ("over_500m", "5억 초과"),
    ),
}

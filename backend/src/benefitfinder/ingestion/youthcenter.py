"""온통청년 정책 목록 API의 페이지 요청과 원천 레코드 추출.

필드 이름·응답 경로는 실제 응답 표본으로 확인한 뒤 ``YouthcenterApiConfig``로 주입한다.
이 모듈은 자격·금액·중복 수급 조건을 해석하지 않고, 원천 레코드를 받은 그대로 넘긴다.
API 키는 요청 파라미터에 넣지 않으며 네트워크 어댑터가 직접 추가한다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from benefitfinder.application.collection import SourceRequest

SOURCE_NAME = "youthcenter"
SECRET_PARAM_HINTS = ("key", "token", "secret", "password")


class PageParseError(ValueError):
    """응답 본문이 설정한 목록 구조와 맞지 않을 때 발생한다."""


@dataclass(frozen=True)
class YouthcenterApiConfig:
    """정책 목록 API 호출·응답 구조 설정. 값은 실제 응답 표본으로 확인해 채운다."""

    endpoint: str
    page_param: str
    page_size_param: str
    page_size: int
    record_list_path: tuple[str, ...]
    external_id_field: str
    total_count_path: tuple[str, ...] | None = None
    extra_params: Mapping[str, str] = field(default_factory=dict)
    max_pages: int = 500

    def __post_init__(self) -> None:
        if not self.endpoint.startswith("https://"):
            raise ValueError("endpoint는 https 주소여야 합니다.")
        if self.page_size < 1 or self.max_pages < 1:
            raise ValueError("page_size와 max_pages는 1 이상이어야 합니다.")
        if not self.record_list_path or not self.external_id_field:
            raise ValueError("record_list_path와 external_id_field가 필요합니다.")
        names = (self.page_param, self.page_size_param, *self.extra_params)
        for name in names:
            if any(hint in name.lower() for hint in SECRET_PARAM_HINTS):
                raise ValueError(f"비밀값으로 보이는 파라미터는 설정에 넣지 마세요: {name}")


def page_request(config: YouthcenterApiConfig, page: int) -> SourceRequest:
    """1부터 시작하는 페이지 번호로 목록 요청을 만든다."""
    params = dict(config.extra_params)
    params[config.page_param] = str(page)
    params[config.page_size_param] = str(config.page_size)
    return SourceRequest(url=config.endpoint, params=params)


def _follow(document: object, path: tuple[str, ...]) -> object:
    current = document
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise PageParseError(f"응답에 경로가 없습니다: {'.'.join(path)}")
        current = current[key]
    return current


def parse_page(
    config: YouthcenterApiConfig, body: bytes
) -> tuple[list[dict[str, object]], int | None]:
    """응답 본문에서 원천 레코드 목록과 (있으면) 전체 건수를 꺼낸다."""
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PageParseError("응답이 UTF-8 JSON이 아닙니다.") from exc
    records = _follow(document, config.record_list_path)
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise PageParseError("레코드 목록이 객체 배열이 아닙니다.")
    total: int | None = None
    if config.total_count_path is not None:
        raw_total = _follow(document, config.total_count_path)
        if isinstance(raw_total, bool):
            raise PageParseError("전체 건수가 정수가 아닙니다.")
        if isinstance(raw_total, str) and raw_total.isdigit():
            raw_total = int(raw_total)
        if not isinstance(raw_total, int) or raw_total < 0:
            raise PageParseError("전체 건수가 정수가 아닙니다.")
        total = raw_total
    return records, total


def external_id_of(config: YouthcenterApiConfig, record: Mapping[str, object]) -> str | None:
    """원천 정책 ID를 문자열로 돌려준다. 없거나 비어 있으면 ``None``."""
    value = record.get(config.external_id_field)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None

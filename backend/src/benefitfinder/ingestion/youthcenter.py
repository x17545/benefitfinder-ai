"""온통청년 정책 목록 API의 페이지 요청과 원천 레코드 추출.

필드 이름·응답 경로는 ``YouthcenterApiConfig``로 주입하며, 실제 표본으로 확인한 설정은
``YOUTHCENTER_POLICY_LIST``다.
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
    result_code_path: tuple[str, ...] | None = None
    success_codes: frozenset[str] = frozenset()
    hash_excluded_fields: frozenset[str] = frozenset()
    dropped_fields: frozenset[str] = frozenset()
    extra_params: Mapping[str, str] = field(default_factory=dict)
    max_pages: int = 500

    def __post_init__(self) -> None:
        if not self.endpoint.startswith("https://"):
            raise ValueError("endpoint는 https 주소여야 합니다.")
        if self.page_size < 1 or self.max_pages < 1:
            raise ValueError("page_size와 max_pages는 1 이상이어야 합니다.")
        if not self.record_list_path or not self.external_id_field:
            raise ValueError("record_list_path와 external_id_field가 필요합니다.")
        if self.external_id_field in self.hash_excluded_fields | self.dropped_fields:
            raise ValueError("정책 ID 필드는 해시 제외·삭제 대상이 될 수 없습니다.")
        if (self.result_code_path is None) != (not self.success_codes):
            raise ValueError("result_code_path와 success_codes는 함께 설정하세요.")
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
    if config.result_code_path is not None:
        code = _follow(document, config.result_code_path)
        if isinstance(code, bool) or str(code) not in config.success_codes:
            raise PageParseError(f"응답 결과 코드가 성공이 아닙니다: {code!r}")
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


def stored_fields(
    config: YouthcenterApiConfig, record: Mapping[str, object]
) -> dict[str, object]:
    """저장할 레코드 필드. 담당자 이름 등 저장하지 않기로 한 필드를 뺀다."""
    return {key: value for key, value in record.items() if key not in config.dropped_fields}


def hashed_fields(
    config: YouthcenterApiConfig, record: Mapping[str, object]
) -> dict[str, object]:
    """변경 판정에 쓰는 필드. 조회수처럼 정책 내용과 무관하게 바뀌는 값을 뺀다."""
    excluded = config.hash_excluded_fields | config.dropped_fields
    return {key: value for key, value in record.items() if key not in excluded}


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


# 2026-10-30 실제 응답 표본으로 확인한 정책 목록 API 설정 (청년정책API, getPlcy).
# 인증키(apiKeyNm)는 넣지 않으며 HTTP 어댑터가 추가한다. 한 페이지 최대 건수는 미확인이라
# 서버가 요청보다 적게 주면 수집기가 short_page 오류로 멈춘다.
YOUTHCENTER_POLICY_LIST = YouthcenterApiConfig(
    endpoint="https://www.youthcenter.go.kr/go/ythip/getPlcy",
    page_param="pageNum",
    page_size_param="pageSize",
    page_size=100,
    record_list_path=("result", "youthPolicyList"),
    external_id_field="plcyNo",
    total_count_path=("result", "pagging", "totCount"),
    result_code_path=("resultCode",),
    success_codes=frozenset({"200"}),
    hash_excluded_fields=frozenset({"inqCnt"}),
    dropped_fields=frozenset({"sprvsnInstPicNm", "operInstPicNm"}),
    extra_params={"rtnType": "json"},
)

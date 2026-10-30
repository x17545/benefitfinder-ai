"""온통청년 정책 목록 수집 실행 함수 (jobs가 호출하는 진입점).

모든 응답을 원본 그대로 스냅숏으로 보관하고, 정책 ID별 레코드 버전을 비교한다.
페이지·레코드 오류는 성공 데이터와 분리해 돌려주며, 일부라도 실패하면
``complete=False``로 표시하고 삭제 감지를 하지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from benefitfinder.application.collection import (
    CollectedRecord,
    RawSnapshot,
    RawSnapshotStore,
    RecordChange,
    RecordVersionStore,
    SourceFetcher,
)
from benefitfinder.ingestion.versioning import diff_records, record_sha256, sha256_hex
from benefitfinder.ingestion.youthcenter import (
    SOURCE_NAME,
    PageParseError,
    YouthcenterApiConfig,
    external_id_of,
    hashed_fields,
    page_request,
    parse_page,
    stored_fields,
)


@dataclass(frozen=True)
class CollectionError:
    """성공 데이터와 분리한 수집 오류. 응답 본문·예외 원문·키를 담지 않는다."""

    page: int
    code: str
    detail: str
    snapshot_ref: str | None = None
    external_id: str | None = None


@dataclass(frozen=True)
class CollectionResult:
    """한 번의 수집 실행 결과."""

    source_name: str
    started_at: datetime
    records: tuple[CollectedRecord, ...]
    changes: tuple[RecordChange, ...]
    errors: tuple[CollectionError, ...]
    complete: bool


def _now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock은 시간대가 있는 시각을 돌려줘야 합니다.")
    return value


def collect_youthcenter(
    config: YouthcenterApiConfig,
    fetcher: SourceFetcher,
    snapshot_store: RawSnapshotStore,
    version_store: RecordVersionStore,
    clock: Callable[[], datetime],
) -> CollectionResult:
    """정책 목록 전체를 페이지 순서대로 수집하고 버전 변경을 저장한다."""
    started_at = _now(clock)
    records: dict[str, CollectedRecord] = {}
    conflicted: set[str] = set()
    errors: list[CollectionError] = []
    finished = False
    expected_total: int | None = None

    for page in range(1, config.max_pages + 1):
        request = page_request(config, page)
        try:
            response = fetcher.fetch(request)
        except Exception as exc:  # 어댑터 오류 원문에는 키·주소가 섞일 수 있다.
            errors.append(CollectionError(page, "fetch_failed", type(exc).__name__))
            break
        fetched_at = _now(clock)
        snapshot = RawSnapshot(
            source_name=SOURCE_NAME,
            request=request,
            fetched_at=fetched_at,
            status_code=response.status_code,
            content_type=response.content_type,
            content_sha256=sha256_hex(response.body),
            body=response.body,
        )
        snapshot_ref = snapshot_store.save(snapshot)
        if not 200 <= response.status_code < 300:
            errors.append(
                CollectionError(page, "http_status", str(response.status_code), snapshot_ref)
            )
            break
        try:
            page_records, total = parse_page(config, response.body)
        except PageParseError as exc:
            errors.append(CollectionError(page, "parse_failed", str(exc), snapshot_ref))
            break

        for fields in page_records:
            external_id = external_id_of(config, fields)
            if external_id is None:
                errors.append(
                    CollectionError(page, "missing_external_id", "정책 ID 없음", snapshot_ref)
                )
                continue
            record = CollectedRecord(
                source_name=SOURCE_NAME,
                external_id=external_id,
                record_sha256=record_sha256(hashed_fields(config, fields)),
                snapshot_ref=snapshot_ref,
                fetched_at=fetched_at,
                fields=stored_fields(config, fields),
            )
            existing = records.get(external_id)
            if existing is None and external_id not in conflicted:
                records[external_id] = record
            elif existing is not None and existing.record_sha256 != record.record_sha256:
                del records[external_id]
                conflicted.add(external_id)
                errors.append(
                    CollectionError(
                        page, "conflicting_duplicate", "같은 ID의 다른 내용",
                        snapshot_ref, external_id,
                    )
                )

        if total is not None:
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                errors.append(
                    CollectionError(page, "total_changed", "수집 중 전체 건수가 바뀜", snapshot_ref)
                )
                break
            expected_count = min(
                config.page_size, max(0, total - (page - 1) * config.page_size)
            )
            if not page_records and expected_count:
                errors.append(
                    CollectionError(page, "unexpected_empty_page", "전체 건수보다 일찍 끝남",
                                    snapshot_ref)
                )
                break
            if len(page_records) < expected_count:
                errors.append(
                    CollectionError(page, "short_page", "요청보다 적은 건수, 페이지 크기 확인 필요",
                                    snapshot_ref)
                )
                break
            if len(page_records) > expected_count:
                errors.append(
                    CollectionError(page, "page_count_mismatch", "예상보다 많은 페이지 건수",
                                    snapshot_ref)
                )
                break
            if page * config.page_size >= total:
                if len(records) != total:
                    errors.append(
                        CollectionError(page, "record_count_mismatch", "전체 건수와 고유 ID 수 불일치",
                                        snapshot_ref)
                    )
                finished = True
                break
        elif len(page_records) < config.page_size:
            finished = True
            break
    else:
        errors.append(
            CollectionError(config.max_pages, "page_limit", f"{config.max_pages}페이지 초과")
        )

    complete = finished and not errors
    collected = tuple(records.values())
    previous = version_store.latest_hashes(SOURCE_NAME)
    changes = tuple(diff_records(SOURCE_NAME, previous, collected, complete=complete))
    if collected or changes:
        version_store.save_versions(collected, changes, checked_at=_now(clock))
    return CollectionResult(
        source_name=SOURCE_NAME,
        started_at=started_at,
        records=collected,
        changes=changes,
        errors=tuple(errors),
        complete=complete,
    )

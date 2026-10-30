"""온통청년 목록 수집·원본 보관·버전 비교 테스트.

네트워크·DB·실제 수집 원본 없이 가상 어댑터와 가상 필드 이름으로 검증한다.
실제 API 필드 이름은 응답 표본으로 확인한 뒤 설정으로 주입한다.
"""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone

from benefitfinder.application.collection import (
    ChangeKind,
    CollectedRecord,
    RawSnapshot,
    RecordChange,
    SourceRequest,
    SourceResponse,
)
from benefitfinder.ingestion.collect import collect_youthcenter
from benefitfinder.ingestion.versioning import record_sha256
from benefitfinder.ingestion.youthcenter import (
    YOUTHCENTER_POLICY_LIST,
    PageParseError,
    YouthcenterApiConfig,
    page_request,
    parse_page,
)

KST = timezone(timedelta(hours=9))
CONFIG = YouthcenterApiConfig(
    endpoint="https://api.example.invalid/policies",
    page_param="page",
    page_size_param="size",
    page_size=2,
    record_list_path=("result", "items"),
    external_id_field="policyId",
    total_count_path=("result", "total"),
    extra_params={"format": "json"},
)


def page_body(items: list[dict[str, object]], total: int | None) -> bytes:
    result: dict[str, object] = {"items": items}
    if total is not None:
        result["total"] = total
    return json.dumps({"result": result}, ensure_ascii=False).encode("utf-8")


class FakeFetcher:
    def __init__(self, pages: Mapping[int, SourceResponse | Exception]) -> None:
        self.pages = pages
        self.requests: list[SourceRequest] = []

    def fetch(self, request: SourceRequest) -> SourceResponse:
        self.requests.append(request)
        page = request.params.get("page") or request.params["pageNum"]
        outcome = self.pages[int(page)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeSnapshotStore:
    def __init__(self) -> None:
        self.saved: list[RawSnapshot] = []

    def save(self, snapshot: RawSnapshot) -> str:
        self.saved.append(snapshot)
        return f"snap-{len(self.saved)}"


class FakeVersionStore:
    def __init__(self, previous: Mapping[str, str] | None = None) -> None:
        self.previous = dict(previous or {})
        self.saved_records: list[CollectedRecord] = []
        self.saved_changes: list[RecordChange] = []

    def latest_hashes(self, source_name: str) -> Mapping[str, str]:
        return self.previous

    def save_versions(
        self, records: Sequence[CollectedRecord], changes: Sequence[RecordChange]
    ) -> None:
        self.saved_records.extend(records)
        self.saved_changes.extend(changes)


def ok(body: bytes) -> SourceResponse:
    return SourceResponse(200, body, "application/json")


def clock() -> datetime:
    return datetime(2026, 10, 30, 3, 0, tzinfo=KST)


def run(
    pages: Mapping[int, SourceResponse | Exception],
    previous: Mapping[str, str] | None = None,
    config: YouthcenterApiConfig = CONFIG,
):
    fetcher, snapshots, versions = FakeFetcher(pages), FakeSnapshotStore(), FakeVersionStore(
        previous
    )
    result = collect_youthcenter(config, fetcher, snapshots, versions, clock)
    return result, fetcher, snapshots, versions


A = {"policyId": "P-A", "name": "가상 정책 A"}
B = {"policyId": "P-B", "name": "가상 정책 B"}
C = {"policyId": 3, "name": "가상 정책 C"}


class ConfigAndParseTests(unittest.TestCase):
    def test_rejects_secret_like_params_and_plain_http(self) -> None:
        base = dict(
            page_param="page", page_size_param="size", page_size=10,
            record_list_path=("items",), external_id_field="id",
        )
        with self.assertRaises(ValueError):
            YouthcenterApiConfig(endpoint="http://api.example.invalid", **base)
        with self.assertRaises(ValueError):
            YouthcenterApiConfig(
                endpoint="https://api.example.invalid", extra_params={"apiKey": "x"}, **base
            )

    def test_page_request_has_paging_and_no_secret(self) -> None:
        request = page_request(CONFIG, 3)
        self.assertEqual(request.params, {"format": "json", "page": "3", "size": "2"})

    def test_parse_page_errors(self) -> None:
        for body in (b"not json", b'{"result": {}}', b'{"result": {"items": [1]}}'):
            with self.assertRaises(PageParseError):
                parse_page(CONFIG, body)
        records, total = parse_page(CONFIG, b'{"result": {"items": [], "total": "7"}}')
        self.assertEqual((records, total), ([], 7))

    def test_record_hash_ignores_key_order(self) -> None:
        self.assertEqual(record_sha256({"a": 1, "b": "가"}), record_sha256({"b": "가", "a": 1}))
        self.assertNotEqual(record_sha256({"a": 1}), record_sha256({"a": 2}))


class CollectTests(unittest.TestCase):
    def test_collects_all_pages_and_keeps_raw_snapshots(self) -> None:
        bodies = {1: page_body([A, B], 3), 2: page_body([C], 3)}
        result, fetcher, snapshots, versions = run({k: ok(v) for k, v in bodies.items()})
        self.assertTrue(result.complete)
        self.assertEqual(result.errors, ())
        self.assertEqual([r.external_id for r in result.records], ["P-A", "P-B", "3"])
        self.assertEqual(len(fetcher.requests), 2)
        self.assertEqual([s.body for s in snapshots.saved], [bodies[1], bodies[2]])
        self.assertTrue(all(s.fetched_at.tzinfo for s in snapshots.saved))
        self.assertEqual(result.records[2].snapshot_ref, "snap-2")
        self.assertEqual({c.kind for c in result.changes}, {ChangeKind.NEW})
        self.assertEqual(len(versions.saved_records), 3)

    def test_stops_on_short_page_without_total(self) -> None:
        config = YouthcenterApiConfig(
            endpoint=CONFIG.endpoint, page_param="page", page_size_param="size", page_size=2,
            record_list_path=("result", "items"), external_id_field="policyId",
        )
        pages = {1: ok(page_body([A, B], None)), 2: ok(page_body([C], None))}
        result, fetcher, _, _ = run(pages, config=config)
        self.assertTrue(result.complete)
        self.assertEqual(len(fetcher.requests), 2)

    def test_detects_new_changed_unchanged_and_missing(self) -> None:
        previous = {
            "P-A": record_sha256(A),
            "P-B": record_sha256({"policyId": "P-B", "name": "예전 이름"}),
            "P-GONE": "old-hash",
        }
        pages = {1: ok(page_body([A, B], 3)), 2: ok(page_body([C], 3))}
        result, *_ = run(pages, previous)
        kinds = {c.external_id: c.kind for c in result.changes}
        self.assertEqual(
            kinds,
            {
                "P-A": ChangeKind.UNCHANGED,
                "P-B": ChangeKind.CHANGED,
                "3": ChangeKind.NEW,
                "P-GONE": ChangeKind.MISSING,
            },
        )

    def test_partial_failure_keeps_successes_and_skips_missing(self) -> None:
        previous = {"P-GONE": "old-hash"}
        pages = {1: ok(page_body([A, B], 3)), 2: SourceResponse(503, b"busy", "text/plain")}
        result, _, snapshots, versions = run(pages, previous)
        self.assertFalse(result.complete)
        self.assertEqual([e.code for e in result.errors], ["http_status"])
        self.assertEqual(len(snapshots.saved), 2)
        self.assertEqual(len(result.records), 2)
        self.assertNotIn(ChangeKind.MISSING, {c.kind for c in result.changes})
        self.assertEqual(len(versions.saved_records), 2)

    def test_fetch_exception_does_not_leak_message(self) -> None:
        secret = "https://api.example.invalid/?apiKey=SECRET"
        result, _, snapshots, _ = run({1: ConnectionError(secret)})
        self.assertFalse(result.complete)
        self.assertEqual(result.errors[0].code, "fetch_failed")
        self.assertNotIn("SECRET", repr(result.errors))
        self.assertEqual(snapshots.saved, [])

    def test_parse_failure_and_missing_id_are_separated(self) -> None:
        result, *_ = run({1: ok(b"<html>maintenance</html>")})
        self.assertEqual(result.errors[0].code, "parse_failed")
        self.assertEqual(result.errors[0].snapshot_ref, "snap-1")

        no_id = {"name": "ID 없는 가상 정책"}
        result, *_ = run({1: ok(page_body([A, no_id], 2))})
        self.assertFalse(result.complete)
        self.assertEqual([r.external_id for r in result.records], ["P-A"])
        self.assertEqual(result.errors[0].code, "missing_external_id")

    def test_duplicates_are_merged_or_held_back(self) -> None:
        same = {1: ok(page_body([A, B], 4)), 2: ok(page_body([A, C], 4))}
        result, *_ = run(same)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.records), 3)

        changed_a = {"policyId": "P-A", "name": "내용이 다른 A"}
        conflict = {1: ok(page_body([A, B], 4)), 2: ok(page_body([changed_a, C], 4))}
        result, *_ = run(conflict)
        self.assertFalse(result.complete)
        self.assertNotIn("P-A", [r.external_id for r in result.records])
        self.assertEqual(result.errors[0].code, "conflicting_duplicate")

    def test_empty_page_before_total_and_page_limit(self) -> None:
        result, *_ = run({1: ok(page_body([A, B], 5)), 2: ok(page_body([], 5))})
        self.assertEqual(result.errors[0].code, "unexpected_empty_page")

        config = YouthcenterApiConfig(
            endpoint=CONFIG.endpoint, page_param="page", page_size_param="size", page_size=2,
            record_list_path=("result", "items"), external_id_field="policyId",
            max_pages=1,
        )
        result, *_ = run({1: ok(page_body([A, B], None))}, config=config)
        self.assertEqual(result.errors[0].code, "page_limit")
        self.assertFalse(result.complete)

    def test_rejects_naive_clock(self) -> None:
        with self.assertRaises(ValueError):
            collect_youthcenter(
                CONFIG, FakeFetcher({}), FakeSnapshotStore(), FakeVersionStore(),
                lambda: datetime(2026, 10, 30),
            )


    def test_short_page_before_total_stops_with_error(self) -> None:
        result, *_ = run({1: ok(page_body([A], 5))})
        self.assertFalse(result.complete)
        self.assertEqual(result.errors[0].code, "short_page")


class SourceSettingTests(unittest.TestCase):
    """실제 표본에서 확인한 필드 이름에 가상 값을 넣어 설정을 검증한다."""

    def body(self, items: list[dict[str, object]], total: int, code: object = 200) -> bytes:
        document = {
            "resultCode": code,
            "resultMessage": "가상 응답",
            "result": {
                "pagging": {"totCount": total, "pageNum": 1, "pageSize": 100},
                "youthPolicyList": items,
            },
        }
        return json.dumps(document, ensure_ascii=False).encode("utf-8")

    def policy(self, number: str, views: str = "1", manager: str = "") -> dict[str, object]:
        return {
            "plcyNo": number,
            "plcyNm": "가상 청년 정책",
            "lclsfNm": "일자리",
            "inqCnt": views,
            "sprvsnInstPicNm": manager,
            "operInstPicNm": manager,
        }

    def collect(self, body: bytes, previous: Mapping[str, str] | None = None):
        fetcher = FakeFetcher({1: ok(body)})
        versions = FakeVersionStore(previous)
        result = collect_youthcenter(
            YOUTHCENTER_POLICY_LIST, fetcher, FakeSnapshotStore(), versions, clock
        )
        return result, fetcher

    def test_request_uses_verified_params_without_key(self) -> None:
        request = page_request(YOUTHCENTER_POLICY_LIST, 1)
        self.assertEqual(
            request.params, {"rtnType": "json", "pageNum": "1", "pageSize": "100"}
        )
        self.assertNotIn("apiKeyNm", request.params)

    def test_collects_with_verified_paths_and_drops_manager_names(self) -> None:
        result, _ = self.collect(self.body([self.policy("V-1", manager="가상 담당자")], 1))
        self.assertTrue(result.complete)
        record = result.records[0]
        self.assertEqual(record.external_id, "V-1")
        self.assertNotIn("sprvsnInstPicNm", record.fields)
        self.assertNotIn("operInstPicNm", record.fields)

    def test_view_count_change_is_not_a_policy_change(self) -> None:
        first, _ = self.collect(self.body([self.policy("V-1", views="1")], 1))
        previous = {"V-1": first.records[0].record_sha256}
        second, _ = self.collect(self.body([self.policy("V-1", views="999")], 1), previous)
        self.assertEqual(second.changes[0].kind, ChangeKind.UNCHANGED)

    def test_failed_result_code_is_an_error(self) -> None:
        result, _ = self.collect(self.body([], 0, code=500))
        self.assertFalse(result.complete)
        self.assertEqual(result.errors[0].code, "parse_failed")


if __name__ == "__main__":
    unittest.main()

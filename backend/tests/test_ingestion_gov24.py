"""Focused GOV24 collector tests use virtual responses and no real source data."""

from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from benefitfinder.application.collection import (
    ChangeKind,
    CollectedRecord,
    RawSnapshot,
    RawSnapshotStore,
    RecordChange,
    RecordVersionStore,
    SourceRequest,
    SourceResponse,
)
from benefitfinder.ingestion.gov24 import (
    Gov24ParseError,
    analysis_fields,
    collect_gov24,
    gov24_hashed_fields,
    gov24_request,
    parse_gov24_response,
)
from benefitfinder.ingestion.versioning import record_sha256, sha256_hex
from benefitfinder.infrastructure.local_files import (
    JsonFileSnapshotStore,
    JsonFileVersionStore,
)

KST = timezone(timedelta(hours=9))
BASE_TIME = datetime(2026, 11, 13, 3, 0, tzinfo=KST)
CATEGORIES = {"employment": ("취업",), "housing": ("주거",)}


def envelope(row: dict[str, object] | None) -> bytes:
    count = 0 if row is None else 1
    return json.dumps(
        {
            "page": 1,
            "perPage": 1,
            "totalCount": 9999,
            "currentCount": count,
            "matchCount": count,
            "data": [] if row is None else [row],
        },
        ensure_ascii=False,
    ).encode("utf-8")


def rows_for(
    external_id: str,
    *,
    category: str = "취업",
    name: str = "가상 취업 지원",
    age_start: object = 19,
    age_end: object = 39,
    purpose: str = "가상 목적",
    views: object = 7,
) -> dict[str, dict[str, object] | None]:
    return {
        "serviceList": {
            "서비스ID": external_id,
            "서비스명": name,
            "서비스분야": category,
            "조회수": views,
            "전화문의": "가상 전화번호",
            "상세조회URL": "https://example.invalid/list-detail",
        },
        "serviceDetail": {
            "서비스ID": external_id,
            "서비스명": name,
            "서비스목적": purpose,
            "지원대상": "가상 대상",
            "선정기준": "가상 기준",
            "지원내용": "가상 지원",
            "신청방법": "가상 신청",
            "신청기한": "가상 기한",
            "구비서류": "가상 서류",
            "문의처": "가상 문의",
            "온라인신청사이트URL": "https://example.invalid/apply",
            "법령": "가상 법령",
            "자치법규": "가상 자치법규",
            "행정규칙": "가상 행정규칙",
        },
        "supportConditions": {
            "서비스ID": external_id,
            "서비스명": name,
            "JA0110": age_start,
            "JA0111": age_end,
            "JA0101": "Y",
        },
    }


def ok(body: bytes) -> SourceResponse:
    return SourceResponse(200, body, "application/json; charset=utf-8")


class FakeFetcher:
    def __init__(self, rows: Mapping[str, Mapping[str, dict[str, object] | None]]) -> None:
        self.rows = rows
        self.requests: list[SourceRequest] = []

    def fetch(self, request: SourceRequest) -> SourceResponse:
        self.requests.append(request)
        endpoint = request.url.rsplit("/", 1)[-1]
        external_id = request.params["cond[서비스ID::EQ]"]
        return ok(envelope(self.rows[external_id][endpoint]))


class FakeSnapshotStore:
    def __init__(self) -> None:
        self.saved: list[RawSnapshot] = []

    def save(self, snapshot: RawSnapshot) -> str:
        self.saved.append(snapshot)
        return f"snap-{len(self.saved)}"


class FakeVersionStore:
    def __init__(self) -> None:
        self.saved: list[
            tuple[tuple[CollectedRecord, ...], tuple[RecordChange, ...], datetime]
        ] = []

    def latest_hashes(self, source_name: str) -> Mapping[str, str]:
        return {}

    def save_versions(
        self,
        records: Sequence[CollectedRecord],
        changes: Sequence[RecordChange],
        *,
        checked_at: datetime,
    ) -> None:
        self.saved.append((tuple(records), tuple(changes), checked_at))


class IncrementingClock:
    def __init__(self) -> None:
        self.value = -1

    def __call__(self) -> datetime:
        self.value += 1
        return BASE_TIME + timedelta(seconds=self.value)


def run_collect(
    rows: Mapping[str, Mapping[str, dict[str, object] | None]],
    *,
    external_ids: tuple[str, ...],
    snapshot_store: RawSnapshotStore | None = None,
    version_store: RecordVersionStore | None = None,
) -> tuple[object, FakeFetcher, RawSnapshotStore, RecordVersionStore]:
    fetcher = FakeFetcher(rows)
    snapshots = snapshot_store or FakeSnapshotStore()
    versions = version_store or FakeVersionStore()
    result = collect_gov24(
        external_ids,
        CATEGORIES,
        fetcher,
        snapshots,
        versions,
        IncrementingClock(),
    )
    return result, fetcher, snapshots, versions


class ParseTests(unittest.TestCase):
    def test_returns_exact_matching_row_and_allows_only_typed_nonempty_envelope(self) -> None:
        row = rows_for("ID-1")["serviceList"]
        self.assertEqual(parse_gov24_response(envelope(row), "ID-1"), row)
        self.assertIsNone(
            parse_gov24_response(envelope(None), "ID-1", allow_empty=True)
        )
        with self.assertRaises(Gov24ParseError):
            parse_gov24_response(envelope(None), "ID-1")

    def test_rejects_duplicate_json_nonfinite_counts_and_ids(self) -> None:
        row = rows_for("ID-1")["serviceList"]
        valid = json.loads(envelope(row))
        broken = []
        broken.append(b'{"page":1,"page":1,"perPage":1,"totalCount":1,'
                      b'"currentCount":1,"matchCount":1,"data":[]}')
        nonfinite = dict(valid)
        nonfinite["data"] = [{**row, "bad": float("inf")}]
        broken.append(json.dumps(nonfinite).encode("utf-8"))
        for mutate in (
            lambda value: value.update(matchCount="1"),
            lambda value: value.update(currentCount=0),
            lambda value: value.update(matchCount=2),
            lambda value: value.update(data=[{**row, "서비스ID": "OTHER"}]),
            lambda value: value.update(extra=True),
        ):
            data = dict(valid)
            mutate(data)
            broken.append(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        for body in broken:
            with self.subTest(body=body):
                with self.assertRaises(Gov24ParseError):
                    parse_gov24_response(body, "ID-1")

    def test_request_is_fixed_filtered_and_has_no_key(self) -> None:
        request = gov24_request("serviceDetail", "ID-1")
        self.assertEqual(
            request.url,
            "https://api.odcloud.kr/api/gov24/v3/serviceDetail",
        )
        self.assertEqual(
            request.params,
            {
                "cond[서비스ID::EQ]": "ID-1",
                "page": "1",
                "perPage": "1",
                "returnType": "JSON",
            },
        )


class CollectionTests(unittest.TestCase):
    def test_uses_match_count_and_writes_stable_manifest(self) -> None:
        rows = {"ID-1": rows_for("ID-1")}
        result, fetcher, snapshots, versions = run_collect(rows, external_ids=("ID-1",))
        self.assertTrue(result.complete)
        self.assertEqual(result.errors, ())
        self.assertEqual(len(fetcher.requests), 3)
        self.assertEqual(
            [snapshot.source_name for snapshot in snapshots.saved],
            [
                "gov24-serviceList",
                "gov24-serviceDetail",
                "gov24-supportConditions",
                "gov24",
            ],
        )
        manifest = json.loads(snapshots.saved[-1].body)
        self.assertEqual(manifest["format_version"], 1)
        self.assertEqual(manifest["external_id"], "ID-1")
        self.assertEqual(
            set(manifest["responses"]),
            {"serviceList", "serviceDetail", "supportConditions"},
        )
        for endpoint, entry in manifest["responses"].items():
            raw = next(
                snapshot
                for snapshot in snapshots.saved
                if snapshot.source_name == f"gov24-{endpoint}"
            )
            self.assertEqual(entry["content_sha256"], sha256_hex(raw.body))
        self.assertEqual(snapshots.saved[-1].request, gov24_request("serviceList", "ID-1"))
        record = result.records[0]
        self.assertEqual(record.source_name, "gov24")
        self.assertEqual(record.snapshot_ref, "snap-4")
        self.assertEqual(record.fetched_at, snapshots.saved[-1].fetched_at)
        self.assertEqual(set(record.fields), set(manifest["responses"]))
        self.assertEqual(len(versions.saved), 1)

    def test_valid_age_outside_youth_is_confirmed_removal_but_invalid_age_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshots = JsonFileSnapshotStore(root / "raw")
            versions = JsonFileVersionStore(root / "processed")
            first, *_ = run_collect(
                {"ID-1": rows_for("ID-1")},
                external_ids=("ID-1",),
                snapshot_store=snapshots,
                version_store=versions,
            )
            self.assertTrue(first.complete)

            removed_rows = {"ID-1": rows_for("ID-1", age_start=60, age_end=80)}
            removed, fetcher, _, _ = run_collect(
                removed_rows,
                external_ids=("ID-1",),
                snapshot_store=snapshots,
                version_store=versions,
            )
            self.assertTrue(removed.complete)
            self.assertEqual(removed.errors, ())
            self.assertEqual(removed.records, ())
            self.assertEqual([change.kind for change in removed.changes], [ChangeKind.MISSING])
            self.assertEqual(len(fetcher.requests), 3)
            self.assertIsNotNone(
                versions.load_records("gov24")["ID-1"]["missing_detected_at"]
            )

            before = versions.records_path("gov24").read_bytes()
            invalid_rows = {"ID-1": rows_for("ID-1", age_start="19", age_end=39)}
            invalid, *_ = run_collect(
                invalid_rows,
                external_ids=("ID-1",),
                snapshot_store=snapshots,
                version_store=versions,
            )
            self.assertFalse(invalid.complete)
            self.assertEqual(invalid.errors[0].code, "invalid_age")
            self.assertEqual(versions.records_path("gov24").read_bytes(), before)

    def test_list_absence_is_missing_but_empty_detail_or_support_preserves_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshots = JsonFileSnapshotStore(root / "raw")
            versions = JsonFileVersionStore(root / "processed")
            first, *_ = run_collect(
                {"ID-1": rows_for("ID-1")},
                external_ids=("ID-1",),
                snapshot_store=snapshots,
                version_store=versions,
            )
            self.assertTrue(first.complete)
            before = versions.records_path("gov24").read_bytes()

            absent_rows = {"ID-1": rows_for("ID-1")}
            absent_rows["ID-1"]["serviceList"] = None
            absent, fetcher, _, _ = run_collect(
                absent_rows,
                external_ids=("ID-1",),
                snapshot_store=snapshots,
                version_store=versions,
            )
            self.assertTrue(absent.complete)
            self.assertEqual(len(fetcher.requests), 1)
            self.assertEqual(absent.changes[0].kind, ChangeKind.MISSING)

            before_second = versions.records_path("gov24").read_bytes()
            for endpoint in ("serviceDetail", "supportConditions"):
                failed_rows = {"ID-1": rows_for("ID-1")}
                failed_rows["ID-1"][endpoint] = None
                failed, *_ = run_collect(
                    failed_rows,
                    external_ids=("ID-1",),
                    snapshot_store=snapshots,
                    version_store=versions,
                )
                self.assertFalse(failed.complete)
                self.assertEqual(failed.errors[0].code, "empty_response")
                self.assertEqual(
                    versions.records_path("gov24").read_bytes(), before_second
                )
            self.assertNotEqual(before, before_second)

    def test_one_failure_prevents_saving_any_version_or_missing_change(self) -> None:
        rows = {
            "ID-1": rows_for("ID-1"),
            "ID-2": rows_for("ID-2", age_start=39, age_end=19),
        }
        result, _, snapshots, versions = run_collect(
            rows,
            external_ids=("ID-1", "ID-2"),
        )
        self.assertFalse(result.complete)
        self.assertEqual([record.external_id for record in result.records], ["ID-1"])
        self.assertEqual(result.changes, ())
        self.assertEqual(versions.saved, [])
        self.assertEqual(
            sum(snapshot.source_name == "gov24" for snapshot in snapshots.saved), 1
        )

    def test_category_name_and_age_validation_are_fixed_failures(self) -> None:
        cases = []
        rows = {"ID-1": rows_for("ID-1", category="생활")}
        cases.append((rows, "category_out_of_scope"))
        rows = {"ID-1": rows_for("ID-1")}
        rows["ID-1"]["serviceDetail"]["서비스명"] = "다른 이름"
        cases.append((rows, "service_name_mismatch"))
        rows = {"ID-1": rows_for("ID-1", age_start=0, age_end=120)}
        self.assertTrue(run_collect(rows, external_ids=("ID-1",))[0].complete)
        rows = {"ID-1": rows_for("ID-1", age_start=121, age_end=130)}
        cases.append((rows, "invalid_age"))
        for row_map, code in cases:
            with self.subTest(code=code):
                result, *_ = run_collect(row_map, external_ids=("ID-1",))
                self.assertFalse(result.complete)
                self.assertEqual(result.errors[0].code, code)

    def test_selected_ids_and_category_mapping_are_explicit(self) -> None:
        with self.assertRaises(ValueError):
            collect_gov24(
                ("ID-1",),
                {"unmapped": ("취업",)},
                FakeFetcher({}),
                FakeSnapshotStore(),
                FakeVersionStore(),
                IncrementingClock(),
            )
        with self.assertRaises(ValueError):
            collect_gov24(
                tuple(str(index) for index in range(101)),
                CATEGORIES,
                FakeFetcher({}),
                FakeSnapshotStore(),
                FakeVersionStore(),
                IncrementingClock(),
            )

    def test_hashes_ignore_only_list_views_and_projection_excludes_private_fields(self) -> None:
        first = rows_for("ID-1", views=1)
        second = rows_for("ID-1", views=999)
        self.assertEqual(
            record_sha256(gov24_hashed_fields(first)),
            record_sha256(gov24_hashed_fields(second)),
        )
        changed = rows_for("ID-1", purpose="바뀐 목적")
        self.assertNotEqual(
            record_sha256(gov24_hashed_fields(first)),
            record_sha256(gov24_hashed_fields(changed)),
        )
        projected = analysis_fields(first)
        self.assertEqual(
            set(projected),
            {
                "serviceId",
                "serviceName",
                "serviceCategory",
                "ageStart",
                "ageEnd",
                "detailPurpose",
                "detailTarget",
                "detailCriteria",
                "detailSupport",
                "detailMethod",
                "detailDeadline",
                "detailDocuments",
                "detailLaw",
                "detailLocalLaw",
                "detailAdministrativeRule",
            },
        )
        self.assertEqual(projected["serviceId"], "ID-1")
        self.assertEqual(projected["serviceCategory"], "취업")
        self.assertNotIn("전화문의", repr(projected))
        self.assertNotIn("문의처", repr(projected))
        self.assertNotIn("URL", repr(projected))


if __name__ == "__main__":
    unittest.main()

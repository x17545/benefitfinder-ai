"""Deterministic source-cash batch tests use fictional notices and injected storage."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
from pathlib import Path
import unittest

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import AnalysisPolicy, AnalysisSource, PolicyAnalysisInput
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.region_options import DistrictOption, RegionOption
from benefitfinder.infrastructure.ai_catalog_review import AIReview, project_youthcenter_source
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash
from benefitfinder.ingestion.source_cash import compile_source_cash
from benefitfinder.jobs.source_cash_batch import (
    PreparedSourceCash,
    SOURCE_CASH_REVIEWER_AGENT,
    SOURCE_CASH_REVIEW_METADATA,
    SOURCE_CASH_WRITER_AGENT,
    restore_checked_at_only,
    run_source_cash_batch,
    source_cash_review_timestamp,
)


KST = timezone(timedelta(hours=9))
TODAY = date(2026, 11, 16)
REGIONS = (RegionOption("11", "가상도", (DistrictOption("11110", "가상시"),)),)


def value(number: str) -> PolicyAnalysisInput:
    fields = {
        "plcyNo": number, "plcyNm": "[가상] 청년 구직수당", "lclsfNm": "일자리",
        "mclsfNm": "취업", "plcySprtCn": "매월 12만원을 6개월 동안 지급",
        "aplyPrdSeCd": "0057002", "zipCd": "11110", "jobCd": "0013003",
        "sprtTrgtMinAge": "19", "sprtTrgtMaxAge": "39", "plcyPvsnMthdCd": "0042006",
        "ptcpPrpTrgtCn": "가상 타 사업 참여자는 신청 전 확인",
    }
    policy_id = "youth:youthcenter:" + number
    document = SourceDocument(
        "youthcenter:" + number, policy_id,
        "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/" + number,
        "sha256:" + "a" * 64, datetime(2026, 11, 15, 3, tzinfo=timezone.utc),
        project_youthcenter_source(fields), True,
    )
    return PolicyAnalysisInput(
        policy_id, (document,),
        (AnalysisPolicy(policy_id, fields["plcyNm"], ("employment",)),),
        (AnalysisSource("youthcenter", number, "a" * 64),),
    )


class MemoryFiles:
    def __init__(self) -> None:
        self.files = {}

    def save(self, path: Path, content: object) -> None:
        self.files[path] = deepcopy(content)

    def load(self, path: Path) -> dict | None:
        return deepcopy(self.files.get(path))


class MemoryDatabase:
    def __init__(self, identities=()) -> None:
        self.identities = [deepcopy(row) for row in identities]
        self.calls = []
        self.lookups = []
        self.bad = set()
        self.commit_then_fail = False

    def existing(self, policy_ids, external_ids):
        self.lookups.append((policy_ids, external_ids))
        return tuple(deepcopy(self.identities))

    def publish(self, entries, expected_previous, allow_ai, allow_unreviewed):
        self.calls.append({
            "ids": [entry.policy_id for entry in entries],
            "expected": dict(expected_previous), "allow_ai": allow_ai,
            "allow_unreviewed": allow_unreviewed,
        })
        if {entry.policy_id for entry in entries} & self.bad:
            raise ValueError("catalog_source_is_stale")
        counts = {"published": 0, "replayed": 0, "replaced": 0}
        for entry in entries:
            replaced = any(
                row["policy_id"] == entry.policy_id and row["publication_status"] == "published"
                for row in self.identities
            )
            for row in self.identities:
                if row["policy_id"] == entry.policy_id and row["publication_status"] == "published":
                    row["publication_status"] = "retired"
            self.identities.append({
                "policy_id": entry.policy_id, "document_version": entry.document_version,
                "record_sha256": entry.record_sha256, "publication_status": "published",
                "review_type": "ai", "review_status": "approved", "source": "youthcenter",
                "external_id": entry.policy_id.rsplit(":", 1)[-1],
            })
            counts["replaced" if replaced else "published"] += 1
        if self.commit_then_fail:
            self.commit_then_fail = False
            raise RuntimeError("postgresql://must-not-leak")
        return counts


def synthetic_compile(item: PolicyAnalysisInput) -> Compilation:
    digest = hashlib.sha256(item.policy_id.encode()).hexdigest()
    return Compilation(
        {"source_sha256": digest, "benefit": {"id": item.policy_id}},
        (),
        {"mode": "source-facts-v1", "model_calls": 0, "ai_review_calls": 0},
    )


def synthetic_prepare(compiled, source_text, as_of):
    digest = compiled.payload["source_sha256"]
    policy_id = compiled.payload["benefit"]["id"]
    return PreparedSourceCash(
        policy_id,
        "sha256:" + digest + ":source-facts-v1", digest, "source-facts-v1",
        {
            "benefit": {"id": policy_id, "source": {"checked_at": "compiled-at"}},
            "amount_won": 100000,
        },
        {"opaque": True}, source_text,
    )


def run(files, db, values, *, publish=True, batch_size=10, current_release=None,
        timestamp_replay=None):
    return run_source_cash_batch(
        values, output=Path("/virtual/source-cash"), as_of=TODAY,
        compile_source=synthetic_compile, prepare=synthetic_prepare,
        load_existing=db.existing, publish=db.publish if publish else None,
        load_current_release=current_release, timestamp_replay=timestamp_replay,
        save=files.save, load=files.load, batch_size=batch_size,
    )


class SourceCashBatchTest(unittest.TestCase):
    def test_real_source_cash_dry_run_checkpoints_without_model_or_publish(self):
        item = value("20260101000000000091")
        compiled = compile_source_cash(item, as_of=TODAY, regions=REGIONS)
        self.assertIsNotNone(compiled.payload, compiled.reasons)
        record = CatalogRecord.model_validate(compiled.payload)
        digest = record_hash(record)
        candidate = record.model_dump(mode="json")
        approved = deepcopy(candidate)

        def replace_checked_at(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key == "checked_at":
                        value[key] = "2026-11-15T15:32:50.664656+00:00"
                    else:
                        replace_checked_at(nested)
            elif isinstance(value, list):
                for nested in value:
                    replace_checked_at(nested)

        replace_checked_at(approved)
        approved_record = CatalogRecord.model_validate(approved)
        approved_digest = record_hash(approved_record)
        restored = restore_checked_at_only(candidate, approved_record.model_dump(mode="json"))
        self.assertIsNotNone(restored)
        self.assertEqual(record_hash(CatalogRecord.model_validate(restored)), approved_digest)
        changed = deepcopy(approved)
        changed["benefit"]["summary"] += " 실질 변경"
        self.assertIsNone(
            restore_checked_at_only(candidate, CatalogRecord.model_validate(changed).model_dump(
                mode="json"
            ))
        )
        source = record.benefit.source
        checked_at = datetime.fromisoformat(source.checked_at.replace("Z", "+00:00"))
        now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
        reviewed_at = source_cash_review_timestamp(
            checked_at=checked_at, now=now
        )
        review = AIReview(
            writer_agent=SOURCE_CASH_WRITER_AGENT,
            reviewer_agent=SOURCE_CASH_REVIEWER_AGENT,
            reviewer_model=SOURCE_CASH_REVIEW_METADATA,
            verdict="pass", reviewed_at=reviewed_at,
            document_version=source.document_version,
            source_sha256=record.source_sha256, record_sha256=digest,
        )

        def prepare(compiled_value, source_text, as_of):
            return PreparedSourceCash(
                item.policy_id, source.document_version, digest, "source-facts-v1",
                record, review, source_text,
            )

        files, database = MemoryFiles(), MemoryDatabase()
        report = run_source_cash_batch(
            (item,), output=Path("/virtual/source-cash"), as_of=TODAY,
            compile_source=lambda current: compile_source_cash(
                current, as_of=TODAY, regions=REGIONS
            ),
            prepare=prepare, load_existing=database.existing, publish=None,
            save=files.save, load=files.load,
            selection={
                "counts": {"targets": 4, "selected": 1, "held": 3, "out_of_scope": 4025},
                "reason_counts": {"mixed_official_category": 3},
            },
        )

        self.assertEqual(reviewed_at, now)
        self.assertEqual(report["counts"]["dry_run_ready"], 1)
        self.assertEqual(report["counts"]["published"], 0)
        self.assertEqual(report["counts"]["source_total"], 4029)
        self.assertEqual(report["counts"]["selection_held"], 3)
        self.assertEqual(report["counts"]["held"], 3)
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["ai_review_calls"], 0)
        self.assertEqual(review.reviewer_model, "deterministic-code:no-model-review")
        self.assertEqual(record.benefit.source.document_version.rsplit(":", 1)[-1], "source-facts-v1")
        checkpoints = [entry for path, entry in files.files.items() if path.name == "checkpoint.json"]
        self.assertEqual(checkpoints[0]["ai_verification"], "not_performed")

    def test_future_source_check_fails_and_review_time_is_not_as_of_midnight(self):
        now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
        with self.assertRaisesRegex(
            ValueError, "source_cash_batch_source_time_in_future"
        ):
            source_cash_review_timestamp(
                checked_at=now + timedelta(seconds=1), now=now
            )
        self.assertEqual(
            source_cash_review_timestamp(checked_at=now - timedelta(minutes=1), now=now),
            now,
        )

    def test_failed_batch_transaction_is_isolated_into_single_item_publishes(self):
        values = tuple(value(f"2026010100000000{n:04d}") for n in range(12))
        files, database = MemoryFiles(), MemoryDatabase()
        database.bad.add(values[3].policy_id)
        report = run(files, database, values, batch_size=10)

        self.assertEqual(report["counts"]["published"], 11)
        self.assertEqual(report["counts"]["failed"], 1)
        self.assertEqual(len(database.calls[0]["ids"]), 10)
        self.assertTrue(any(len(call["ids"]) == 1 for call in database.calls))
        failed = next(entry for entry in report["entries"] if entry["status"] == "failed")
        self.assertEqual(failed["reasons"], ["catalog_source_is_stale"])

    def test_human_ai_and_alias_history_are_protected_while_unreviewed_can_cas_replace(self):
        values = tuple(value(f"2026010200000000{n:04d}") for n in range(7))
        prepared = [synthetic_prepare(synthetic_compile(item), item.documents[0].text, TODAY)
                    for item in values]
        human, other_ai, same_ai, alias, unreviewed, timestamp_only, changed = values
        same_p, alias_p = prepared[2], prepared[3]
        timestamp_p, changed_p = prepared[5], prepared[6]
        identities = [
            {"policy_id": human.policy_id, "publication_status": "published",
             "review_type": "human", "review_status": "approved",
             "document_version": "old", "record_sha256": "1" * 64},
            {"policy_id": other_ai.policy_id, "publication_status": "published",
             "review_type": "ai", "review_status": "approved",
             "document_version": "sha256:" + "2" * 64 + ":decider-v3",
             "record_sha256": "2" * 64},
            {"policy_id": same_ai.policy_id, "publication_status": "published",
             "review_type": "ai", "review_status": "approved",
             "document_version": same_p.document_version,
             "record_sha256": same_p.record_sha256},
            {"policy_id": "youth:legacy:" + alias.policy_id.rsplit(":", 1)[-1],
             "publication_status": "retired", "review_type": "ai", "review_status": "approved",
             "document_version": "retired", "record_sha256": "3" * 64,
             "source": "youthcenter", "external_id": alias_p.policy_id.rsplit(":", 1)[-1]},
            {"policy_id": unreviewed.policy_id, "publication_status": "published",
             "review_type": "unreviewed", "review_status": "unreviewed",
             "document_version": "old-unreviewed", "record_sha256": "4" * 64},
            {"policy_id": timestamp_only.policy_id, "publication_status": "published",
             "review_type": "ai", "review_status": "approved",
             "document_version": timestamp_p.document_version,
             "record_sha256": "5" * 64},
            {"policy_id": changed.policy_id, "publication_status": "published",
             "review_type": "ai", "review_status": "approved",
             "document_version": changed_p.document_version,
             "record_sha256": "6" * 64},
        ]
        approved = {
            "benefit": {"id": "ignored", "source": {"checked_at": "approved-at"}},
            "amount_won": 100000,
        }

        def load_current(policy_id, document_version):
            payload = deepcopy(approved)
            payload["benefit"]["id"] = policy_id
            if policy_id == changed.policy_id:
                payload["amount_won"] = 200000
            return {
                "payload": payload, "publication_status": "published",
                "review_type": "ai", "review_status": "approved",
                "document_version": document_version,
            }

        def restore_timestamp(publication, current):
            return restore_checked_at_only(
                publication.record, current["payload"]
            ) is not None

        files, database = MemoryFiles(), MemoryDatabase(identities)
        report = run(
            files, database, values,
            current_release=load_current, timestamp_replay=restore_timestamp,
        )

        self.assertEqual(report["counts"]["held"], 4)
        self.assertEqual(report["counts"]["replayed"], 2)
        self.assertEqual(report["counts"]["replaced"], 1)
        self.assertEqual(database.calls[0]["expected"], {
            unreviewed.policy_id: "4" * 64,
        })
        self.assertTrue(database.calls[0]["allow_unreviewed"])
        timestamp_entry = next(
            entry for entry in report["entries"] if entry["policy_id"] == timestamp_only.policy_id
        )
        changed_entry = next(
            entry for entry in report["entries"] if entry["policy_id"] == changed.policy_id
        )
        self.assertEqual(timestamp_entry["replay_mode"], "checked_at_restored")
        self.assertEqual(changed_entry["reasons"], ["source_cash_batch_version_hash_conflict"])

    def test_committed_but_lost_response_is_reconciled_without_secret_or_duplicate(self):
        item = value("20260103000000000001")
        files, database = MemoryFiles(), MemoryDatabase()
        database.commit_then_fail = True
        first = run(files, database, (item,))
        files.files.clear()
        second = run(files, database, (item,))

        self.assertEqual(first["counts"]["replayed"], 1)
        self.assertEqual(first["counts"]["failed"], 0)
        self.assertEqual(second["counts"]["replayed"], 1)
        self.assertNotIn("postgresql://", str(first))
        self.assertEqual(len([
            row for row in database.identities
            if row["policy_id"] == item.policy_id and row["publication_status"] == "published"
        ]), 1)


if __name__ == "__main__":
    unittest.main()

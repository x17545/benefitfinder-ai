"""AI provenance checks use fictional records and cannot publish by themselves."""

from datetime import datetime, timezone
import hashlib
import json
from types import SimpleNamespace
import unittest

from pydantic import ValidationError

from benefitfinder.infrastructure.ai_catalog_review import (
    AIReview,
    _expected_previous_hashes,
    _verify_real_source,
    project_youthcenter_source,
    publish_ai_batch,
    validate_ai_review,
)
from benefitfinder.infrastructure.policy_catalog_records import (
    CatalogRecord, CollectionSource, record_hash,
)
from backend.tests.catalog_fixtures import SOURCE_TEXT, release_data


class SourceConnection:
    def __init__(self, observed):
        self.observed = observed

    def execute(self, statement, parameters):
        self.statement = statement
        self.parameters = parameters
        return self

    def fetchone(self):
        return self.observed


OFFICIAL_API_ID = "20260101000000000021"
OFFICIAL_DOCUMENT_ID = "fictional:primary-notice"


def official_publication_fixture(
    *, include_api: bool = False, api_first: bool = False,
    policy_id: str = "youth:" + OFFICIAL_API_ID,
) -> tuple[CatalogRecord, str, dict]:
    """Fictional source rows, with an official heading distinct from the API name."""
    data = release_data("official-primary")
    text = "[가상] 종합 사업 공고\n" + SOURCE_TEXT
    fields = {
        "policy_id": policy_id,
        "categories": ["housing"],
        "source_url": "https://example.test/fictional-primary-notice",
        "text_status": "ready",
        "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "title": "[가상] 종합 사업 공고",
    }
    material = hashlib.sha256(json.dumps(
        fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    data["benefit"].update(id=policy_id, title="[가상] 세부 주거 지원", virtual_data=False)
    primary = {
        "url": fields["source_url"],
        "document_version": "official-v1:" + material,
        "checked_at": data["benefit"]["source"]["checked_at"],
    }
    data["benefit"]["source"] = primary
    for evidence in data["payment_evidence"] + [
        item for condition in data["benefit"]["eligibility_conditions"]
        for item in condition["evidence"]
    ]:
        evidence["source"] = primary.copy()
    data["source_sha256"] = fields["text_sha256"]
    data["collection_sources"] = [{
        "source": "official_documents", "external_id": OFFICIAL_DOCUMENT_ID,
        "material_sha256": material,
    }]
    rows = {("official_documents", OFFICIAL_DOCUMENT_ID): (fields, material, None)}
    if include_api:
        api_fields = {
            "plcyNo": OFFICIAL_API_ID, "plcyNm": data["benefit"]["title"],
            "lclsfNm": "주거", "plcySprtCn": "[가상] API 요약은 공식 원문과 다릅니다.",
        }
        api_material = hashlib.sha256(json.dumps(
            api_fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        data["collection_sources"].append({
            "source": "youthcenter", "external_id": OFFICIAL_API_ID,
            "material_sha256": api_material,
        })
        rows[("youthcenter", OFFICIAL_API_ID)] = (api_fields, api_material, None)
        if api_first:
            data["collection_sources"].reverse()
    return CatalogRecord.model_validate(data), text, rows


class BoundSourceConnection:
    def __init__(self, rows: dict) -> None:
        self.rows = rows

    def execute(self, statement: str, parameters: tuple[str, str]) -> SourceConnection:
        return SourceConnection(self.rows.get(parameters))


class OfficialPrimarySourceTest(unittest.TestCase):
    def test_exact_official_primary_with_optional_api_in_either_order(self) -> None:
        for include_api, api_first in ((False, False), (True, False), (True, True)):
            with self.subTest(include_api=include_api, api_first=api_first):
                record, text, rows = official_publication_fixture(
                    include_api=include_api, api_first=api_first,
                )
                _verify_real_source(BoundSourceConnection(rows), record, text)

    def test_official_primary_without_api_accepts_its_bound_policy_identity(self) -> None:
        record, text, rows = official_publication_fixture(policy_id="youth:official:fictional")
        _verify_real_source(BoundSourceConnection(rows), record, text)

    def test_both_numeric_api_policy_aliases_remain_valid(self) -> None:
        for policy_id in ("youth:" + OFFICIAL_API_ID, "youth:youthcenter:" + OFFICIAL_API_ID):
            with self.subTest(policy_id=policy_id):
                record, text, rows = official_publication_fixture(
                    include_api=True, policy_id=policy_id,
                )
                _verify_real_source(BoundSourceConnection(rows), record, text)

    def test_missing_stale_or_forged_bindings_reject_each_source(self) -> None:
        record, text, rows = official_publication_fixture(include_api=True)
        for key, (fields, material, _) in rows.items():
            for state in (None, (fields, "0" * 64, None), (fields, material, self)):
                with self.subTest(source=key, state=state is None), self.assertRaisesRegex(
                    ValueError, "ai_catalog_source_stale",
                ):
                    _verify_real_source(BoundSourceConnection(rows | {key: state}), record, text)

    def test_official_content_identity_hash_and_categories_must_match(self) -> None:
        record, text, rows = official_publication_fixture()
        key = ("official_documents", OFFICIAL_DOCUMENT_ID)
        fields, material, _ = rows[key]
        for change in (
            {"text_status": "requires_review"}, {"policy_id": "youth:other"},
            {"source_url": "https://example.test/other"}, {"text": text + " changed"},
            {"text_sha256": "0" * 64}, {"categories": []}, {"categories": None},
            {"categories": ["employment"]}, {"categories": ["housing", "employment"]},
            {"categories": ["housing", "housing"]}, {"categories": ["education"]},
            {"categories": [{}]},
        ):
            with self.subTest(change=change), self.assertRaisesRegex(
                ValueError, "ai_catalog_source_projection_mismatch",
            ):
                _verify_real_source(
                    BoundSourceConnection(rows | {key: (fields | change, material, None)}),
                    record, text,
                )
        for name in ("text_status", "policy_id", "source_url", "text", "text_sha256", "categories"):
            incomplete = {key: value for key, value in fields.items() if key != name}
            with self.subTest(missing=name), self.assertRaisesRegex(
                ValueError, "ai_catalog_source_projection_mismatch",
            ):
                _verify_real_source(
                    BoundSourceConnection(rows | {key: (incomplete, material, None)}), record, text,
                )

    def test_official_document_version_cannot_be_forged(self) -> None:
        record, text, rows = official_publication_fixture()
        for version in (
            "official-v1:" + "0" * 64,
            record.benefit.source.document_version + ":decider-v3",
            record.benefit.source.document_version.replace("official-v1:", "sha256:"),
        ):
            changed = record.model_copy(update={"benefit": record.benefit.model_copy(update={
                "source": record.benefit.source.model_copy(update={"document_version": version}),
            })})
            with self.subTest(version=version), self.assertRaisesRegex(
                ValueError, "ai_catalog_source_projection_mismatch",
            ):
                _verify_real_source(BoundSourceConnection(rows), changed, text)

    def test_official_cash_rule_revision_keeps_the_exact_material_binding(self) -> None:
        record, text, rows = official_publication_fixture()
        source = record.benefit.source
        for suffix in (":cash-rules-v1", ":cash-rules-v2"):
            changed = record.model_copy(update={"benefit": record.benefit.model_copy(update={
                "source": source.model_copy(update={"document_version": source.document_version + suffix}),
            })})
            _verify_real_source(BoundSourceConnection(rows), changed, text)
            wrong = changed.model_copy(update={"benefit": changed.benefit.model_copy(update={
                "source": source.model_copy(update={"document_version": "official-v1:" + "0" * 64 + suffix}),
            })})
            with self.subTest(suffix=suffix), self.assertRaisesRegex(
                ValueError, "ai_catalog_source_projection_mismatch",
            ):
                _verify_real_source(BoundSourceConnection(rows), wrong, text)

    def test_primary_cannot_cover_fewer_categories_than_the_release(self) -> None:
        record, text, rows = official_publication_fixture()
        changed = record.model_copy(update={"benefit": record.benefit.model_copy(update={
            "categories": ["employment", "housing"],
        })})
        with self.assertRaisesRegex(ValueError, "ai_catalog_source_projection_mismatch"):
            _verify_real_source(BoundSourceConnection(rows), changed, text)

    def test_employment_api_subcategory_stays_within_the_existing_mvp_scope(self) -> None:
        record, text, rows = official_publication_fixture(include_api=True)
        record = record.model_copy(update={"benefit": record.benefit.model_copy(update={
            "categories": ["employment"],
        })})
        key = ("official_documents", OFFICIAL_DOCUMENT_ID)
        fields, material, _ = rows[key]
        rows[key] = (fields | {"categories": ["employment"]}, material, None)
        api_key = ("youthcenter", OFFICIAL_API_ID)
        fields, material, _ = rows[api_key]
        for subcategory in ("취업", "재직자", "권익보호", "취업,권익보호"):
            with self.subTest(subcategory=subcategory):
                _verify_real_source(BoundSourceConnection(rows | {api_key: (
                    fields | {"lclsfNm": "일자리", "mclsfNm": subcategory}, material, None,
                )}), record, text)
        for subcategory in (None, "", "창업", "취업,창업"):
            with self.subTest(subcategory=subcategory), self.assertRaisesRegex(
                ValueError, "ai_catalog_source_projection_mismatch",
            ):
                _verify_real_source(BoundSourceConnection(rows | {api_key: (
                    fields | {"lclsfNm": "일자리", "mclsfNm": subcategory}, material, None,
                )}), record, text)

    def test_api_metadata_identity_and_scope_are_checked_without_replacing_primary(self) -> None:
        record, text, rows = official_publication_fixture(include_api=True)
        key = ("youthcenter", OFFICIAL_API_ID)
        fields, material, _ = rows[key]
        for change in (
            {"plcyNo": "999"}, {"plcyNm": "[가상] 다른 세부사업"},
            {"lclsfNm": "교육"}, {"lclsfNm": "주거,교육"}, {"lclsfNm": None},
            {"lclsfNm": ""}, {"lclsfNm": "일자리", "mclsfNm": "취업"},
            {"plcySprtCn": ["invalid"]},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                _verify_real_source(
                    BoundSourceConnection(rows | {key: (fields | change, material, None)}),
                    record, text,
                )
        wrong_id, text, rows = official_publication_fixture(
            include_api=True, policy_id="youth:other:" + OFFICIAL_API_ID,
        )
        with self.assertRaisesRegex(ValueError, "ai_catalog_source_projection_mismatch"):
            _verify_real_source(BoundSourceConnection(rows), wrong_id, text)

    def test_extra_duplicate_or_incomplete_source_sets_are_rejected(self) -> None:
        record, text, rows = official_publication_fixture(include_api=True)
        official, api = record.collection_sources
        other_official = official.model_copy(update={"external_id": "fictional:another-notice"})
        unsupported = CollectionSource.model_validate({
            "source": "gov24", "external_id": "fictional:other", "material_sha256": "0" * 64,
        })
        for bindings in (
            [], [api], [official, official], [official, other_official],
            [official, api, api], [official, unsupported], [official, api, unsupported],
        ):
            with self.subTest(sources=[item.source for item in bindings]), self.assertRaises(ValueError):
                _verify_real_source(
                    BoundSourceConnection(rows), record.model_copy(update={"collection_sources": bindings}),
                    text,
                )


class AIReviewTest(unittest.TestCase):
    def setUp(self):
        self.record = CatalogRecord.model_validate(release_data())
        self.fields = {
            "writer_agent": "writer-1", "reviewer_agent": "reviewer-1", "reviewer_model": "test-model",
            "verdict": "pass", "reviewed_at": "2026-11-02T00:00:00+00:00",
            "document_version": "fictional-v1", "source_sha256": self.record.source_sha256,
            "record_sha256": record_hash(self.record),
        }
        self.now = datetime(2026, 11, 3, tzinfo=timezone.utc)

    def check(self, fields=None, source=SOURCE_TEXT):
        validate_ai_review(self.record, AIReview.model_validate(fields or self.fields), source_text=source, now=self.now)

    def test_pass_and_source_invalidation(self):
        self.check()
        with self.assertRaises(ValueError):
            self.check(source=SOURCE_TEXT + " 변경")

    def test_rejects_failed_pending_and_changed_record_or_source(self):
        for field, value in (("verdict", "revise"), ("verdict", "hold"), ("record_sha256", "0" * 64),
                             ("source_sha256", "0" * 64), ("document_version", "other")):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.check({**self.fields, field: value})

    def test_independent_reviewer_and_valid_times(self):
        with self.assertRaises(ValidationError):
            self.check({**self.fields, "reviewer_agent": "writer-1"})
        for value in ("2026-10-01T00:00:00+00:00", "2026-12-01T00:00:00+00:00"):
            with self.subTest(reviewed_at=value), self.assertRaises(ValueError):
                self.check({**self.fields, "reviewed_at": value})

    def test_real_source_accepts_only_exact_youthcenter_policy_ids(self):
        external_id = "20260101000000000001"
        material = "a" * 64
        title = "[가상] 준비 큐 정책"
        fields = {"plcyNo": external_id, "plcyNm": title, "plcySprtCn": "[가상] 지원 내용"}
        source_text = project_youthcenter_source(fields)
        binding = SimpleNamespace(
            source="youthcenter", external_id=external_id, material_sha256=material
        )

        def record(policy_id, document_version="sha256:" + material):
            benefit = SimpleNamespace(
                id=policy_id,
                title=title,
                virtual_data=False,
                source=SimpleNamespace(
                    url="https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/"
                    + external_id,
                    document_version=document_version,
                ),
            )
            return SimpleNamespace(benefit=benefit, collection_sources=(binding,))

        observed = (fields, material, None)
        for policy_id in ("youth:" + external_id, "youth:youthcenter:" + external_id):
            for document_version in (
                "sha256:" + material,
                "sha256:" + material + ":decider-v3",
                "sha256:" + material + ":decider-expense-v2",
                "sha256:" + material + ":decider-compact-v1",
                "sha256:" + material + ":decider-reimburse-v1",
                "sha256:" + material + ":decider-fixed-v1",
                "sha256:" + material + ":source-facts-v1",
            ):
                with self.subTest(policy_id=policy_id, document_version=document_version):
                    _verify_real_source(
                        SourceConnection(observed),
                        record(policy_id, document_version),
                        source_text,
                    )
        for policy_id in (
            "youth:other:" + external_id,
            "youth:" + external_id + "0",
            "youth:youthcenter:" + external_id + "0",
        ):
            with self.subTest(policy_id=policy_id), self.assertRaisesRegex(
                ValueError, "ai_catalog_source_projection_mismatch"
            ):
                _verify_real_source(SourceConnection(observed), record(policy_id), source_text)
        other_source = SimpleNamespace(
            source="gov24", external_id=external_id, material_sha256=material
        )
        record_with_other_source = record("youth:" + external_id)
        record_with_other_source.collection_sources = (other_source,)
        with self.assertRaisesRegex(ValueError, "ai_catalog_youthcenter_source_required"):
            _verify_real_source(SourceConnection(observed), record_with_other_source, source_text)

    def test_real_source_rejects_forged_normalization_versions(self):
        external_id = "20260101000000000001"
        material = "a" * 64
        title = "[가상] 준비 큐 정책"
        fields = {"plcyNo": external_id, "plcyNm": title, "plcySprtCn": "[가상] 지원 내용"}
        source_text = project_youthcenter_source(fields)
        binding = SimpleNamespace(
            source="youthcenter", external_id=external_id, material_sha256=material
        )

        def record(document_version):
            benefit = SimpleNamespace(
                id="youth:" + external_id,
                title=title,
                virtual_data=False,
                source=SimpleNamespace(
                    url="https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/"
                    + external_id,
                    document_version=document_version,
                ),
            )
            return SimpleNamespace(benefit=benefit, collection_sources=(binding,))

        observed = (fields, material, None)
        for document_version in (
            "sha256:" + material + ":decider-v2",
            "sha256:" + material + ":decider-v3:forged",
            "sha256:" + "b" * 64 + ":decider-v3",
            "sha256:" + material + ":Decider-v3",
        ):
            with self.subTest(document_version=document_version), self.assertRaisesRegex(
                ValueError, "ai_catalog_source_projection_mismatch"
            ):
                _verify_real_source(SourceConnection(observed), record(document_version), source_text)

    def test_projection_includes_critical_decider_v3_fields(self):
        fields = {
            "plcyNo": "20260101000000000001",
            "plcyNm": "[가상] 준비 큐 정책",
            "earnCndSeCd": "0043001",
            "mrgSttsCd": "0055003",
            "sbizCd": "0014004",
            "plcyPvsnMthdCd": "0041002",
            "sprtArvlSeqYn": "Y",
            "sprtSclCnt": 10,
            "sprtSclLmtYn": "Y",
            "plcyMajorCd": "0011001,0011002",
            "bizPrdSeCd": "0056001",
        }
        projected = project_youthcenter_source(fields)
        for name in (
            "earnCndSeCd",
            "mrgSttsCd",
            "sbizCd",
            "plcyPvsnMthdCd",
            "sprtArvlSeqYn",
            "sprtSclCnt",
            "sprtSclLmtYn",
            "plcyMajorCd",
            "bizPrdSeCd",
        ):
            with self.subTest(name=name):
                self.assertIn(f"[{name}]\n{fields[name]}", projected)

    def test_replacement_arguments_are_validated_before_database_access(self):
        entry = (self.record, AIReview.model_validate(self.fields), SOURCE_TEXT, None)
        previous = {self.record.benefit.id: record_hash(self.record)}
        with self.assertRaisesRegex(ValueError, "ai_catalog_replacement_not_allowed"):
            publish_ai_batch("unused", [entry], expected_previous=previous)
        for expected_previous in ({self.record.benefit.id: "0" * 63}, {"youth:other": "0" * 64}):
            with self.subTest(expected_previous=expected_previous), self.assertRaisesRegex(
                ValueError, "invalid_ai_catalog_expected_previous"
            ):
                publish_ai_batch(
                    "unused", [entry], allow_replace_ai=True, expected_previous=expected_previous
                )

    def test_expected_previous_is_allowed_by_either_replacement_option(self):
        policy_id = self.record.benefit.id
        previous = {policy_id: record_hash(self.record)}
        for options in (
            {"allow_replace_ai": True, "allow_replace_unreviewed": False},
            {"allow_replace_ai": False, "allow_replace_unreviewed": True},
        ):
            with self.subTest(options=options):
                self.assertEqual(
                    _expected_previous_hashes(
                        {policy_id}, expected_previous=previous, **options
                    ),
                    previous,
                )
        with self.assertRaisesRegex(ValueError, "ai_catalog_replacement_not_allowed"):
            _expected_previous_hashes(
                {policy_id},
                allow_replace_ai=False,
                allow_replace_unreviewed=False,
                expected_previous=previous,
            )


if __name__ == "__main__":
    unittest.main()

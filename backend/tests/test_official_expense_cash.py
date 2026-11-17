"""Fictional official-document tests for dynamic actual-expense cash rules.

No database, network, model call, environment value or real notice is used.
"""

from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib
from types import SimpleNamespace
import unittest

from benefitfinder.api.schemas.cash_facts import CashFacts, SourceCashFacts
from benefitfinder.application.policy_analysis import (
    AnalysisDocumentSource,
    AnalysisPolicy,
    AnalysisSource,
    PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.region_options import DistrictOption, RegionOption
from benefitfinder.domains.youth.source_cash import source_cash_payments
from benefitfinder.infrastructure.ai_catalog_review import project_youthcenter_source
from benefitfinder.ingestion.official_expense_cash import compile_official_expense_cash


ID = "999000123"
POLICY_ID = "youth:youthcenter:" + ID
POLICY_NAME = "가상 청년 응시료 지원사업"
EXPENSE_LINE = "응시료 최대 10만원 한도 실비 지원"
APPLICATION_LINE = "2026. 1. 1. ~ 2026. 12. 31."
OFFICIAL_TEXT = (
    f"{POLICY_NAME}\n"
    "[가상 공고]\n\n"
    "□ 지원대상\n"
    "- 가상시에 주민등록을 둔 만 19세 이상 39세 이하 청년\n\n"
    "□ 신청자격\n"
    "- 가상시 거주 청년으로 응시료를 실제로 납부한 자\n\n"
    "□ 지원내용\n"
    f"- {EXPENSE_LINE}\n\n"
    "□ 신청기간\n"
    f"- {APPLICATION_LINE}\n\n"
    "□ 신청방법\n"
    "- 가상시 청년포털에서 본인이 신청\n\n"
    "□ 기타 유의사항\n"
    "- 지원 여부는 제출 서류 확인 후 결정\n"
)
REGIONS = (
    RegionOption(
        "99",
        "가상도",
        (
            DistrictOption("99110", "가상시"),
            DistrictOption("99200", "가상군"),
        ),
    ),
    RegionOption(
        "88",
        "다른도",
        (DistrictOption("88110", "다른시"),),
    ),
)
COLLIDING_REGIONS = (
    RegionOption(
        "91",
        "가상도",
        (
            DistrictOption("91110", "서구"),
            DistrictOption("91200", "가상시"),
        ),
    ),
    RegionOption(
        "92",
        "다른도",
        (
            DistrictOption("92110", "서구"),
            DistrictOption("92200", "다른시"),
        ),
    ),
)


def api_fields(*, remove=(), **changes):
    values = {
        "plcyNo": ID,
        "plcyNm": POLICY_NAME,
        "lclsfNm": "일자리",
        "mclsfNm": "취업",
        "rgtrInstCdNm": "가상시",
        "sprtTrgtAgeLmtYn": "N",
        "sprtTrgtMinAge": "19",
        "sprtTrgtMaxAge": "39",
        "zipCd": "99110",
        "aplyPrdSeCd": "0057001",
        "aplyYmd": "20260101 ~ 20261231",
        "plcySprtCn": EXPENSE_LINE,
        "ptcpPrpTrgtCn": "가상 제출 서류 미비자는 제외",
    }
    values.update(changes)
    for name in remove:
        values.pop(name, None)
    return values


def sample(*, official_text=None, fields=None, target_name=None):
    fields = fields or api_fields()
    official_text = official_text or OFFICIAL_TEXT
    api_text = project_youthcenter_source(fields)
    official_hash = hashlib.sha256(official_text.encode()).hexdigest()
    api_hash = hashlib.sha256(api_text.encode()).hexdigest()
    collected_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    official_id = "fictional-expense-notice"
    api_document_id = "youthcenter:" + ID
    official = SourceDocument(
        official_id,
        POLICY_ID,
        "https://official.example.test/expense-notice",
        "official-v1:" + official_hash,
        collected_at,
        official_text,
        True,
    )
    projected = SourceDocument(
        api_document_id,
        POLICY_ID,
        "https://api.example.test/youth-policy/" + ID,
        "sha256:" + api_hash,
        collected_at,
        api_text,
        True,
    )
    return PolicyAnalysisInput(
        POLICY_ID,
        (official, projected),
        (
            AnalysisPolicy(
                POLICY_ID,
                target_name or fields["plcyNm"],
                ("employment",),
            ),
        ),
        (
            AnalysisSource("official_documents", official_id, official_hash),
            AnalysisSource("youthcenter", ID, api_hash),
        ),
        (
            AnalysisDocumentSource(
                official_id,
                "official_documents",
                official_id,
            ),
            AnalysisDocumentSource(api_document_id, "youthcenter", ID),
        ),
    )


def source_cash_rule_native(payload):
    rules = [
        condition for condition in payload["conditions"] if condition.get("kind") == "source_cash"
    ]
    if len(rules) != 1:
        raise AssertionError(f"expected one source_cash rule, got {len(rules)}")
    rule = rules[0].get("source_cash")
    if not isinstance(rule, dict):
        raise AssertionError("source_cash condition requires a nested rule")
    return rule


def official_evidence(payload, source_url):
    evidence = []
    for condition in payload["benefit"]["eligibility_conditions"]:
        evidence.extend(condition.get("evidence") or ())
    evidence.extend(payload.get("payment_evidence") or ())
    unique = {}
    for item in evidence:
        if (item.get("source") or {}).get("url") == source_url:
            unique[(item["location"], item["quote"])] = item
    return sorted(
        unique.values(),
        key=lambda item: int(item["location"].removeprefix("chars:").split("-", 1)[0]),
    )


class OfficialExpenseCashSuccessTest(unittest.TestCase):
    def compile(self, value=None, *, as_of=date(2026, 6, 1), regions=REGIONS):
        value = value or sample()
        result = compile_official_expense_cash(
            value,
            as_of=as_of,
            regions=regions,
        )
        return value, result

    def test_single_actual_expense_cap_becomes_dynamic_source_cash(self):
        value, result = self.compile()

        self.assertEqual(result.reasons, ())
        self.assertIsNotNone(result.payload)
        self.assertTrue(result.normalized["calculation_ready"])
        self.assertEqual(result.normalized.get("model_calls", 0), 0)
        self.assertEqual(result.normalized.get("ai_review_calls", 0), 0)

        payload = result.payload
        rule = source_cash_rule_native(payload)
        self.assertEqual(rule["policy_id"], POLICY_ID)
        self.assertEqual(rule["mode"], "expense")
        self.assertEqual(rule["amount_won"], 100_000)
        self.assertEqual(rule["reimbursement_bps"], 10_000)
        self.assertIsNone(payload["payments"])
        self.assertTrue(payload["benefit"]["reviewed"])
        self.assertFalse(payload["unresolved_combination_rules"])
        self.assertEqual(
            payload["benefit"]["source"]["document_version"],
            value.documents[0].document_version + ":cash-rules-v2",
        )
        self.assertEqual(
            payload["source_sha256"],
            hashlib.sha256(value.documents[0].text.encode()).hexdigest(),
        )
        self.assertEqual(
            len(payload["collection_sources"]),
            2,
        )
        self.assertEqual(
            payload["benefit"]["application"],
            {
                "status": "open",
                "starts_on": "2026-01-01",
                "ends_on": "2026-12-31",
                "url": None,
            },
        )

        policy = SimpleNamespace(
            policy_id=POLICY_ID,
            conditions=tuple(payload["conditions"]),
            payments=payload["payments"],
        )
        profile = SimpleNamespace(
            domain_data=SimpleNamespace(
                cash_facts=CashFacts(
                    source_cash=[
                        SourceCashFacts(
                            policy_id=POLICY_ID,
                            payment_month="2026-06",
                            eligible_cost_won=50_000,
                            prior_support_won=0,
                            other_reimbursement_won=0,
                            other_exclusions_clear=True,
                        )
                    ]
                )
            )
        )
        self.assertEqual(
            source_cash_payments(profile, policy, date(2026, 6, 1)),
            ((0, 50_000),),
        )

    def test_full_official_text_and_money_are_reconstructible_from_char_spans(self):
        value, result = self.compile()
        payload = result.payload
        evidence = official_evidence(
            payload,
            value.documents[0].source_url,
        )
        self.assertTrue(evidence)
        cursor = 0
        saw_expense = False
        for item in evidence:
            location = item["location"]
            self.assertTrue(location.startswith("chars:"))
            start_text, end_text = location.removeprefix("chars:").split("-", 1)
            start, end = int(start_text), int(end_text)
            self.assertLessEqual(len(item["quote"]), 2500)
            self.assertEqual(
                value.documents[0].text[start:end],
                item["quote"],
            )
            self.assertLessEqual(start, cursor)
            cursor = max(cursor, end)
            saw_expense = saw_expense or EXPENSE_LINE in item["quote"]
        self.assertEqual(cursor, len(value.documents[0].text))
        self.assertTrue(saw_expense)

        manual_ids = {
            rule["condition_id"] for rule in payload["conditions"] if rule.get("kind") == "manual"
        }
        manual_evidence = [
            evidence_item
            for condition in payload["benefit"]["eligibility_conditions"]
            if condition["id"] in manual_ids
            for evidence_item in condition.get("evidence") or ()
        ]
        self.assertTrue(
            any(item["source"]["url"] == value.documents[0].source_url for item in manual_evidence)
        )

    def test_application_status_uses_as_of(self):
        for day, status in (
            (date(2025, 12, 31), "upcoming"),
            (date(2026, 6, 1), "open"),
            (date(2027, 1, 1), "closed"),
        ):
            with self.subTest(day=day):
                _, result = self.compile(as_of=day)
                self.assertIsNotNone(result.payload)
                self.assertEqual(
                    result.payload["benefit"]["application"]["status"],
                    status,
                )

    def test_verified_region_links_api_code_and_resident_text(self):
        _, result = self.compile()
        payload = result.payload
        region_rules = [
            rule
            for rule in payload["conditions"]
            if rule.get("kind") == "selection" and rule.get("field") == "region_code"
        ]
        self.assertEqual(len(region_rules), 1)
        self.assertEqual(region_rules[0]["values"], ["99110"])

    def test_lifetime_once_keeps_received_rule_and_policy_flag(self):
        value = sample(official_text=OFFICIAL_TEXT + "\n□ 수혜 이력\n- 생애 1회만 지원\n")
        _, result = self.compile(value)
        self.assertEqual(result.reasons, ())
        payload = result.payload
        self.assertIs(payload["benefit"]["lifetime_once"], True)
        received_rules = [
            rule
            for rule in payload["conditions"]
            if rule.get("kind") == "none_of" and rule.get("field") == "domain_data.received"
        ]
        self.assertEqual(
            received_rules,
            [
                {
                    "condition_id": "no_previous_grant",
                    "kind": "none_of",
                    "field": "domain_data.received",
                    "values": [POLICY_ID],
                }
            ],
        )
        condition = next(
            item
            for item in payload["benefit"]["eligibility_conditions"]
            if item["id"] == "no_previous_grant"
        )
        evidence = condition["evidence"][0]
        start, end = map(
            int,
            evidence["location"].removeprefix("chars:").split("-", 1),
        )
        self.assertEqual(value.documents[0].text[start:end], evidence["quote"])
        self.assertIn("생애 1회", evidence["quote"])


class OfficialExpenseCashHoldTest(unittest.TestCase):
    def assert_held(
        self,
        value,
        *,
        as_of=date(2026, 6, 1),
        regions=REGIONS,
    ):
        result = compile_official_expense_cash(
            value,
            as_of=as_of,
            regions=regions,
        )
        self.assertIsNone(result.payload)
        self.assertTrue(result.reasons)
        self.assertFalse(result.normalized.get("calculation_ready", False))

    def test_exact_official_and_youthcenter_bindings_are_required(self):
        value = sample()
        official, projected = value.documents
        official_source, api_source = value.collection_sources
        official_binding, api_binding = value.document_sources
        cases = {
            "missing_projection": replace(
                value,
                documents=(official,),
                collection_sources=(official_source,),
                document_sources=(official_binding,),
            ),
            "missing_official": replace(
                value,
                documents=(projected,),
                collection_sources=(api_source,),
                document_sources=(api_binding,),
            ),
            "official_version": replace(
                value,
                documents=(
                    replace(official, document_version="official-v1:" + "0" * 64),
                    projected,
                ),
            ),
            "projection_version": replace(
                value,
                documents=(
                    official,
                    replace(projected, document_version="sha256:" + "0" * 64),
                ),
            ),
            "binding_external_id": replace(
                value,
                collection_sources=(
                    official_source,
                    replace(api_source, external_id="999999999"),
                ),
                document_sources=(
                    official_binding,
                    replace(api_binding, external_id="999999999"),
                ),
            ),
        }
        for name, candidate in cases.items():
            with self.subTest(name=name):
                self.assert_held(candidate)

    def test_target_identity_eligibility_and_application_are_required(self):
        cases = {
            "target_name": sample(target_name="다른 가상 지원사업"),
            "api_id": sample(fields=api_fields(plcyNo="888000123")),
            "official_name": sample(
                official_text=OFFICIAL_TEXT.replace(
                    POLICY_NAME,
                    "다른 가상 응시료 지원사업",
                )
            ),
            "eligibility_heading": sample(
                official_text=OFFICIAL_TEXT.replace(
                    "□ 지원대상\n"
                    "- 가상시에 주민등록을 둔 만 19세 이상 39세 이하 청년\n\n"
                    "□ 신청자격\n"
                    "- 가상시 거주 청년으로 응시료를 실제로 납부한 자\n\n",
                    "",
                )
            ),
            "application_body": sample(
                official_text=OFFICIAL_TEXT.replace(
                    "□ 신청방법\n- 가상시 청년포털에서 본인이 신청\n\n",
                    "",
                )
            ),
        }
        for name, candidate in cases.items():
            with self.subTest(name=name):
                self.assert_held(candidate)

    def test_notice_and_api_recruitment_dates_must_match(self):
        cases = {
            "api_mismatch": sample(fields=api_fields(aplyYmd="20260101 ~ 20260630")),
            "notice_mismatch": sample(
                official_text=OFFICIAL_TEXT.replace(
                    APPLICATION_LINE,
                    "2027. 1. 1. ~ 2027. 12. 31.",
                )
            ),
            "invalid_notice_date": sample(
                official_text=OFFICIAL_TEXT.replace(
                    APPLICATION_LINE,
                    "2026. 2. 30. ~ 2026. 12. 31.",
                )
            ),
            "invalid_api_date": sample(fields=api_fields(aplyYmd="20260230 ~ 20261231")),
            "unknown_api_period": sample(fields=api_fields(aplyYmd="", aplyPrdSeCd="9999999")),
        }
        for name, candidate in cases.items():
            with self.subTest(name=name):
                self.assert_held(candidate)

    def test_region_code_and_resident_registration_text_must_agree(self):
        cases = (
            (
                "unknown_code",
                sample(fields=api_fields(zipCd="99999")),
                REGIONS,
            ),
            (
                "notice_region_mismatch",
                sample(fields=api_fields(zipCd="88110")),
                REGIONS,
            ),
            (
                "missing_resident_evidence",
                sample(
                    official_text=OFFICIAL_TEXT.replace(
                        "가상시에 주민등록을 둔",
                        "가상 지역에 거주하는",
                    ).replace(
                        "가상시 거주 청년",
                        "해당 지역 거주 청년",
                    )
                ),
                REGIONS,
            ),
            (
                "missing_catalogue",
                sample(),
                (),
            ),
        )
        for name, candidate, regions in cases:
            with self.subTest(
                name=name,
            ):
                self.assert_held(candidate, regions=regions)

    def test_ambiguous_district_name_without_province_is_held(self):
        district_names = [
            district.name for region in COLLIDING_REGIONS for district in region.sigungu
        ]
        self.assertEqual(district_names.count("서구"), 2)
        value = sample(
            fields=api_fields(zipCd="91110", rgtrInstCdNm="가상도 서구"),
            official_text=OFFICIAL_TEXT.replace(
                "가상시에 주민등록을 둔",
                "서구에 주민등록을 둔",
            ).replace(
                "가상시 거주 청년",
                "서구 거주 청년",
            ),
        )
        self.assert_held(value, regions=COLLIDING_REGIONS)

    def test_single_actual_expense_cap_is_required(self):
        cases = {
            "no_expense": EXPENSE_LINE.replace(" 실비", ""),
            "no_cap": "응시료 실비 지원",
            "multiple_caps": "응시료 최대 10만원 또는 최대 20만원 한도 실비 지원",
            "multiple_expenses": "응시료와 교통비 각각 최대 10만원 한도 실비 지원",
            "family_brackets": "가족 수에 따라 응시료 최대 10만원, 20만원 한도 실비 지원",
            "rate": "응시료의 80% 한도 실비 지원",
            "loan": "응시료 대출 이자 최대 10만원 한도 지원",
        }
        for name, replacement in cases.items():
            with self.subTest(name=name):
                self.assert_held(
                    sample(
                        official_text=OFFICIAL_TEXT.replace(
                            EXPENSE_LINE,
                            replacement,
                        )
                    )
                )

    def test_different_cap_in_later_payment_clause_is_not_ignored(self):
        self.assert_held(sample(official_text=OFFICIAL_TEXT
                                + "\n□ 추가 지급 기준\n- 1회 지급액의 상한은 5만원입니다.\n"))

    def test_overlap_and_unmapped_program_exclusions_are_held(self):
        cases = (
            (
                "overlap",
                sample(
                    official_text=OFFICIAL_TEXT
                    + "\n□ 제외조건\n- 가상 다른 지원금과 중복 수급 불가\n"
                ),
            ),
            (
                "other_organization",
                sample(
                    official_text=OFFICIAL_TEXT + "\n□ 제외조건\n- 타 기관 지원사업 참여자는 제외\n"
                ),
            ),
            (
                "other_local_government",
                sample(
                    official_text=OFFICIAL_TEXT
                    + "\n□ 제외조건\n- 타 지자체 동일 사업 참여자는 제외\n"
                ),
            ),
            (
                "participant",
                sample(official_text=OFFICIAL_TEXT + "\n□ 제외조건\n- 현재 참여 중인 자는 제외\n"),
            ),
            (
                "named_program",
                sample(
                    official_text=OFFICIAL_TEXT + "\n□ 제외조건\n- 국민취업지원제도 참여자는 제외\n"
                ),
            ),
            (
                "api_participant",
                sample(fields=api_fields(ptcpPrpTrgtCn="타 지자체 제도 참여 중인 자는 제외")),
            ),
        )
        for name, candidate in cases:
            with self.subTest(name=name):
                self.assert_held(candidate)


if __name__ == "__main__":
    unittest.main()

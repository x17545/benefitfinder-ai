"""Offline regression tests for the structured source eligibility compiler."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
import unittest

from benefitfinder.application.policy_analysis import AnalysisPolicy, AnalysisSource, PolicyAnalysisInput
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.application.region_options import DistrictOption, RegionOption
from benefitfinder.infrastructure.policy_catalog_records import Condition, ExecutableCondition
from benefitfinder.ingestion.source_eligibility import compile_source_eligibility


MATERIAL = "a" * 64
POLICY_ID = "youth:fixture:1"
CHECKED_AT = datetime(
    2026,
    11,
    16,
    9,
    30,
    tzinfo=timezone(timedelta(hours=9)),
)
REGIONS = (
    RegionOption(
        "11",
        "가상도",
        (
            DistrictOption("11110", "가상시"),
            DistrictOption("11200", "가상군"),
        ),
    ),
    RegionOption(
        "41",
        "다른도",
        (
            DistrictOption("41110", "다른시"),
            DistrictOption("41200", "다른군"),
        ),
    ),
)
MIXED_REGIONS = (
    RegionOption(
        "11",
        "가상도",
        (DistrictOption("11110", "가상시"),),
    ),
    RegionOption(
        "41",
        "다른도",
        (
            DistrictOption("41110", "다른시"),
            DistrictOption("41200", "다른군"),
        ),
    ),
)

CHEONGBUK_REGIONS = (
    RegionOption(
        "43",
        "충청북도",
        (
            DistrictOption("43110", "청주시"),
            DistrictOption("43130", "충주시"),
            DistrictOption("43150", "제천시"),
            DistrictOption("43720", "보은군"),
            DistrictOption("43730", "옥천군"),
            DistrictOption("43740", "영동군"),
            DistrictOption("43745", "진천군"),
            DistrictOption("43750", "괴산군"),
            DistrictOption("43760", "음성군"),
            DistrictOption("43770", "단양군"),
            DistrictOption("43800", "증평군"),
        ),
    ),
)

CHEONGBUK_TEST_REGIONS = (*REGIONS, *CHEONGBUK_REGIONS)
CHEONGBUK_WIDE_ZIP = (
    "43111,43112,43113,43114,43130,43150,43720,43730,"
    "43740,43745,43750,43760,43770,43800"
)

YEONGNAM_REGIONS = (
    RegionOption(
        "47",
        "경상북도",
        (
            DistrictOption("47110", "포항시"),
            DistrictOption("47130", "경주시"),
        ),
    ),
    RegionOption(
        "48",
        "경상남도",
        (
            DistrictOption("48120", "창원시"),
            DistrictOption("48170", "진주시"),
        ),
    ),
)

YEONGNAM_TEST_REGIONS = (*REGIONS, *YEONGNAM_REGIONS)


def projection(
    fields: Iterable[tuple[str, str]] | dict[str, str],
    *,
    newline: str = "\n",
) -> str:
    items = fields.items() if isinstance(fields, dict) else fields
    return "".join(
        f"[{name}]{newline}{value}{newline}{newline}"
        for name, value in items
    )


def source_input(text: str) -> PolicyAnalysisInput:
    return PolicyAnalysisInput(
        policy_id=POLICY_ID,
        documents=(
            SourceDocument(
                id="fixture-document",
                policy_id=POLICY_ID,
                source_url="https://example.invalid/youth-policy/1",
                document_version="fixture-version-1",
                collected_at=CHECKED_AT,
                text=text,
                official=True,
            ),
        ),
        policies=(AnalysisPolicy(POLICY_ID, "가상 청년 정책", ("employment",)),),
        collection_sources=(AnalysisSource("youthcenter", "1", MATERIAL),),
    )


def by_id(items: list[dict]) -> dict[str, dict]:
    return {str(item["id"] if "id" in item else item["condition_id"]): item for item in items}


def rules_by_id(items: list[dict]) -> dict[str, dict]:
    return {str(item["condition_id"]): item for item in items}


class SourceEligibilityTests(unittest.TestCase):
    def assert_evidence_ranges(self, document: SourceDocument, conditions: list[dict]) -> None:
        for condition in conditions:
            Condition.model_validate(condition)
            for evidence in condition["evidence"]:
                location = evidence["location"]
                self.assertTrue(location.startswith("chars:"))
                start_text, end_text = location.removeprefix("chars:").split("-", 1)
                start, end = int(start_text), int(end_text)
                self.assertEqual(document.text[start:end], evidence["quote"])
                self.assertLessEqual(len(evidence["quote"]), 2500)
                self.assertEqual(
                    evidence["source"],
                    {
                        "url": document.source_url,
                        "document_version": document.document_version,
                        "checked_at": document.collected_at.isoformat(),
                    },
                )

    def test_verified_scalar_fields_compile_and_all_other_fields_stay_manual(self) -> None:
        text = projection(
            {
                "plcyNm": "가상 청년 지원",
                "plcyNo": "1",
                "sprtTrgtAgeLmtYn": "N",
                "sprtTrgtMinAge": "19",
                "sprtTrgtMaxAge": "34",
                "jobCd": "0013001,0013003",
                "schoolCd": "0049005",
                "mrgSttsCd": "0055002",
                "zipCd": "11110",
                "plcySprtCn": "가상 지원 내용",
                "addAplyQlfcCndCn": "가상 추가 자격",
                "ptcpPrpTrgtCn": "가상 제외 대상",
                "etcMttrCn": "가상 기타 사항",
                "aplyPrdSeCd": "0057001",
                "aplyYmd": "20261101 ~ 20261130",
                "earnMaxAmt": "3000",
            }
        )
        value = source_input(text)

        conditions, executable = compile_source_eligibility(value, regions=REGIONS)

        self.assertEqual(
            [condition["id"] for condition in conditions],
            [rule["condition_id"] for rule in executable],
        )
        rules = rules_by_id(executable)
        self.assertEqual(
            rules["age"],
            {
                "condition_id": "age",
                "kind": "age",
                "minimum": 19,
                "maximum": 34,
                "military_extension_years": 0,
            },
        )
        self.assertEqual(
            rules["selection_employment_status"]["values"],
            ["employed", "job_seeker"],
        )
        self.assertEqual(
            rules["selection_domain_data_school"]["values"],
            ["university_enrolled"],
        )
        self.assertEqual(
            rules["selection_domain_data_marital"]["values"],
            ["single"],
        )
        self.assertEqual(rules["selection_region_code"]["values"], ["11110"])
        self.assertEqual(rules["selection_region_code"]["field"], "region_code")

        manual_ids = {
            condition_id for condition_id, rule in rules.items() if rule["kind"] == "manual"
        }
        self.assertTrue(
            {
                "source:plcyNm",
                "source:plcyNo",
                "source:plcySprtCn",
                "source:addAplyQlfcCndCn",
                "source:ptcpPrpTrgtCn",
                "source:etcMttrCn",
                "source:aplyPrdSeCd",
                "source:aplyYmd",
                "source:earnMaxAmt",
            }
            <= manual_ids
        )
        self.assert_evidence_ranges(value.documents[0], conditions)
        for rule in executable:
            ExecutableCondition.model_validate(rule)

    def test_military_service_exception_is_manual_while_simple_age_stays_executable(self) -> None:
        text = projection(
            {
                "sprtTrgtAgeLmtYn": "N",
                "sprtTrgtMinAge": "18",
                "sprtTrgtMaxAge": "39",
                "addAplyQlfcCndCn": "가상 병역 복무 기간은 연령 상한에 연장 인정",
            }
        )
        value = source_input(text)

        conditions, executable = compile_source_eligibility(value)

        rules = rules_by_id(executable)
        self.assertEqual(rules["age"]["kind"], "age")
        self.assertEqual(rules["age"]["military_extension_years"], 0)
        self.assertEqual(rules["source:addAplyQlfcCndCn"]["kind"], "manual")
        manual = by_id(conditions)["source:addAplyQlfcCndCn"]
        self.assertIn("병역 복무", manual["description"])
        self.assert_evidence_ranges(value.documents[0], conditions)

    def test_unknown_unsupported_and_contradictory_codes_remain_manual(self) -> None:
        text = projection(
            {
                "jobCd": "9999999",
                "schoolCd": "0049003",
                "mrgSttsCd": "0055003,0055002",
            }
        )
        value = source_input(text)

        conditions, executable = compile_source_eligibility(value)

        rules = rules_by_id(executable)
        self.assertNotIn("selection_employment_status", rules)
        self.assertNotIn("selection_domain_data_school", rules)
        self.assertNotIn("selection_domain_data_marital", rules)
        self.assertEqual(rules["source:jobCd"]["kind"], "manual")
        self.assertEqual(rules["source:schoolCd"]["kind"], "manual")
        self.assertEqual(rules["source:mrgSttsCd"]["kind"], "manual")
        self.assert_evidence_ranges(value.documents[0], conditions)

    def test_duplicate_supported_field_is_preserved_as_two_manual_conditions(self) -> None:
        text = (
            "[jobCd]\n0013001\n\n"
            "[jobCd]\n0013003\n\n"
        )
        value = source_input(text)

        conditions, executable = compile_source_eligibility(value)

        self.assertNotIn("selection_employment_status", rules_by_id(executable))
        self.assertEqual(
            [condition["id"] for condition in conditions],
            ["source:jobCd:1", "source:jobCd:2"],
        )
        self.assertEqual(
            [rule["kind"] for rule in executable],
            ["manual", "manual"],
        )
        self.assert_evidence_ranges(value.documents[0], conditions)

    def test_province_marker_uses_sido_and_district_uses_region_code(self) -> None:
        province = source_input(projection({"zipCd": "11000"}))
        district = source_input(projection({"zipCd": " 11110, 11110 "}))

        _, province_rules = compile_source_eligibility(province, regions=REGIONS)
        _, district_rules = compile_source_eligibility(district, regions=REGIONS)

        province_rule = rules_by_id(province_rules)["selection_domain_data_region_sido_code"]
        self.assertEqual(province_rule["values"], ["11"])
        district_rule = rules_by_id(district_rules)["selection_region_code"]
        self.assertEqual(district_rule["values"], ["11110"])

    def test_multiple_same_field_region_groups_are_an_or_selection(self) -> None:
        value = source_input(projection({"zipCd": "11110,41110"}))

        _, executable = compile_source_eligibility(value, regions=REGIONS)

        rule = rules_by_id(executable)["selection_region_code"]
        self.assertEqual(rule["field"], "region_code")
        self.assertEqual(rule["values"], ["11110", "41110"])

    def test_mixed_province_and_district_region_targets_stay_manual(self) -> None:
        value = source_input(projection({"zipCd": "11000,41110"}))

        conditions, executable = compile_source_eligibility(value, regions=MIXED_REGIONS)

        rules = rules_by_id(executable)
        self.assertNotIn("selection_region_code", rules)
        self.assertNotIn("selection_domain_data_region_sido_code", rules)
        self.assertEqual(rules["source:zipCd"]["kind"], "manual")
        self.assertEqual(conditions[0]["id"], "source:zipCd")

    def test_unknown_region_without_catalog_is_manual(self) -> None:
        value = source_input(projection({"zipCd": "11110"}))

        _, executable = compile_source_eligibility(value)

        self.assertEqual(rules_by_id(executable)["source:zipCd"]["kind"], "manual")

    def test_complete_cheongju_district_group_uses_parent_city_code(self) -> None:
        value = source_input(projection({"zipCd": "43111,43112,43113,43114"}))

        conditions, executable = compile_source_eligibility(
            value,
            regions=CHEONGBUK_TEST_REGIONS,
        )

        rule = rules_by_id(executable)["selection_region_code"]
        self.assertEqual(rule["field"], "region_code")
        self.assertEqual(rule["values"], ["43110"])
        self.assert_evidence_ranges(value.documents[0], conditions)

    def test_partial_cheongju_district_group_stays_manual(self) -> None:
        value = source_input(projection({"zipCd": "43111,43112,43113"}))

        conditions, executable = compile_source_eligibility(
            value,
            regions=CHEONGBUK_TEST_REGIONS,
        )

        rules = rules_by_id(executable)
        self.assertNotIn("selection_region_code", rules)
        self.assertNotIn("selection_domain_data_region_sido_code", rules)
        self.assertEqual(rules["source:zipCd"]["kind"], "manual")
        self.assert_evidence_ranges(value.documents[0], conditions)

    def test_cheongbuk_wide_policy_example_uses_sido_selection(self) -> None:
        value = source_input(projection({"zipCd": CHEONGBUK_WIDE_ZIP}))

        conditions, executable = compile_source_eligibility(
            value,
            regions=CHEONGBUK_TEST_REGIONS,
        )

        rule = rules_by_id(executable)["selection_domain_data_region_sido_code"]
        self.assertEqual(rule["field"], "domain_data.region.sido_code")
        self.assertEqual(rule["values"], ["43"])
        self.assert_evidence_ranges(value.documents[0], conditions)

    def test_complete_pohang_district_group_uses_parent_city_code(self) -> None:
        value = source_input(projection({"zipCd": "47111,47113"}))

        conditions, executable = compile_source_eligibility(
            value,
            regions=YEONGNAM_TEST_REGIONS,
        )

        rule = rules_by_id(executable)["selection_region_code"]
        self.assertEqual(rule["values"], ["47110"])
        self.assert_evidence_ranges(value.documents[0], conditions)

    def test_partial_pohang_district_group_stays_manual(self) -> None:
        value = source_input(projection({"zipCd": "47111"}))

        _, executable = compile_source_eligibility(
            value,
            regions=YEONGNAM_TEST_REGIONS,
        )

        self.assertEqual(rules_by_id(executable)["source:zipCd"]["kind"], "manual")

    def test_complete_changwon_district_group_uses_parent_city_code(self) -> None:
        value = source_input(projection({"zipCd": "48121,48123,48125,48127,48129"}))

        conditions, executable = compile_source_eligibility(
            value,
            regions=YEONGNAM_TEST_REGIONS,
        )

        rule = rules_by_id(executable)["selection_region_code"]
        self.assertEqual(rule["values"], ["48120"])
        self.assert_evidence_ranges(value.documents[0], conditions)

    def test_partial_changwon_district_group_stays_manual(self) -> None:
        value = source_input(projection({"zipCd": "48121,48123,48125,48127"}))

        _, executable = compile_source_eligibility(
            value,
            regions=YEONGNAM_TEST_REGIONS,
        )

        self.assertEqual(rules_by_id(executable)["source:zipCd"]["kind"], "manual")

    def test_long_field_evidence_is_split_at_exact_offsets(self) -> None:
        long_value = "가" * 2501
        text = projection({"etcMttrCn": long_value})
        value = source_input(text)

        conditions, executable = compile_source_eligibility(value)

        evidence = conditions[0]["evidence"]
        self.assertEqual([len(item["quote"]) for item in evidence], [2500, 1])
        self.assertEqual(evidence[0]["quote"] + evidence[1]["quote"], long_value)
        self.assertEqual(executable[0]["kind"], "manual")
        self.assertGreater(len(conditions[0]["description"]), 2500)
        self.assert_evidence_ranges(value.documents[0], conditions)

    def test_crlf_projection_preserves_exact_value_offsets(self) -> None:
        text = projection(
            {
                "plcyNm": "가상🙂정책",
                "etcMttrCn": "첫 줄\n둘째 줄",
            },
            newline="\r\n",
        )
        value = source_input(text)

        conditions, _ = compile_source_eligibility(value)

        self.assertEqual(
            [condition["description"].split(": ", 1)[1] for condition in conditions],
            ["가상🙂정책", "첫 줄\n둘째 줄"],
        )
        self.assert_evidence_ranges(value.documents[0], conditions)

    def test_empty_fields_make_no_conditions_and_limit_is_not_truncated(self) -> None:
        empty = source_input(projection({"plcyNm": "   ", "etcMttrCn": ""}))
        self.assertEqual(compile_source_eligibility(empty), ([], []))

        too_many = source_input(
            projection((f"x{index:03d}", "값") for index in range(101))
        )
        with self.assertRaisesRegex(ValueError, "condition_limit_exceeded"):
            compile_source_eligibility(too_many)

    def test_multiple_documents_and_invalid_projection_are_rejected(self) -> None:
        value = source_input(projection({"plcyNm": "가상 정책"}))
        with self.assertRaisesRegex(ValueError, "single_document"):
            compile_source_eligibility(
                PolicyAnalysisInput(
                    policy_id=POLICY_ID,
                    documents=(
                        *value.documents,
                        SourceDocument(
                            id="second",
                            policy_id=POLICY_ID,
                            source_url="https://example.invalid/youth-policy/2",
                            document_version="fixture-version-2",
                            collected_at=CHECKED_AT,
                            text="[plcyNm]\n둘째\n\n",
                            official=True,
                        ),
                    ),
                    policies=value.policies,
                    collection_sources=value.collection_sources,
                )
            )
        with self.assertRaisesRegex(ValueError, "projection_invalid"):
            compile_source_eligibility(source_input("필드 머리글 없는 원문"))


if __name__ == "__main__":
    unittest.main()

"""Offline tests for verified OnTong Youth policy code mappings."""

from __future__ import annotations

import unittest

from benefitfinder.application.region_options import DistrictOption, RegionOption
from benefitfinder.ingestion.youthcenter_codes import (
    CODE_GROUPS,
    CodeState,
    IncompleteRegionAliasError,
    InvalidRegionCatalogError,
    InvalidYouthcenterCodeError,
    LEGAL_REGION_CODE_ARCHIVE_SHA256,
    LEGAL_REGION_CODE_SOURCE_URL,
    LEGAL_REGION_CODE_VERSION,
    UnknownYouthcenterCodeError,
    YOUTHCENTER_CODE_DEFINITION_SHA256,
    YOUTHCENTER_CODE_DEFINITION_URL,
    YOUTHCENTER_CODE_DEFINITION_VERSION,
    decode_code,
    decode_code_list,
    decode_zip_codes,
)


REGIONS = (
    RegionOption(
        "11",
        "가상광역시",
        (
            DistrictOption("11110", "가상시"),
            DistrictOption("11111", "가상시 가상구"),
            DistrictOption("11113", "가상시 다른구"),
            DistrictOption("11200", "가상군"),
        ),
    ),
    RegionOption(
        "41",
        "가상도",
        (
            DistrictOption("41200", "가상읍"),
            DistrictOption("41210", "가상면"),
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

CHEONGBUK_WITH_OTHER_REGION = (*CHEONGBUK_REGIONS, REGIONS[0])
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


EXPECTED_LABELS = {
    "plcyMajorCd": {
        "0011001": "인문계열", "0011002": "사회계열", "0011003": "상경계열",
        "0011004": "이학계열", "0011005": "공학계열", "0011006": "예체능계열",
        "0011007": "농산업계열", "0011008": "기타", "0011009": "제한없음",
    },
    "jobCd": {
        "0013001": "재직자",
        "0013002": "자영업자",
        "0013003": "미취업자",
        "0013004": "프리랜서",
        "0013005": "일용근로자",
        "0013006": "(예비)창업자",
        "0013007": "단기근로자",
        "0013008": "영농종사자",
        "0013009": "기타",
        "0013010": "제한없음",
    },
    "schoolCd": {
        "0049001": "고졸 미만",
        "0049002": "고교 재학",
        "0049003": "고졸 예정",
        "0049004": "고교 졸업",
        "0049005": "대학 재학",
        "0049006": "대졸 예정",
        "0049007": "대학 졸업",
        "0049008": "석·박사",
        "0049009": "기타",
        "0049010": "제한없음",
    },
    "earnCndSeCd": {
        "0043001": "무관",
        "0043002": "연소득",
        "0043003": "기타",
    },
    "mrgSttsCd": {
        "0055001": "기혼",
        "0055002": "미혼",
        "0055003": "제한없음",
    },
    "sbizCd": {
        "0014001": "중소기업",
        "0014002": "여성",
        "0014003": "기초생활수급자",
        "0014004": "한부모가정",
        "0014005": "장애인",
        "0014006": "농업인",
        "0014007": "군인",
        "0014008": "지역인재",
        "0014009": "기타",
        "0014010": "제한없음",
    },
    "plcyPvsnMthdCd": {
        "0042001": "인프라 구축",
        "0042002": "프로그램",
        "0042003": "직접대출",
        "0042004": "공공기관",
        "0042005": "계약(위탁운영)",
        "0042006": "보조금",
        "0042007": "대출보증",
        "0042008": "공적보험",
        "0042009": "조세지출",
        "0042010": "바우처",
        "0042011": "정보제공",
        "0042012": "경제적 규제",
        "0042013": "기타",
    },
}


class ScalarCodeTests(unittest.TestCase):
    def test_source_evidence_is_retained(self) -> None:
        self.assertEqual(
            YOUTHCENTER_CODE_DEFINITION_URL,
            "https://www.youthcenter.go.kr/downloadform/API%EC%BD%94%EB%93%9C%EC%A0%95%EB%B3%B4.xlsx",
        )
        self.assertEqual(YOUTHCENTER_CODE_DEFINITION_VERSION, "2025-06-30")
        self.assertEqual(
            YOUTHCENTER_CODE_DEFINITION_SHA256,
            "81cd89ddc7bd49dfa9e53dec4f093bc8372d241505b5e8374cbfaf018245a5ef",
        )
        self.assertEqual(LEGAL_REGION_CODE_SOURCE_URL, "https://www.code.go.kr/stdcode/regCodeL.do")
        self.assertEqual(LEGAL_REGION_CODE_VERSION, "2026-10-01")
        self.assertEqual(
            LEGAL_REGION_CODE_ARCHIVE_SHA256,
            "44a3fe4cacf88ce98e35fb883807d2725b589f427ff4d73ee40b914d1286bacd",
        )

    def test_all_official_scalar_values_are_preserved(self) -> None:
        for field, expected in EXPECTED_LABELS.items():
            with self.subTest(field=field):
                decoded = {
                    code: decode_code(field, code).label
                    for code in expected
                }
                self.assertEqual(decoded, expected)

    def test_school_normalized_values_are_exact_contract_choices(self) -> None:
        expected = {
            "0049001": "high_school_below",
            "0049002": "high_school_enrolled",
            "0049003": None,
            "0049004": "high_school_graduate",
            "0049005": "university_enrolled",
            "0049006": "university_expected",
            "0049007": "university_graduate",
            "0049008": "masters_doctorate",
            "0049009": None,
            "0049010": None,
        }
        self.assertEqual(
            {code: decode_code("schoolCd", code).normalized for code in expected},
            expected,
        )

    def test_official_examples_and_code_groups(self) -> None:
        self.assertEqual(
            CODE_GROUPS,
            {
                "jobCd": "0013",
                "plcyMajorCd": "0011",
                "schoolCd": "0049",
                "earnCndSeCd": "0043",
                "mrgSttsCd": "0055",
                "sbizCd": "0014",
                "plcyPvsnMthdCd": "0042",
            },
        )
        self.assertEqual(decode_code("jobCd", "0013003").label, "미취업자")
        self.assertEqual(decode_code("jobCd", "0013003").normalized, "job_seeker")
        self.assertEqual(decode_code("schoolCd", "0049010").label, "제한없음")
        self.assertEqual(decode_code("earnCndSeCd", "0043001").label, "무관")
        self.assertEqual(decode_code("mrgSttsCd", "0055003").label, "제한없음")
        self.assertEqual(decode_code("sbizCd", "0014010").label, "제한없음")
        self.assertEqual(decode_code("plcyPvsnMthdCd", "0042007").label, "대출보증")

    def test_restriction_unrestriction_and_mapping_are_distinct(self) -> None:
        self.assertEqual(decode_code("jobCd", "0013010").state, CodeState.UNRESTRICTED)
        self.assertEqual(decode_code("jobCd", "0013001").state, CodeState.HOLD)
        self.assertTrue(decode_code("schoolCd", "0049005").holds)
        self.assertTrue(decode_code("earnCndSeCd", "0043002").holds)
        self.assertEqual(
            decode_code("plcyPvsnMthdCd", "0042001").state,
            CodeState.MAPPED,
        )

    def test_list_form_preserves_each_definition(self) -> None:
        decoded = decode_code_list("jobCd", "0013001,0013003")
        self.assertEqual(decoded.raw, "0013001,0013003")
        self.assertEqual(tuple(code.code for code in decoded.codes), ("0013001", "0013003"))
        self.assertEqual(tuple(code.label for code in decoded.codes), ("재직자", "미취업자"))
        self.assertEqual(
            tuple(code.normalized for code in decoded.codes),
            ("employed", "job_seeker"),
        )
        self.assertEqual(decoded.state, CodeState.HOLD)
        self.assertFalse(decoded.contradictory)

        school = decode_code_list("schoolCd", "0049005,0049006")
        self.assertEqual(
            tuple(code.normalized for code in school.codes),
            ("university_enrolled", "university_expected"),
        )
        self.assertEqual(decode_code("schoolCd", "0049007").normalized, "university_graduate")

    def test_list_with_unrestricted_and_restricted_values_is_contradictory_hold(self) -> None:
        decoded = decode_code_list("jobCd", "0013010,0013003")
        self.assertEqual(decoded.state, CodeState.HOLD)
        self.assertTrue(decoded.holds)
        self.assertTrue(decoded.contradictory)
        self.assertEqual(tuple(code.label for code in decoded.codes), ("제한없음", "미취업자"))

    def test_list_unrestricted_alone_is_unrestricted(self) -> None:
        decoded = decode_code_list("schoolCd", "0049010")
        self.assertEqual(decoded.state, CodeState.UNRESTRICTED)
        self.assertFalse(decoded.contradictory)

    def test_list_never_ignores_unknown_or_empty_items(self) -> None:
        with self.assertRaises(UnknownYouthcenterCodeError):
            decode_code_list("jobCd", "0013001,0013999")
        for value in ("0013001,", ",0013003", "0013001,,0013003"):
            with self.subTest(value=value):
                with self.assertRaises(InvalidYouthcenterCodeError):
                    decode_code_list("jobCd", value)

    def test_missing_list_and_descriptive_list_states(self) -> None:
        missing = decode_code_list("jobCd", "  ")
        self.assertEqual(missing.state, CodeState.MISSING)
        self.assertEqual(missing.codes, ())

        provision = decode_code_list("plcyPvsnMthdCd", "0042006,0042010")
        self.assertEqual(provision.state, CodeState.MAPPED)
        self.assertEqual(len(provision.codes), 2)

    def test_exact_raw_value_is_retained_but_whitespace_is_validated_away(self) -> None:
        decoded = decode_code("jobCd", " 0013001 ")
        self.assertEqual(decoded.raw, " 0013001 ")
        self.assertEqual(decoded.code, "0013001")
        self.assertEqual(decoded.label, "재직자")

    def test_blank_is_explicitly_missing(self) -> None:
        for value in (None, "", "   "):
            with self.subTest(value=value):
                decoded = decode_code("schoolCd", value)
                self.assertEqual(decoded.state, CodeState.MISSING)
                self.assertIsNone(decoded.code)
                self.assertIsNone(decoded.label)

    def test_unknown_and_combined_scalar_codes_fail(self) -> None:
        for value in ("0013999", "0013010,0013003", "전국"):
            with self.subTest(value=value):
                with self.assertRaises(UnknownYouthcenterCodeError) as raised:
                    decode_code("jobCd", value)
                self.assertEqual(raised.exception.field, "jobCd")
        with self.assertRaises(ValueError):
            decode_code("notACodeField", "0013001")  # type: ignore[arg-type]


class ZipCodeTests(unittest.TestCase):
    def test_full_child_coverage_collapses_parent_then_complete_province(self) -> None:
        coverage = decode_zip_codes("11111,11113,11200", REGIONS)
        self.assertFalse(coverage.nationwide)
        self.assertEqual(coverage.state, CodeState.HOLD)
        self.assertEqual(coverage.text, "가상광역시")
        self.assertEqual(coverage.codes, ("11111", "11113", "11200"))

    def test_parent_selection_covers_its_children_but_not_the_whole_country(self) -> None:
        coverage = decode_zip_codes("11110,11200", REGIONS)
        self.assertEqual(coverage.text, "가상광역시")
        self.assertTrue(coverage.holds)

    def test_missing_child_prevents_false_parent_or_province_collapse(self) -> None:
        coverage = decode_zip_codes("11111,11200,41200,41210", REGIONS)
        self.assertFalse(coverage.nationwide)
        self.assertEqual(
            tuple(group.name for group in coverage.groups),
            (
                "가상광역시 가상시 가상구",
                "가상광역시 가상군",
                "가상도",
            ),
        )

    def test_every_current_district_collapses_to_nationwide(self) -> None:
        coverage = decode_zip_codes("11111,11113,11200,41200,41210", REGIONS)
        self.assertTrue(coverage.nationwide)
        self.assertEqual(coverage.state, CodeState.UNRESTRICTED)
        self.assertEqual(coverage.text, "전국")
        self.assertEqual(len(coverage.groups), 1)

    def test_official_sido_markers_collapse_to_nationwide(self) -> None:
        coverage = decode_zip_codes("11000,41000", REGIONS)
        self.assertTrue(coverage.nationwide)
        self.assertEqual(coverage.text, "전국")

    def test_exact_raw_list_is_retained_and_duplicates_are_deduplicated(self) -> None:
        raw = " 11111, 11113,11113,11200 ,41200,41210 "
        coverage = decode_zip_codes(raw, REGIONS)
        self.assertEqual(coverage.raw, raw)
        self.assertEqual(coverage.codes[:3], ("11111", "11113", "11200"))
        self.assertEqual(len(coverage.codes), 5)

    def test_missing_zip_has_explicit_missing_state(self) -> None:
        for value in (None, "", "  "):
            with self.subTest(value=value):
                coverage = decode_zip_codes(value, REGIONS)
                self.assertEqual(coverage.state, CodeState.MISSING)
                self.assertFalse(coverage.nationwide)
                self.assertIsNone(coverage.text)

    def test_unknown_obsolete_or_malformed_code_fails_without_partial_output(self) -> None:
        for value in ("99999", "29110", "11111,99999"):
            with self.subTest(value=value):
                with self.assertRaises(UnknownYouthcenterCodeError):
                    decode_zip_codes(value, REGIONS)
        for value in ("1100", "11000,abcde", "11000,,11110"):
            with self.subTest(value=value):
                with self.assertRaises(InvalidYouthcenterCodeError):
                    decode_zip_codes(value, REGIONS)

    def test_complete_cheongju_district_group_collapses_to_city_code(self) -> None:
        coverage = decode_zip_codes("43111,43112,43113,43114", CHEONGBUK_REGIONS)

        self.assertFalse(coverage.nationwide)
        self.assertEqual(coverage.state, CodeState.HOLD)
        self.assertEqual(coverage.text, "충청북도 청주시")
        self.assertEqual(len(coverage.groups), 1)
        self.assertEqual(coverage.groups[0].code, "43110")
        self.assertEqual(
            coverage.groups[0].source_codes,
            ("43111", "43112", "43113", "43114"),
        )

    def test_partial_cheongju_district_group_never_widens_to_the_city(self) -> None:
        for value in ("43111", "43111,43112", "43111,43112,43113"):
            with self.subTest(value=value):
                with self.assertRaises(IncompleteRegionAliasError):
                    decode_zip_codes(value, CHEONGBUK_REGIONS)

    def test_explicit_cheongju_city_code_makes_partial_district_codes_redundant(self) -> None:
        coverage = decode_zip_codes("43110,43111,43112", CHEONGBUK_REGIONS)

        self.assertEqual(coverage.groups[0].code, "43110")
        self.assertEqual(coverage.groups[0].source_codes, ("43110", "43111", "43112"))

    def test_cheongbuk_wide_notice_resolves_to_province_not_nationwide(self) -> None:
        coverage = decode_zip_codes(CHEONGBUK_WIDE_ZIP, CHEONGBUK_WITH_OTHER_REGION)

        self.assertFalse(coverage.nationwide)
        self.assertEqual(len(coverage.groups), 1)
        self.assertEqual(coverage.groups[0].code, "43000")
        self.assertEqual(coverage.groups[0].name, "충청북도")
        self.assertEqual(
            coverage.groups[0].source_codes,
            tuple(CHEONGBUK_WIDE_ZIP.split(",")),
        )

    def test_complete_pohang_district_group_collapses_to_city_code(self) -> None:
        coverage = decode_zip_codes("47111,47113", YEONGNAM_REGIONS)

        self.assertEqual(coverage.groups[0].code, "47110")
        self.assertEqual(coverage.text, "경상북도 포항시")
        self.assertEqual(coverage.groups[0].source_codes, ("47111", "47113"))

    def test_partial_pohang_district_group_never_widens_to_the_city(self) -> None:
        for value in ("47111", "47113"):
            with self.subTest(value=value):
                with self.assertRaises(IncompleteRegionAliasError):
                    decode_zip_codes(value, YEONGNAM_REGIONS)

    def test_complete_changwon_district_group_collapses_to_city_code(self) -> None:
        coverage = decode_zip_codes(
            "48121,48123,48125,48127,48129",
            YEONGNAM_REGIONS,
        )

        self.assertEqual(coverage.groups[0].code, "48120")
        self.assertEqual(coverage.text, "경상남도 창원시")
        self.assertEqual(
            coverage.groups[0].source_codes,
            ("48121", "48123", "48125", "48127", "48129"),
        )

    def test_partial_changwon_district_group_never_widens_to_the_city(self) -> None:
        for value in (
            "48121",
            "48121,48123",
            "48121,48123,48125",
            "48121,48123,48125,48127",
        ):
            with self.subTest(value=value):
                with self.assertRaises(IncompleteRegionAliasError):
                    decode_zip_codes(value, YEONGNAM_REGIONS)

    def test_invalid_region_catalogs_fail_loudly(self) -> None:
        with self.assertRaises(InvalidRegionCatalogError):
            decode_zip_codes("11000", ())
        with self.assertRaises(InvalidRegionCatalogError):
            decode_zip_codes(
                "11000",
                (RegionOption("11", "가상광역시", (DistrictOption("11110", "가상시"),)),) * 2,
            )


if __name__ == "__main__":
    unittest.main()

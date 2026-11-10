"""가상 원문으로 조건·예외의 문맥 보존과 수치·논리 추정 금지를 검사한다."""
from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import Mock

from benefitfinder.ai.policy_facts_agent import extract_policy_facts
from benefitfinder.application.policy_rules import (
    PolicyReference,
    RuleReviewBatch,
    SourceDocument,
)


def _batch(*lines: str) -> RuleReviewBatch:
    document = SourceDocument(
        "virtual-notice",
        "virtual-policy",
        "https://example.invalid/notice",
        "v1",
        datetime(2026, 11, 6, tzinfo=timezone.utc),
        "\n".join(lines),
        True,
    )
    policy = PolicyReference("virtual-policy", "youth", ("employment",))
    return RuleReviewBatch((document,), (policy,), ())


def _reply(*lines: int) -> dict[str, object]:
    return {
        "tool_calls": [
            {"function": {"name": "submit_evidence", "arguments": {"lines": list(lines)}}}
        ]
    }


def _chat(
    eligibility: tuple[int, ...],
    amounts: tuple[int, ...],
    timing: tuple[int, ...],
    captured: list[str] | None = None,
) -> Mock:
    def _fn(
        messages: list[dict[str, object]], tool_defs: list[dict[str, object]]
    ) -> dict[str, object]:
        body = str(messages[1].get("content", "")) if len(messages) > 1 else ""
        if captured is not None:
            captured.append(body)
        if "Eligibility:" in body:
            return _reply(*eligibility)
        if "Benefit amounts:" in body:
            return _reply(*amounts)
        if "Payment timing:" in body:
            return _reply(*timing)
        return _reply()

    return Mock(side_effect=_fn)


class PolicyFactsContextTests(TestCase):
    def test_address_paragraph_keeps_residence_and_move_stop(self) -> None:
        addr1 = "가상시에 주민등록을 둔 23~29세 청년"
        addr2 = "신청일 기준 가상시에 5개월 이상 계속 거주한 자"
        stop = "도외로 이전하면 지원이 중단된다"
        batch = _batch("1. 지원대상", addr1, addr2, stop)
        chat = _chat((2,), (), ())
        result = extract_policy_facts(batch, chat=chat)
        joined = "\n".join(c.quote for c in result.eligibility)
        self.assertIn(addr1, joined)
        self.assertIn(addr2, joined, "주소 2줄 중 일부만 골라도 나머지 주소 조건이 보존돼야 한다")
        self.assertIn(stop, joined, "다음 줄 도외 이전 지원중단이 함께 보존돼야 한다")

    def test_income_band_keeps_distant_exception(self) -> None:
        heading = "1. 지원대상"
        band = "가구 소득이 기준중위소득 87% 이하인 가상청년"
        fillers = [f"가상 안내 문장 {i:02d} 거주 요건 확인용" for i in range(1, 23)]
        exc1 = "※ 기준중위소득 87% 초과 가구는 지원대상에서 제외되며"
        exc2 = "증빙 서류 미제출 시 대상에서 제외된다"
        batch = _batch(heading, band, "❍ 가구 소득 확인", *fillers, exc1, exc2)
        chat = _chat((2,), (), ())
        result = extract_policy_facts(batch, chat=chat)
        joined = "\n".join(c.quote for c in result.eligibility)
        self.assertIn(band, joined)
        self.assertIn(exc1, joined, "22줄 밖 기준중위소득 예외가 함께 보존돼야 한다")
        self.assertIn(exc2, joined, "※/증빙으로 이어지는 예외 문장이 함께 보존돼야 한다")

    def test_business_exclusion_keeps_reopening_exception(self) -> None:
        exclusion = "- 사업자 등록이 있는 자는 신청할 수 없다"
        reopen = "√ 휴업·폐업 신고 후 2개월이 지난 경우는 신청할 수 있다"
        batch = _batch(
            "1. 지원대상",
            "가상시에 거주하는 23~29세 미취업 청년",
            "1-1. 지원제외",
            exclusion,
            reopen,
        )
        chat = _chat((4,), (), ())
        result = extract_policy_facts(batch, chat=chat)
        joined = "\n".join(c.quote for c in result.eligibility)
        self.assertIn(exclusion, joined)
        self.assertIn(reopen, joined, "사업자 제외 다음 줄 휴폐업 예외가 누락되면 안 된다")

    def test_housing_amount_keeps_caps_without_misreading_income_or_total(self) -> None:
        monthly = "- 월 73,000원씩 9개월 지원"
        actual = "- 실제 지출액 한도 내에서 지급하며 월 지출액이 73,000원보다 적으면 실제액만 지급"
        lifetime = "- 생애 최대 12개월까지 지원하며 기지원 개월은 차감한다"
        income_limit = "- 가구 월소득 2,130,000원 이하인 경우에 신청할 수 있다"
        batch = _batch(
            "1. 지원대상",
            "가상시에 거주하는 청년",
            "2. 지원금액",
            monthly,
            actual,
            lifetime,
            income_limit,
        )
        chat = _chat((2,), (4,), ())
        result = extract_policy_facts(batch, chat=chat)
        joined = "\n".join(c.evidence.quote for c in result.amounts)
        self.assertIn(monthly, joined, "월액/기간이 보존돼야 한다")
        self.assertIn(actual, joined, "실제비용 한도가 함께 보존돼야 한다")
        self.assertIn(lifetime, joined, "생애 한도/기지원 차감이 함께 보존돼야 한다")
        self.assertNotIn(income_limit, joined, "소득 한도를 지원금 근거에 섞으면 안 된다")
        won = tuple(v for c in result.amounts for v in c.amounts_won)
        self.assertIn(73_000, won)
        self.assertNotIn(2_130_000, won, "소득 한도액을 지원금으로 오인하면 안 된다")
        self.assertNotIn(657_000, won, "월액×기간 총액을 계산해 저장하면 안 된다")

    def test_amount_faq_deduction_shown_and_kept_as_full_qa(self) -> None:
        monthly = "- 월 73,000원씩 9개월 지원"
        question = "Q. 가상청년수당을 받고 있으면 신청할 수 있나요?"
        answer = "A. 가상청년수당 수급액은 지원금에서 차감되며 중복 수급분은 제외됩니다"
        lines = (
            "1. 지원대상",
            "가상시에 거주하는 청년",
            "2. 지원금액",
            monthly,
            "3. 신청방법",
            "가상포털에서 신청",
            "4. 자주 묻는 질문",
            question,
            answer,
        )
        captured: list[str] = []
        chat = _chat((2,), (4,), (), captured)
        result = extract_policy_facts(batch=_batch(*lines), chat=chat)
        amounts_prompt = "\n".join(b for b in captured if "Benefit amounts:" in b)
        self.assertIn(question, amounts_prompt, "금액 섹션 밖 Q 차감 근거가 모델에 보여야 한다")
        self.assertIn(answer, amounts_prompt, "금액 섹션 밖 A 차감 근거가 모델에 보여야 한다")
        self.assertTrue(result.amounts, "금액 근거가 비어 있으면 안 된다")
        chat2 = _chat((2,), (9,), ())
        result2 = extract_policy_facts(batch=_batch(*lines), chat=chat2)
        joined2 = "\n".join(c.evidence.quote for c in result2.amounts)
        self.assertIn(question, joined2, "FAQ 선택 시 질문까지 완전한 Q/A로 보존돼야 한다")
        self.assertIn(answer, joined2, "FAQ 선택 시 답변이 보존돼야 한다")
        self.assertEqual(result2.amounts[0].amounts_won, ())

    def test_caution_about_selected_applicants_does_not_reopen_eligibility(self) -> None:
        caution = "❍ 지원 대상자로 선정된 후에도 신청 전 자격을 꼭 확인하시기 바랍니다"
        batch = _batch(
            "1. 지원대상", "가상시에 거주하는 청년", "2. 신청방법", "가상포털 신청",
            "3. 기타 유의사항", caution,
        )
        captured: list[str] = []
        result = extract_policy_facts(batch, chat=_chat((2,), (), (), captured))
        eligibility_prompt = next(body for body in captured if "Eligibility:" in body)
        self.assertNotIn(caution, eligibility_prompt)
        self.assertNotIn(caution, "\n".join(c.quote for c in result.eligibility))

    def test_ambiguous_conjunction_and_birth_year_kept_verbatim(self) -> None:
        birth = "2001년생 또는 2002년생 중 가상시에 거주하는 청년"
        or_line = "가상직업훈련 OR 가상인턴십에 참여한 자"
        batch = _batch("1. 지원대상", birth, or_line)
        chat = _chat((2, 3), (), ())
        result = extract_policy_facts(batch, chat=chat)
        joined = "\n".join(c.quote for c in result.eligibility)
        self.assertIn(birth, joined, "출생년도를 추정 교정하지 않고 원문 그대로 둬야 한다")
        self.assertIn("또는", joined, "모호한 접속어를 교정하지 않아야 한다")
        self.assertIn("OR", joined, "원문의 OR을 교정하지 않아야 한다")
        self.assertIn("2001년생", joined)
        self.assertIn("2002년생", joined)

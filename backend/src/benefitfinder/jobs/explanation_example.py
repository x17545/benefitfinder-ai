"""Virtual published corpora and code results for the F13 example command."""

from benefitfinder.application.policy_explanations import (
    ExplanationRequest,
    PublishedClause,
    safe_condition_results,
)


def explanation_example(category: str) -> tuple[ExplanationRequest, tuple[PublishedClause, ...]]:
    if category == "employment":
        name, amount = "[가상] 새출발 취업준비 지원", 3_000_000
        eligibility = "가상 지역의 미취업 청년을 대상으로 하며 세부 자격은 신청 시 확인합니다."
        action = "온라인 신청서와 구직활동 계획서를 제출합니다."
    elif category == "housing":
        name, amount = "[가상] 독립 주거비 지원", 2_400_000
        eligibility = "가상 지역에서 별도로 거주하는 무주택 청년을 대상으로 합니다."
        action = "온라인 신청 시 임대차계약서와 월세 납부 자료를 제출합니다."
    else:
        raise ValueError("invalid_example_category")
    policy_id = "youth:virtual:" + category
    request = ExplanationRequest(
        policy_id,
        name,
        category,
        "fictional-v1",
        amount,
        safe_condition_results({"age": "met", "region": "met", "employment": "needs_review"}),
    )
    url = "https://example.invalid/f13/" + category
    clauses = (
        PublishedClause(
            "virtual-eligibility",
            policy_id,
            "fictional-v1",
            "가상 지원 대상",
            eligibility,
            url,
            ("age", "region", "employment", "housing"),
            True,
        ),
        PublishedClause(
            "virtual-application",
            policy_id,
            "fictional-v1",
            "가상 신청 안내",
            action,
            url,
            ("application",),
            True,
        ),
        PublishedClause(
            "virtual-draft",
            policy_id,
            "fictional-v1",
            "미게시 가상 초안",
            "미게시 가상 내용은 설명에 사용하지 않습니다.",
            url,
            ("application",),
            False,
        ),
    )
    return request, clauses

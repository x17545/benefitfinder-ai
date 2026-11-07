"""Virtual published clauses for the existing three-policy recommendation example."""

from benefitfinder.application.policy_explanations import ExplanationRequest, PublishedClause
from benefitfinder.application.response_mapper import MVP_POLICY_SOURCES


def recommendation_explanation_data() -> tuple[
    tuple[ExplanationRequest, ...], tuple[PublishedClause, ...]
]:
    examples = (
        (
            "P1",
            "[가상] 독립 주거비 지원",
            "housing",
            "가상 지역에서 별도로 거주하는 무주택 청년을 대상으로 합니다.",
            "온라인 신청 시 임대차계약서와 월세 납부 자료를 제출합니다.",
        ),
        (
            "P3",
            "[가상] 새출발 취업준비 지원",
            "employment",
            "가상 지역의 미취업 청년을 대상으로 하며 세부 자격은 신청 시 확인합니다.",
            "온라인 신청서와 구직활동 계획서를 제출합니다.",
        ),
        (
            "P6",
            "[가상] 장기 주거 정착 지원",
            "housing",
            "가상 지역의 무주택 청년을 대상으로 하며 세부 자격은 신청 시 확인합니다.",
            "신청 안내에 따라 거주 확인 서류를 제출합니다.",
        ),
    )
    policies, clauses = [], []
    for key, name, category, eligibility, action in examples:
        policy_id = "youth:example:" + key
        version = "fictional-v1"
        policies.append(ExplanationRequest(policy_id, name, category, version, None))
        for topic, title, text in (
            ("age", "가상 지원 대상", eligibility),
            ("application", "가상 신청 안내", action),
        ):
            clauses.append(
                PublishedClause(
                    key + "-" + topic,
                    policy_id,
                    version,
                    title,
                    text,
                    MVP_POLICY_SOURCES[key],
                    (topic,),
                    True,
                )
            )
    return tuple(policies), tuple(clauses)

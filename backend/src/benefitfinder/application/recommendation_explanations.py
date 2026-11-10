"""Add F13 explanations after recommendation calculation, without accepting a profile."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from typing import Any

from benefitfinder.application.policy_explanations import (
    ExplanationGenerator,
    ExplanationRequest,
    PublishedClauseRepository,
    explain_policy,
    explanation_text,
    safe_condition_results,
)


class RecommendationExplainer:
    def __init__(
        self,
        repository: PublishedClauseRepository,
        policies: tuple[ExplanationRequest, ...],
        generator: ExplanationGenerator | None = None,
        *,
        timeout_seconds: float = 5,
    ) -> None:
        if not 0 < timeout_seconds <= 5:
            raise ValueError("invalid_explanation_timeout")
        self._repository = repository
        self._policies = {policy.policy_id: policy for policy in policies}
        self._generator = generator
        self._timeout_seconds = timeout_seconds

    async def enrich(self, response: dict[str, Any]) -> dict[str, Any]:
        """Search once per policy/version/verdict in this request; never cache across requests."""
        result = deepcopy(response)
        groups: dict[ExplanationRequest, list[dict[str, Any]]] = {}
        for period in result["results"].values():
            for view in period.values():
                for portfolio in view["portfolios"]:
                    for policy in portfolio["policies"]:
                        policy["ai_generated"] = False
                        policy["ai_sources"] = []
                        template = self._policies.get(policy["policy_id"])
                        if (
                            template is None
                            or policy["source"]["document_version"] != template.document_version
                        ):
                            policy["ai_reason"] = (
                                "세부 자격과 신청 방법은 공식 공고에서 확인해 주세요."
                            )
                            continue
                        # Public response verdicts already preserve needs_review when evidence is absent.
                        conditions = safe_condition_results(
                            {item["condition_id"]: item["status"] for item in policy["eligibility"]}
                        )
                        key = replace(template, conditions=conditions, amount_won=None)
                        groups.setdefault(key, []).append(policy)

        async def explain_group(key: ExplanationRequest, rows: list[dict[str, Any]]) -> bool:
            # One number-free application action serves all periods/PV views. Each code lead is new.
            request = replace(key, amount_won=rows[0]["amount_in_period_won"])
            explanation = await explain_policy(
                request,
                self._repository,
                self._generator,
                timeout_seconds=self._timeout_seconds,
            )
            for row in rows:
                context = replace(key, amount_won=row["amount_in_period_won"])
                row["ai_reason"] = explanation_text(context, explanation.application_detail)
                row["ai_generated"] = explanation.ai_generated
                row["ai_sources"] = [
                    {
                        "quote": clause.text,
                        "location": clause.location or clause.id,
                        "source": {
                            "url": clause.source_url,
                            "document_version": clause.document_version,
                            "checked_at": (
                                clause.checked_at.isoformat()
                                if clause.checked_at is not None
                                else row["source"]["checked_at"]
                            ),
                        },
                    }
                    for clause in explanation.evidence
                ]
            return explanation.ai_generated

        generated = await asyncio.gather(
            *(explain_group(key, rows) for key, rows in groups.items())
        )
        result["ai_generated"] = any(generated)
        return result

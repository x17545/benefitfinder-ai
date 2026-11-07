"""F13 explanations over published clauses and safe, already calculated values."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

SAFE_CONDITIONS = frozenset({"age", "region", "employment", "housing", "graduation"})
VERDICTS = frozenset({"met", "not_met", "needs_review"})


def safe_condition_results(results: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    """Copy only general condition IDs and verdicts; never copy free-form reasons or profiles."""
    selected = []
    for key in sorted(SAFE_CONDITIONS):
        if key not in results:
            continue
        value = results[key]
        if not isinstance(value, str) or value not in VERDICTS:
            raise ValueError("invalid_explanation_verdict")
        selected.append((key, value))
    return tuple(selected)


@dataclass(frozen=True)
class ExplanationRequest:
    policy_id: str
    policy_name: str
    category: str
    document_version: str
    amount_won: int | None
    conditions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(value, str)
                for value in (self.policy_id, self.policy_name, self.document_version)
            )
            or not re.fullmatch(r"youth:[A-Za-z0-9:_-]{1,100}", self.policy_id)
            or not 0 < len(self.policy_name.strip()) <= 120
            or not self.policy_name.isprintable()
            or not isinstance(self.category, str)
            or self.category not in {"employment", "housing"}
            or not 0 < len(self.document_version.strip()) <= 120
            or (
                self.amount_won is not None
                and (type(self.amount_won) is not int or self.amount_won < 0)
            )
            or not isinstance(self.conditions, tuple)
            or any(not isinstance(pair, tuple) or len(pair) != 2 for pair in self.conditions)
            or any(
                not isinstance(key, str)
                or not isinstance(value, str)
                or key not in SAFE_CONDITIONS
                or value not in VERDICTS
                for key, value in self.conditions
            )
            or len({key for key, _ in self.conditions}) != len(self.conditions)
        ):
            raise ValueError("invalid_explanation_request")


@dataclass(frozen=True)
class PublishedClause:
    id: str
    policy_id: str
    document_version: str
    title: str
    text: str
    source_url: str
    topics: tuple[str, ...]
    published: bool = False


class PublishedClauseRepository(Protocol):
    async def search(
        self, request: ExplanationRequest, *, limit: int
    ) -> tuple[PublishedClause, ...]: ...


@dataclass(frozen=True)
class GeneratedExplanation:
    text: str
    source_ids: tuple[str, ...]


class ExplanationGenerator(Protocol):
    async def generate(
        self,
        request: ExplanationRequest,
        clauses: tuple[PublishedClause, ...],
        *,
        timeout_seconds: float,
    ) -> GeneratedExplanation: ...


class StructuredCompletion(Protocol):
    async def complete(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, object],
        *,
        timeout_seconds: float,
    ) -> object: ...


@dataclass(frozen=True)
class PolicyExplanation:
    policy_id: str
    ai_reason: str
    ai_generated: bool
    ai_sources: tuple[str, ...]
    evidence: tuple[PublishedClause, ...]
    fallback_reason: str | None = None


def explanation_lead(request: ExplanationRequest) -> str:
    """Keep the policy name, qualification verdict and calculated amount under code control."""
    statuses = {value for _, value in request.conditions}
    if "not_met" in statuses:
        status = "미충족인 일반 조건이 있고"
    elif not statuses or "needs_review" in statuses:
        status = "확인이 필요한 일반 조건이 있고"
    else:
        status = "입력된 일반 조건의 코드 판정은 충족이며"
    amount = (
        "지원 금액은 확인이 필요해요"
        if request.amount_won is None
        else (f"선택 기간 계산 금액은 {request.amount_won:,}원이에요")
    )
    return f"{request.policy_name}은 {status} {amount}."


def _fallback(request: ExplanationRequest, reason: str) -> PolicyExplanation:
    text = explanation_lead(request) + " 세부 자격과 신청 방법은 공식 공고에서 확인해 주세요."
    return PolicyExplanation(request.policy_id, text, False, (), (), reason)


async def explain_policy(
    request: ExplanationRequest,
    repository: PublishedClauseRepository,
    generator: ExplanationGenerator | None = None,
    *,
    timeout_seconds: float = 5.0,
) -> PolicyExplanation:
    """Search on every call; source/model failures never replace calculation results."""
    if not 0 < timeout_seconds <= 5:
        raise ValueError("invalid_explanation_timeout")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    stage = "retrieval_failed"
    try:
        async with asyncio.timeout(timeout_seconds):
            clauses = await repository.search(request, limit=5)
            if not clauses:
                return _fallback(request, "no_published_evidence")
            if len(clauses) > 5 or len({clause.id for clause in clauses}) != len(clauses):
                return _fallback(request, "invalid_evidence")
            for clause in clauses:
                if (
                    not isinstance(clause, PublishedClause)
                    or clause.published is not True
                    or clause.policy_id != request.policy_id
                    or clause.document_version != request.document_version
                    or not clause.id
                    or not 0 < len(clause.title) <= 120
                    or not 0 < len(clause.text) <= 2500
                    or urlsplit(clause.source_url).scheme != "https"
                    or not urlsplit(clause.source_url).hostname
                    or urlsplit(clause.source_url).username is not None
                ):
                    return _fallback(request, "invalid_evidence")
            if generator is None:
                return _fallback(request, "model_unconfigured")
            stage = "generation_failed"
            generated = await generator.generate(
                request, clauses, timeout_seconds=max(0.001, deadline - loop.time())
            )
            if (
                not isinstance(generated, GeneratedExplanation)
                or not generated.text.strip()
                or not generated.source_ids
                or len(set(generated.source_ids)) != len(generated.source_ids)
                or not set(generated.source_ids) <= {clause.id for clause in clauses}
            ):
                return _fallback(request, "invalid_generation")
            evidence = tuple(clause for clause in clauses if clause.id in generated.source_ids)
            return PolicyExplanation(
                request.policy_id, generated.text, True, generated.source_ids, evidence
            )
    except TimeoutError:
        return _fallback(request, "timeout")
    except Exception:
        return _fallback(request, stage)

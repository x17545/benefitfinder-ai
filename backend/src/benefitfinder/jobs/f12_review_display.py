"""Format a verified pair of local F12 review candidates for a person to read."""

import re

from benefitfinder.application.f12_review import (
    F12ReviewDecision,
    f12_item_ids,
    latest_f12_decisions,
)
from benefitfinder.application.policy_facts import PolicyFactsDraft
from benefitfinder.application.policy_rules import RuleCitation, RuleReviewSnapshot


def _terminal_text(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", lambda match: ascii(match[0])[1:-1], text)


def _quote(citation: RuleCitation) -> list[str]:
    return [
        f"  원문 근거 ({_terminal_text(citation.location)}):",
        *(f"    | {_terminal_text(line)}" for line in citation.quote.split("\n")),
    ]


def _decision_lines(item_id: str, entry: F12ReviewDecision | None) -> list[str]:
    if entry is None:
        return [f"- {item_id} · needs_review"]
    label = "승인" if entry.decision == "approve" else "반려"
    return [
        f"- {item_id} · {label} ({entry.decision})",
        f"  검수자: {_terminal_text(entry.reviewer)} · {entry.reviewed_at.isoformat()}",
        f"  판단 사유: {_terminal_text(entry.reason)}",
    ]


def format_f12_review(
    snapshot: RuleReviewSnapshot,
    facts: PolicyFactsDraft,
    decisions: tuple[F12ReviewDecision, ...] = (),
) -> str:
    document = facts.document
    ids = f12_item_ids(snapshot, facts)
    latest = latest_f12_decisions(decisions)
    lines = [
        "F12 검수 자료 · 로컬 후보",
        f"정책: {_terminal_text(document.policy_id)}",
        f"공식 출처: {_terminal_text(document.source_url)}",
        f"문서: {_terminal_text(document.id)} · 버전: {_terminal_text(document.document_version)}",
        f"원문 SHA-256: {document.content_sha256}",
        "",
        "[중복 제한·예외]",
    ]
    for item_id, review in zip(ids["rule"], snapshot.reviews, strict=True):
        lines.extend(_decision_lines(item_id, latest.get(item_id)))
        rule = review.candidate
        targets = ", ".join(map(_terminal_text, rule.target_policy_ids))
        lines.append(f"  규칙 후보: {_terminal_text(rule.id)} · 상대 정책: {targets}")
        lines.append(f"  규칙 유형: {rule.kind.value} · 방향: {rule.direction.value}")
        for label, items in (("제한 조건", rule.conditions), ("예외", rule.exceptions)):
            lines.append(f"  {label}: " + ("; ".join(map(_terminal_text, items)) or "후보 없음"))
        if rule.period:
            lines.append(f"  적용 기간: {_terminal_text(rule.period)}")
        if rule.amount_effect:
            lines.append(f"  금액 영향: {_terminal_text(rule.amount_effect)}")
        for issue in review.issues:
            lines.append(f"  검수 사유: {_terminal_text(issue.code)}")
        for citation in rule.citations:
            lines.extend(_quote(citation))

    lines.extend(("", "[자격 조건]"))
    for item_id, citation in zip(ids["eligibility"], facts.eligibility, strict=True):
        lines.extend(_decision_lines(item_id, latest.get(item_id)))
        lines.extend(_quote(citation))
    if not facts.eligibility:
        lines.append("  근거 후보 없음 (not_found)")

    lines.extend(("", "[지원 금액]"))
    for item_id, amount in zip(ids["amount"], facts.amounts, strict=True):
        lines.extend(_decision_lines(item_id, latest.get(item_id)))
        values = ", ".join(f"{value:,}원" for value in amount.amounts_won)
        lines.append(f"  명시 금액: {values or '미확인'}")
        lines.extend(_quote(amount.evidence))
    if not facts.amounts:
        lines.append("  근거 후보 없음 (not_found)")

    day = facts.scheduled_monthly_day
    lines.extend(("", "[지급 시점]"))
    lines.append(f"  고정 월 지급일: {f'매월 {day}일' if day is not None else '미확인'}")
    for item_id, citation in zip(ids["timing"], facts.payment_timing, strict=True):
        lines.extend(_decision_lines(item_id, latest.get(item_id)))
        lines.extend(_quote(citation))
    if not facts.payment_timing:
        lines.append("  근거 후보 없음 (not_found)")
    return "\n".join(lines)

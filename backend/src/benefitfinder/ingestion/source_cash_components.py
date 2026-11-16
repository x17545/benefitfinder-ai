"""Select an explicitly labelled personal monthly grant from mixed support text.

Selection never removes eligibility or exceptions: callers retain the complete
notice. It only identifies one cash component, without combining alternative
programs, employer subsidies, reimbursement caps or unstated durations.
"""

from dataclasses import dataclass
import re

from benefitfinder.ingestion.source_payment_facts import PaymentFacts, parse_source_payment


@dataclass(frozen=True)
class MonthlyComponent:
    start: int
    end: int
    label: str
    facts: PaymentFacts


_PERSONAL = re.compile(
    r"(?:청년\s*근로자|청\s*년)\s*[:：]\s*"
    r"(?P<label>임금격차지원금|지역정착\s*인센티브)\s*\([^()\n]+\)"
)
_ADDITIONAL = re.compile(
    r"(?P<label>주거\s*생활비|교통비|지역정착\s*인센티브)"
    r"\s*(?:지원)?\s*\([^()\n]+\)(?:\s*지원)?"
)


def select_monthly_personal_component(
    text: str, *, allow_first_year: bool = False,
) -> MonthlyComponent | None:
    """Return the first clearly labelled, fixed monthly personal component.

When more than one independent component exists, only the first one is counted
and its label must be displayed. The remaining source is retained by the caller.
``최대`` or actual-cost qualifiers applying to the selected clause reject it.
"""
    candidates = sorted((*_PERSONAL.finditer(text), *_ADDITIONAL.finditer(text)),
                        key=lambda match: match.start())
    for match in candidates:
        prefix = text[max(0, match.start() - 24):match.start()]
        if re.search(r"(?:최대|한도|상한|실비|실제\s*납부)\s*$", prefix):
            continue
        segment = text[match.start():match.end()]
        parsed = parse_source_payment(segment)
        facts = parsed.amountfact
        if facts is not None and facts.amount_type == "monthly":
            return MonthlyComponent(match.start(), match.end(), match.group("label"), facts)
    if allow_first_year:
        first = re.search(r"(?<!\d)1\s*년\s*차", text)
        if first:
            following = re.search(r"(?<!\d)[2-9]\s*년\s*차", text[first.end():])
            end = first.end() + following.start() if following else len(text)
            # Keep the original grant heading, but stop before another phase or
            # financing component. A numbered business year is not a duration.
            cut = re.search(r"\n[^\n]*(?:융자|대출)", text[:end])
            if cut:
                end = cut.start()
            segment = text[:end].rstrip(" ,;·()\n")
            parsed = parse_source_payment(segment)
            facts = parsed.amountfact
            if facts is not None and facts.amount_type == "monthly":
                return MonthlyComponent(0, len(segment), "독립경영 1년차 정착지원금", facts)
    return None

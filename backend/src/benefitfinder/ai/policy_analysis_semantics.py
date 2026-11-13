"""Conservative source roles for pending evidence, without eligibility calculations."""

import re

from benefitfinder.ai.policy_amounts import won_mentions
from benefitfinder.ai.policy_evidence_context import field_kind


def evidence_kind(text: str, kind: str, start: int, end: int) -> str:
    source_kind = field_kind(text, start, end)
    quote = ''.join(text.splitlines(keepends=True)[start - 1:end])
    if source_kind in {'application', 'program_period'}:
        return source_kind
    if source_kind == 'service':
        return 'service'
    if source_kind == 'eligibility' and kind in {'amount', 'timing', 'service'}:
        return 'eligibility'
    if kind == 'timing' and not payment_context(quote):
        if re.search(r'신청|접수|예약', quote):
            return 'application'
        if re.search(r'사업\s*기간|운영\s*기간', quote):
            return 'program_period'
        return 'service'
    if source_kind == 'support' and kind == 'eligibility' and not re.search(
        r'대상|자격|무주택|미취업|구직자|거주|\d\s*세|소득\s*(?:이하|미만)', quote
    ):
        return 'service'
    return kind


def payment_context(quote: str) -> bool:
    """An application date or a course duration alone is not a payment schedule."""
    if re.search(r'생애\s*(?:1회|한\s*번)', quote):
        return True
    for line in quote.splitlines():
        if not re.search(r'지급|수당|지원금|급여', line):
            continue
        if re.search(r'매월|매주|월별|분기별|매년|일시금|분할\s*지급|'
                     r'월\s*\d[\d~∼～, .]*(?:천|만)?원|\d+\s*일\s*(?:지급|이내|이후|까지)', line):
            return True
        if re.search(r'(?:우선|선)\s*지급|지급\s*후|'
                     r'(?:이행|승인|신청|제출|정산|완료|확인).{0,20}(?:시|후|때).{0,20}(?:지급|지원|수령)', line):
            return True
    return False


def benefit_amounts(quote: str) -> tuple[int, ...]:
    """Exclude explicit own costs/thresholds; retain literal bounds, never derive totals."""
    result: set[int] = set()
    for mention in won_mentions(quote):
        # A role applies within its clause. Do not let an earlier own-cost line
        # swallow a later support cap, or a support cap swallow a housing threshold.
        prefix = re.split(r'[\r\n;。]', quote[:mention.start])[-1]
        roles = list(re.finditer(
            r'(?P<excluded>본인\s*부담|신청인\s*부담|자부담|거래금액|거래가액|임차보증금|'
            r'보증금|월세액|소득(?!지원)|대출\s*(?:원금|한도|금액)|사업\s*예산|총\s*사업비)'
            r'|(?P<benefit>지원금|지원\s*한도|수당|지원내용|지원\s*금액)', prefix
        ))
        if roles and roles[-1].lastgroup == 'excluded':
            continue
        suffix = quote[mention.end:]
        if re.match(r'\s*(?:의\s*)?(?:본인\s*부담|자부담|사업\s*예산)', suffix):
            continue
        result.update(mention.amounts_won)
    return tuple(sorted(result))

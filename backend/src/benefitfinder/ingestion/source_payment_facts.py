"""Deterministic extraction of fixed source payment facts.

``parse_source_payment`` returns ``PaymentParseResult``. A calculation-ready result has
``amountfact`` populated with ``PaymentFacts``; an unsupported or ambiguous source has
``amountfact=None`` and diagnostic reason codes in the outer result. Reasons inside
``PaymentFacts`` describe assumptions made for a successful conservative component.

Monthly and yearly payments use months relative to the start of support. They are calculator
periods, not claims about an actual calendar day or an application-to-payment delay.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Literal, TypeAlias


AmountType: TypeAlias = Literal["monthly", "lump_sum"]
PayTiming: TypeAlias = Literal["monthly_from_start", "once", "yearly"]

_MAX_WON = 2**53 - 1
_MAX_PERIOD_MONTHS = 600


@dataclass(frozen=True, slots=True)
class PaymentFacts:
    """One independently calculable cash payment component.

    ``payments`` contains ``(relative_month, amount_won)`` pairs. For monthly support the
    first period is month 1. A one-time payment may use month 0 only when the source explicitly
    says the current month/day. ``lifetime_once`` is ``None`` when the source does not say it.
    """

    amount_won: int
    amount_type: AmountType
    period_months: int
    pay_timing: PayTiming
    payments: tuple[tuple[int, int], ...]
    evidence_spans: tuple[tuple[int, int], ...]
    amount_text: str
    lifetime_once: bool | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PaymentParseResult:
    """Parse outcome. ``amountfact=None`` is the explicit failure representation."""

    amountfact: PaymentFacts | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Money:
    value: int
    start: int
    end: int
    raw: str


@dataclass(frozen=True, slots=True)
class _Duration:
    months: int
    start: int
    end: int
    maximum: bool


@dataclass(frozen=True, slots=True)
class _Timing:
    month: int
    start: int
    end: int


_MONEY_RE = re.compile(
    r"(?<![\d,])(?P<money>(?:\d[\d,]*(?:\.\d+)?\s*(?:억|만|천|백)?\s*)+원)"
)
_MONEY_PART_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*([억만천백]*)")
_UNIT_WON = {"": 1, "백": 100, "천": 1_000, "만": 10_000, "억": 100_000_000}

_CAP_BEFORE_RE = re.compile(
    r"(?:최대|최장|한도|상한|최소|약)\s*(?:월|연|매월|매년|1인당|회당|가구당)?\s*$"
)
_CAP_AFTER_RE = re.compile(
    r"^\s*\(?\s*(?:까지|이내|이하|이상|초과|미만|내외|정도|한도|상한)"
)
_MAX_AMOUNT_AFTER_RE = re.compile(
    r"^\s*\(?\s*(?:최대|최장)\s*(?:지원액|지원금|금액|한도|상한)"
)
_RANGE_AFTER_RE = re.compile(r"^\s*[~∼～]\s*\d")
_RANGE_BEFORE_RE = re.compile(r"\d\s*[~∼～]\s*$")

_LOAN_RE = re.compile(r"대출|대부|융자|상환|(?<!지)원금|금리|보증한도|담보|LTV|DTI")
_WAGE_RE = re.compile(
    r"급여(?:조건|수준|액)?|임금|시급|일급|월급|생활임금|보수|인건비|최저임금|세전"
)
_WAGE_SUPPORT_RE = re.compile(r"지원금|수당|장려금|인센티브|보조금")
_EMPLOYER_RE = re.compile(r"기업|사업주|사업체|업체|개소당|고용주")
_MONEY_IS_NOT_FIXED_RE = re.compile(
    r"실비|실제\s*(?:납부|결제|부담)|차액|잔액의|비율|퍼센트|차등|구간별|호당|가구별"
)
_IN_KIND_OR_SERVICE_RE = re.compile(
    r"무상\s*(?:제공|대여|이용)|정장\s*대여|무료\s*(?:교육|훈련|강좌|서비스)|"
    r"교육\s*(?:서비스\s*)?바우처|서비스\s*바우처|이용권|감면|할인"
)
_PAYMENT_MEDIUM_RE = re.compile(r"상품권|포인트|바우처|쿠폰|마일리지|[가-힣]{2,10}페이|충전|카드")
_REGIONAL_MEDIUM_RE = re.compile(
    r"지역화폐|지역\s*상품권|온누리\s*상품권|[가-힣]{2,10}사랑\s*상품권|[가-힣]{2,10}페이"
)
_STRONG_FIXED_GRANT_RE = re.compile(
    r"지원금|지원액|정액\s*지원|수당|보조금|장려금|인센티브|면접비|면접비용|응시료"
)
_SPLIT_TOTAL_RE = re.compile(r"분할\s*지급|(?:\d+\s*회|두\s*번|세\s*번)\s*분할")
_INSTALLMENT_COMPONENT_RE = re.compile(r"1\s*회차|회당|1\s*회\s*(?:당|분)")
_THRESHOLD_RE = re.compile(r"소득|재산|연봉|매출|주택가격|보증금|임대료|월세")
_THRESHOLD_TAIL_RE = re.compile(r"이하|미만|초과|한도")
_NOT_GRANT_ACCOUNT_RE = re.compile(r"납입|적립|청약|소득\s*공제|예금|펀드")
_USER_COST_RE = re.compile(r"기숙사비|이용료|사용료|입주비|숙박비|저렴한\s*비용")

_SUPPORT_BEFORE_RE = re.compile(
    r"지원금액|지원내용|지원금|지원액|지원비|지원사업|수당|보조금|장려금|인센티브|"
    r"정착지원금|정착금|축하금|면접비|면접비용|응시료|임차료|월세|주거비|생활비|지원"
)
_SUPPORT_AFTER_RE = re.compile(
    r"지원(?:금|액|비)?|지급|지급액|보조|수당|장려금|인센티브|현금|계좌이체|입금"
)
_MONTHLY_RE = re.compile(r"매월|매달|월마다|월별|월\s*1\s*회|1\s*개월\s*(?:당|지급액)")
_MONTHLY_PREFIX_RE = re.compile(r"(?<![가-힣])월\s*$")
_ANNUAL_PREFIX_RE = re.compile(r"(?<![가-힣])(?:매년|연간|연)\s*$")
_ANNUAL_ONE_RE = re.compile(r"(?<![가-힣])(?:매년|연간|연)\s*1\s*회\s*$")
_ANNUAL_AFTER_RE = re.compile(r"^\s*(?:씩\s*)?(?:매년|연간|연\s*1\s*회)")
_ANNUAL_LIMIT_RE = re.compile(r"(?:매년|연간|연)\s*(?:최대\s*)?\d+\s*회")
_QUARTERLY_RE = re.compile(r"분기별|반기별|격월|두\s*달에\s*한\s*번")

_DURATION_RE = re.compile(
    r"(?P<prefix>최대|최장|최소|약)?\s*(?P<number>\d+)\s*"
    r"(?P<unit>개월|년|회)(?:\s*(?P<tail>간|동안|기간|분|지원|지급))?"
)
_LABELED_DURATION_RE = re.compile(
    r"(?:지원|지급)\s*기간\s*[:：]?\s*(?P<number>\d+)\s*(?P<unit>개월|년)"
)
_DURATION_EXCLUDE_BEFORE_RE = re.compile(
    r"거주|재직|근속|경과|계약|모집|신청|접수|응시|채용|창업|근무|사업\s*기간|운영\s*기간|"
    r"만\s*\d+세\s*$"
)
_DURATION_EXCLUDE_AFTER_RE = re.compile(r"이상|이내|미만|초과|전|이전|후")

_CURRENT_MONTH_RE = re.compile(r"당월|당해\s*월|신청월|접수월|당일|즉시")
_NEXT_MONTH_RE = re.compile(r"익월|다음\s*달|다음달|차월|명월")
_PAYMENT_WORD_RE = re.compile(r"지급(?:일|시기|방법|액)?|입금|계좌이체|송금")
_SEGMENT_BOUNDARY_RE = re.compile(r"(?:\n|\r|□|○|ㅇ|▪|◈|❍|◎)|(?<!\d)[.!?](?!\d)")
_MONTH_OFFSET_RE = re.compile(
    r"(?P<anchor>신청|접수|제출)?\s*(?:일|월)?\s*"
    r"(?:기준|으로부터|부터|후|이후|뒤)?\s*"
    r"(?P<number>\d+)\s*개월\s*(?:후|이후|뒤)"
)
_EVENT_ANCHOR_RE = re.compile(
    r"선정|이수|수료|취업|근속|전입|입주|계약|퇴사|개업|창업|응시"
)
_DAY_DELAY_RE = re.compile(
    r"(?:신청|접수|선정|제출)?\s*(?:일|후)?\s*(?:\d+\s*)?"
    r"(?:일|주)\s*(?:후|이내|이후|뒤)"
)
_LIFETIME_RE = re.compile(r"생애\s*(?:1\s*회|한\s*번)|평생\s*1\s*회")


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _dedupe_spans(spans: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    return tuple(sorted(set(spans)))


def _parse_money(expression: str) -> int | None:
    body = expression[:-1].strip()
    parts = list(_MONEY_PART_RE.finditer(body))
    if not parts:
        return None
    cursor = 0
    total = Decimal(0)
    try:
        for part in parts:
            if body[cursor : part.start()].strip():
                return None
            multiplier = 1
            units = part.group(2)
            if units:
                for unit in units:
                    multiplier *= _UNIT_WON[unit]
            total += Decimal(part.group(1).replace(",", "")) * multiplier
            cursor = part.end()
    except (InvalidOperation, KeyError):
        return None
    if body[cursor:].strip() or total != total.to_integral_value():
        return None
    value = int(total)
    return value if 0 < value <= _MAX_WON else None


def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left = text.rfind("\n", 0, start)
    right = text.find("\n", end)
    return (left + 1 if left >= 0 else 0, right if right >= 0 else len(text))


def _local_span(
    text: str, start: int, end: int, before: int = 140, after: int = 180
) -> tuple[int, int]:
    line_start, line_end = _line_bounds(text, start, end)
    return (max(line_start, start - before), min(line_end, end + after))


def _local_text(text: str, start: int, end: int, before: int = 140, after: int = 180) -> str:
    local_start, local_end = _local_span(text, start, end, before, after)
    return text[local_start:local_end]


def _amount_rejection(text: str, money: _Money) -> str | None:
    before_24 = text[max(0, money.start - 24) : money.start]
    after_24 = text[money.end : min(len(text), money.end + 24)]
    if (
        _CAP_BEFORE_RE.search(before_24)
        or _CAP_AFTER_RE.search(after_24)
        or _MAX_AMOUNT_AFTER_RE.search(after_24)
        or _RANGE_AFTER_RE.search(after_24)
        or _RANGE_BEFORE_RE.search(before_24)
    ):
        return "cash_amount_not_fixed"

    window = _local_text(text, money.start, money.end, before=140, after=220)
    extended = text[max(0, money.start - 120) : min(len(text), money.end + 300)]
    if _MONEY_IS_NOT_FIXED_RE.search(extended):
        return "cash_amount_not_fixed"
    if _LOAN_RE.search(window):
        return "loan_or_financing_not_grant"
    if _IN_KIND_OR_SERVICE_RE.search(window):
        return "noncash_support_not_cash_grant"
    if _NOT_GRANT_ACCOUNT_RE.search(extended):
        return "savings_or_contribution_not_grant"

    before = text[max(0, money.start - 70) : money.start]
    after = text[money.end : min(len(text), money.end + 90)]
    if _USER_COST_RE.search(extended):
        return "user_cost_not_grant"
    if _PAYMENT_MEDIUM_RE.search(window):
        regional_fixed = _REGIONAL_MEDIUM_RE.search(window) and _PAYMENT_WORD_RE.search(
            window
        )
        if not (_STRONG_FIXED_GRANT_RE.search(window) or regional_fixed):
            return "noncash_support_not_cash_grant"
    if _SPLIT_TOTAL_RE.search(window) and not _INSTALLMENT_COMPONENT_RE.search(window):
        return "cash_installment_amount_not_fixed"
    if _WAGE_RE.search(window) and not _WAGE_SUPPORT_RE.search(window):
        return "wage_not_cash_grant"
    employer_recipient = re.search(
        r"(?:\(\s*(?:중소)?기업\s*\)|기업\s*[:：]|기업(?:에|에게|에는|당)|"
        r"업체(?:에|에게|에는|당)|"
        r"사업주|고용주|개소당)",
        before[-90:],
    )
    employer_recipients = list(
        re.finditer(
            r"(?:\(\s*(?:중소)?기업\s*\)|기업\s*[:：]|기업(?:에|에게|에는|당)|"
            r"업체(?:에|에게|에는|당)|사업주|고용주|개소당)",
            before[-120:],
        )
    )
    individual_recipient = re.search(
        r"(?:청년|참여자|근로자|군민|개인)\s*(?:에게|1인당|개인|지원금|수당|참여)",
        before[-55:],
    )
    individual_recipients = list(
        re.finditer(
            r"(?:청년|참여자|근로자|군민|개인)\s*(?:에게|1인당|개인|지원금|수당|참여)",
            before[-120:],
        )
    )
    if employer_recipients and (
        not individual_recipients
        or employer_recipients[-1].start() > individual_recipients[-1].start()
    ):
        return "employer_support_not_cash_grant"
    if re.search(r"참여\s*기업(?:에는|에게|에|당)", before[-90:]) and not re.search(
        r"\(\s*청년\s*\)|청년\s*[:：]", before[-50:]
    ):
        return "employer_support_not_cash_grant"
    if employer_recipient and not individual_recipient:
        return "employer_support_not_cash_grant"
    if _EMPLOYER_RE.search(before[-55:]) and not re.search(
        r"청년|근로자|참여자|개인", before[-55:]
    ):
        return "employer_support_not_cash_grant"
    if re.search(r"인건비|고용장려금|기업\s*지원", window):
        return "employer_support_not_cash_grant"
    if (
        _THRESHOLD_RE.search(before)
        and _THRESHOLD_TAIL_RE.search(after[:45])
        and not _SUPPORT_AFTER_RE.search(after[:45])
    ):
        return "income_or_cost_threshold_not_grant"
    return None


def _has_support_anchor(text: str, money: _Money) -> bool:
    before = text[max(0, money.start - 80) : money.start]
    after = text[money.end : min(len(text), money.end + 80)]
    return bool(
        _SUPPORT_BEFORE_RE.search(before)
        or _SUPPORT_AFTER_RE.search(after)
        or (_MONTHLY_PREFIX_RE.search(before) and _SUPPORT_AFTER_RE.search(after))
    )


def _monthly_marker(text: str, money: _Money) -> tuple[int, int] | None:
    start = max(0, money.start - 45)
    end = min(len(text), money.end + 65)
    window = text[start:end]
    marker = _MONTHLY_RE.search(window)
    if marker:
        return (start + marker.start(), start + marker.end())
    before = text[max(0, money.start - 18) : money.start]
    prefix = _MONTHLY_PREFIX_RE.search(before)
    if prefix:
        marker_start = max(0, money.start - 18) + prefix.start()
        return (marker_start, marker_start + len(prefix.group()))
    suffix = re.match(r"\s*/\s*월", text[money.end:])
    if suffix:
        return money.end + suffix.start(), money.end + suffix.end()
    return None


def _annual_marker(text: str, money: _Money) -> tuple[int, int] | None:
    before_start = max(0, money.start - 30)
    before = text[before_start : money.start]
    marker = _ANNUAL_PREFIX_RE.search(before) or _ANNUAL_ONE_RE.search(before)
    if marker:
        return (before_start + marker.start(), before_start + marker.end())
    after = text[money.end : min(len(text), money.end + 45)]
    marker = _ANNUAL_AFTER_RE.search(after)
    if marker:
        return (money.end + marker.start(), money.end + marker.end())
    return None


def _duration_is_exact_context(text: str, match: re.Match[str]) -> bool:
    before = text[max(0, match.start() - 28) : match.start()]
    after = text[match.end() : min(len(text), match.end() + 28)]
    if re.match(r"\s*(?:차|이상|이내|이하|미만|초과)", after):
        return False
    if _DURATION_EXCLUDE_BEFORE_RE.search(before):
        return False
    if _DURATION_EXCLUDE_AFTER_RE.search(after) and not re.search(r"지원|지급", after):
        return False
    return True


def _find_duration(text: str, money: _Money, *, monthly: bool) -> _Duration | None:
    candidates: list[_Duration] = []
    start = max(0, money.start - 90)
    end = min(len(text), money.end + 190)
    for match in _DURATION_RE.finditer(text, start, end):
        unit = match.group("unit")
        if unit == "회" and not monthly:
            continue
        if not monthly and unit == "회":
            continue
        if not _duration_is_exact_context(text, match):
            continue
        number = int(match.group("number"))
        months = number if unit in {"개월", "회"} else number * 12
        maximum = match.group("prefix") in {"최대", "최장", "최소", "약"}
        candidates.append(_Duration(months, match.start(), match.end(), maximum))

    labeled = _LABELED_DURATION_RE.search(text, max(0, money.start - 180), end)
    if labeled and _duration_is_exact_context(text, labeled):
        months = int(labeled.group("number")) * (1 if labeled.group("unit") == "개월" else 12)
        candidates.append(_Duration(months, labeled.start(), labeled.end(), False))

    if not candidates:
        return None
    distances = [min(abs(candidate.start - money.end), abs(money.start - candidate.end))
                 for candidate in candidates]
    return candidates[distances.index(min(distances))]


def _segment_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left = 0
    right = len(text)
    for boundary in _SEGMENT_BOUNDARY_RE.finditer(text):
        if boundary.end() <= start:
            left = boundary.end()
        elif boundary.start() >= end:
            right = boundary.start()
            break
    return left, right


def _near_payment_label(text: str, start: int, end: int) -> bool:
    left, right = _segment_bounds(text, start, end)
    return bool(_PAYMENT_WORD_RE.search(text[left:right]))


def _find_offset(text: str) -> tuple[_Timing | None, bool, bool]:
    """Return an exact month offset, plus day-delay and unsupported-anchor flags."""

    offsets: list[_Timing] = []
    for current in _CURRENT_MONTH_RE.finditer(text):
        if _near_payment_label(text, current.start(), current.end()):
            offsets.append(_Timing(0, current.start(), current.end()))
    for next_month in _NEXT_MONTH_RE.finditer(text):
        if _near_payment_label(text, next_month.start(), next_month.end()):
            offsets.append(_Timing(1, next_month.start(), next_month.end()))

    for match in _MONTH_OFFSET_RE.finditer(text):
        if not _near_payment_label(text, match.start(), match.end()):
            continue
        before = text[max(0, match.start() - 24) : match.start()]
        anchor_text = (match.group("anchor") or "") + before
        if _EVENT_ANCHOR_RE.search(anchor_text) and not re.search(r"신청|접수|제출", anchor_text):
            continue
        offsets.append(_Timing(int(match.group("number")), match.start(), match.end()))

    unique = {(offset.month, offset.start, offset.end) for offset in offsets}
    ordered = [_Timing(*item) for item in sorted(unique, key=lambda item: (item[1], item[0]))]
    months = {offset.month for offset in ordered}
    exact = ordered[0] if len(months) == 1 and ordered else None
    day_delay = any(
        _near_payment_label(text, match.start(), match.end())
        for match in _DAY_DELAY_RE.finditer(text)
    )
    event_anchor = bool(_EVENT_ANCHOR_RE.search(text)) and not bool(_PAYMENT_WORD_RE.search(text))
    return exact, day_delay, event_anchor


def _lifetime_once(text: str) -> bool | None:
    if _LIFETIME_RE.search(text):
        return True
    return None


def _hold(*reasons: str) -> PaymentParseResult:
    return PaymentParseResult(None, _dedupe(reasons))


def parse_source_payment(text: str) -> PaymentParseResult:
    """Extract one fixed cash payment component from official source text.

    Caps, reimbursements, differential amounts, wages, employer support, financing, and
    non-cash benefits are not converted into a grant. When a monthly amount is fixed but the
    only duration is a maximum (or no duration is stated), one relative month is returned.
    """

    if not isinstance(text, str) or not text.strip():
        return _hold("payment_text_empty")

    money_candidates: dict[int, tuple[_Money, str | None]] = {}
    rejection_reasons: list[str] = []
    for match in _MONEY_RE.finditer(text):
        raw = match.group("money")
        value = _parse_money(raw)
        if value is None:
            rejection_reasons.append("cash_amount_invalid")
            continue
        money = _Money(value, match.start("money"), match.end("money"), raw)
        if not _has_support_anchor(text, money):
            continue
        rejection = _amount_rejection(text, money)
        if rejection:
            rejection_reasons.append(rejection)
            continue
        money_candidates.setdefault(value, (money, None))

    if not money_candidates:
        return _hold("cash_amount_not_found_or_not_fixed", *rejection_reasons)
    if len(money_candidates) > 1:
        return _hold("cash_amount_ambiguous", "multiple_fixed_payment_amounts")

    amount = next(iter(money_candidates.values()))[0]
    monthly_span = _monthly_marker(text, amount)
    annual_span = _annual_marker(text, amount)
    if monthly_span and annual_span:
        return _hold("payment_frequency_ambiguous")

    local_start, local_end = _local_span(text, amount.start, amount.end, before=180, after=240)
    local = text[local_start:local_end]
    if _QUARTERLY_RE.search(local) or any(
        _near_payment_label(text, match.start(), match.end())
        for match in _QUARTERLY_RE.finditer(text)
    ):
        return _hold("payment_frequency_not_monthly_or_once")

    base_reasons = ["exact_cash_amount"]
    if _ANNUAL_LIMIT_RE.search(text):
        base_reasons.append("annual_limit_not_multiplied")
    lifetime_once = _lifetime_once(local)
    if lifetime_once is True:
        base_reasons.append("lifetime_once_explicit")

    duration = _find_duration(text, amount, monthly=bool(monthly_span))
    monthly = monthly_span is not None
    annual = annual_span is not None

    if monthly:
        exact_duration = duration if duration and not duration.maximum else None
        period = exact_duration.months if exact_duration else 1
        if period > _MAX_PERIOD_MONTHS:
            return _hold("payment_period_exceeds_limit")
        if exact_duration is None:
            base_reasons.extend(
                (
                    "conservative_one_month_component",
                    "maximum_period_not_used_as_total"
                    if duration
                    else "support_period_not_stated",
                )
            )
        else:
            base_reasons.append("exact_support_period_used")
        base_reasons.append("actual_calendar_schedule_unknown")
        spans = [(amount.start, amount.end), monthly_span]
        if duration:
            spans.append((duration.start, duration.end))
        payments = tuple((month, amount.value) for month in range(1, period + 1))
        note = (
            f"월별 지원, 확정 {period}개월분; actual_calendar_schedule_unknown"
            if exact_duration
            else "전체 기간 합계가 아닌 1개월분; actual_calendar_schedule_unknown"
        )
        amount_text = f"{amount.raw} ({note})"
        facts = PaymentFacts(
            amount.value,
            "monthly",
            period,
            "monthly_from_start",
            payments,
            _dedupe_spans(spans),
            amount_text,
            lifetime_once,
            _dedupe(tuple(base_reasons)),
        )
        return PaymentParseResult(facts, facts.reasons)

    if annual:
        exact_duration = duration if duration and not duration.maximum else None
        years = 1
        if exact_duration and exact_duration.months % 12 == 0:
            years = exact_duration.months // 12
        if exact_duration is None:
            base_reasons.extend(
                (
                    "conservative_one_year_component",
                    "maximum_period_not_used_as_total"
                    if duration
                    else "support_period_not_stated",
                )
            )
        else:
            if exact_duration.months != years * 12:
                base_reasons.append("annual_period_not_in_whole_years")
            base_reasons.append("exact_support_period_used")
        period = years * 12
        if period > _MAX_PERIOD_MONTHS:
            return _hold("payment_period_exceeds_limit")
        base_reasons.append("actual_calendar_schedule_unknown")
        spans = [(amount.start, amount.end), annual_span]
        if duration:
            spans.append((duration.start, duration.end))
        payments = tuple((1 + 12 * index, amount.value) for index in range(years))
        note = (
            f"연별 고정금액, 확정 {years}년분; actual_calendar_schedule_unknown"
            if exact_duration
            else "전체 기간 합계가 아닌 1년분; actual_calendar_schedule_unknown"
        )
        facts = PaymentFacts(
            amount.value,
            "lump_sum",
            period,
            "yearly",
            payments,
            _dedupe_spans(spans),
            f"{amount.raw} ({note})",
            lifetime_once,
            _dedupe(tuple(base_reasons)),
        )
        return PaymentParseResult(facts, facts.reasons)

    timing, day_delay, event_anchor = _find_offset(text)
    if timing is None:
        if day_delay:
            return _hold("payment_timing_not_integer_month")
        if event_anchor:
            return _hold("payment_anchor_unsupported")
        return _hold("one_time_payment_timing_unknown")
    if _MONTH_OFFSET_RE.search(local) and "combination" in text:
        return _hold("payment_timing_ambiguous")

    facts = PaymentFacts(
        amount.value,
        "lump_sum",
        1,
        "once",
        ((timing.month, amount.value),),
        _dedupe_spans([(amount.start, amount.end), (timing.start, timing.end)]),
        f"{amount.raw} (1회분, 지급기준 상대월 {timing.month})",
        lifetime_once,
        _dedupe(
            tuple(
                base_reasons
                + ["exact_one_time_payment_timing", "actual_calendar_schedule_unknown"]
            )
        ),
    )
    return PaymentParseResult(facts, facts.reasons)


__all__ = [
    "AmountType",
    "PayTiming",
    "PaymentFacts",
    "PaymentParseResult",
    "parse_source_payment",
]

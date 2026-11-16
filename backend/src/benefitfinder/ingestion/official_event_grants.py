"""Compile one explicitly fixed interview allowance from official source facts.

``as_of`` is the application reference date, including the payment month basis.
Annual limits and historical eligibility remain manual, never extra payments.
This pure builder neither reviews with a model nor publishes a release.
"""

from datetime import date
from decimal import Decimal
import re
import unicodedata

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import AnalysisPolicy, PolicyAnalysisInput


_MONEY = re.compile(
    r'(?P<number>(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)'
    r'\s*(?P<unit>억|만|천)?\s*원'
)
_WEEKDAY = r'(?:\s*\([월화수목금토일](?:요일)?\))?'
_DATE_RANGE = re.compile(
    r'(?P<sy>20[0-9]{2})\s*[.년/-]\s*(?P<sm>[0-9]{1,2})\s*[.월/-]\s*'
    r'(?P<sd>[0-9]{1,2})\s*[.일]?' + _WEEKDAY + r'\s*[~∼～–-]\s*'
    r'(?:(?P<ey>20[0-9]{2})\s*[.년/-]\s*)?'
    r'(?P<em>[0-9]{1,2})\s*[.월/-]\s*(?P<ed>[0-9]{1,2})\s*[.일]?'
    + _WEEKDAY
)
_APPLICATION_LABEL = re.compile(r'(?:신청|접수|모집)\s*(?:기간|기한)\s*\)?\s*[:：]?\s*')


class _Hold(ValueError):
    pass


def _event_amount(text: str) -> tuple[int, list[tuple[int, int]]]:
    if re.search(r'실비|실제\s*(?:비용|지출|소요)|차등|차감|공제|감액|비례', text):
        raise _Hold('event_grant_amount_not_fixed')
    anchors = list(re.finditer(r'(?:1\s*)?회\s*당\s*', text))
    amounts = set()
    spans = []
    for anchor in anchors:
        money = _MONEY.match(text, anchor.end())
        if money is None:
            raise _Hold('event_grant_amount_not_fixed')
        suffix = re.match(r'[^,，)）\n]*', text[money.end():]).group()
        if re.search(r'최대|한도|이내|이하|이상|미만|초과|상한|[~∼～]', suffix):
            raise _Hold('event_grant_amount_not_fixed')
        amount = Decimal(money['number'].replace(',', '')) * {
            None: 1, '천': 1_000, '만': 10_000, '억': 100_000_000,
        }[money['unit']]
        if amount != amount.to_integral_value() or not 0 < amount <= 2**53 - 1:
            raise _Hold('event_grant_amount_invalid')
        amounts.add(int(amount))
        spans.append((anchor.start(), money.end()))
    if not anchors:
        raise _Hold('event_grant_per_event_amount_required')
    if len(amounts) != 1:
        raise _Hold('event_grant_amount_ambiguous')
    return amounts.pop(), spans


def _application_dates(text: str) -> tuple[date, date]:
    ranges = set()
    for label in _APPLICATION_LABEL.finditer(text):
        match = _DATE_RANGE.match(text, label.end())
        if match is None:
            raise _Hold('event_grant_application_dates_unknown')
        try:
            start = date(int(match['sy']), int(match['sm']), int(match['sd']))
            end = date(int(match['ey'] or match['sy']), int(match['em']), int(match['ed']))
        except ValueError:
            raise _Hold('event_grant_application_dates_invalid') from None
        if start > end:
            raise _Hold('event_grant_application_dates_invalid')
        ranges.add((start, end))
    if len(ranges) != 1:
        raise _Hold('event_grant_application_dates_unknown')
    return ranges.pop()


def _payment_timing(text: str, as_of: date) -> tuple[int, list[tuple[int, int]]]:
    next_month = list(re.finditer(
        r'신청(?:일)?\s*기준\s*(?:다음\s*달|익월)\s*말(?:일)?\s*'
        r'(?:까지|이내)?[^\n.!?]{0,60}?지급', text,
    ))
    if not next_month:
        raise _Hold('event_grant_payment_timing_unknown')
    december = list(re.finditer(
        r'(?:매년\s*)?12\s*월\s*(?:신청|접수)(?:분|건)?(?:은|는)?\s*'
        r'[^\n.!?]{0,50}?10\s*일\s*까지\s*접수\s*(?:후|하여)?[\s,，]*'
        r'(?:당월|해당\s*월|같은\s*달)[^\n.!?]{0,40}?지급', text,
    ))
    if re.search(r'12\s*월\s*(?:신청|접수)', text) and not december:
        raise _Hold('event_grant_december_timing_unknown')
    spans = [match.span() for match in (*next_month, *december)]
    return (0 if as_of.month == 12 and december else 1), spans


def compile_official_event_grant(
    value: PolicyAnalysisInput,
    *,
    as_of: date,
    region_codes: tuple[str, ...] = (),
    conflict_policies: tuple[AnalysisPolicy, ...] = (),
) -> Compilation:
    """Prepare one application, holding unsupported or ambiguous source facts.

    Region codes must already be verified by the caller. Source identity and
    current material are checked again by the existing publication writer.
    """
    normalized = {
        'mode': 'official_event_source_facts', 'status': 'held',
        'calculation_ready': False, 'ai_review_calls': 0, 'model_analysis': 'not_run',
    }
    official = [s for s in value.collection_sources if s.source == 'official_documents']
    api = [s for s in value.collection_sources if s.source == 'youthcenter']
    if (len(value.documents) != 1 or len(official) != 1 or len(api) > 1
            or len(value.collection_sources) != len(official) + len(api)):
        return Compilation(None, ('event_grant_official_primary_required',), normalized)
    doc = value.documents[0]
    if (not doc.official or doc.policy_id != value.policy_id
            or doc.document_version != 'official-v1:' + official[0].material_sha256
            or any(binding.source != 'official_documents'
                   or binding.external_id != official[0].external_id
                   for binding in value.document_sources)
            or (api and (re.fullmatch(r'[0-9]+', api[0].external_id) is None
                         or value.policy_id not in (
                             'youth:' + api[0].external_id,
                             'youth:youthcenter:' + api[0].external_id)))):
        return Compilation(None, ('event_grant_source_mismatch',), normalized)
    if (not isinstance(region_codes, tuple)
            or any(not isinstance(code, str) or re.fullmatch(r'[0-9]{5}', code) is None
                   for code in region_codes)):
        return Compilation(None, ('event_grant_region_codes_invalid',), normalized)
    target = next(policy for policy in value.policies if policy.id == value.policy_id)
    text = doc.text
    if 'employment' not in target.categories or not re.search(r'면접\s*수당', text):
        return Compilation(None, ('event_grant_interview_allowance_required',), normalized)
    try:
        amount, amount_spans = _event_amount(text)
        starts_on, ends_on = _application_dates(text)
        payment_month, timing_spans = _payment_timing(text, as_of)
    except _Hold as exc:
        return Compilation(None, (str(exc),), normalized)

    source = {'url': doc.source_url, 'document_version': doc.document_version,
              'checked_at': doc.collected_at.isoformat()}

    def evidence(start: int, end: int) -> dict:
        return {'quote': text[start:end], 'location': f'chars:{start}-{end}',
                'source': source.copy()}

    conditions = []
    executable = []
    for index, start in enumerate(range(0, len(text), 2500), 1):
        identity = f'official_terms_{index}'
        end = min(len(text), start + 2500)
        conditions.append({'id': identity, 'description': '공식 원문 조건 확인: ' + text[start:end],
                           'evidence': [evidence(start, end)]})
        executable.append({'condition_id': identity, 'kind': 'manual'})
    residence = re.search(
        r'(?:지원금\s*)?신청일\s*기준\s*[가-힣]+(?:시|군|구)\s*에?\s*'
        r'주민\s*등록\s*을?\s*(?:둔|두고\s*있는)\s*자', text,
    )
    if region_codes and residence:
        conditions.append({'id': 'residence', 'description': residence.group(),
                           'evidence': [evidence(*residence.span())]})
        executable.append({'condition_id': 'residence', 'kind': 'selection',
                           'field': 'region_code', 'values': list(dict.fromkeys(region_codes))})

    conflicts = []
    ban = re.search(
        r'국민\s*취업\s*지원\s*제도(?:와|과)?\s*(?:중복|동시)\s*'
        r'(?:수혜|지원|수급|참여)?\s*(?:은|는)?\s*(?:불가|금지|제한)', text,
    )
    if ban:
        # Qualified bans remain manual: a global conflict cannot encode exceptions.
        line_start = text.rfind('\n', 0, ban.start()) + 1
        line_end = text.find('\n', ban.end())
        line = text[line_start:line_end if line_end != -1 else len(text)]
        if not re.search(r'다만|예외|경우|단[,\s(]', line):
            for policy in conflict_policies:
                name = re.sub(r'\s+', '', unicodedata.normalize('NFKC', policy.name))
                if (name == '국민취업지원제도' and policy.id != value.policy_id
                        and policy.id not in {item['policy_id'] for item in conflicts}):
                    conflicts.append({'policy_id': policy.id, 'evidence': [evidence(*ban.span())]})

    amount_text = f'1회 신청분 면접수당 {amount:,}원 (연간 한도·횟수 등은 원문 조건 확인)'
    payload = {
        'benefit': {
            'id': value.policy_id, 'domain': 'youth', 'title': target.name,
            'summary': amount_text, 'provider': '공식 공고 기관',
            'categories': list(target.categories),
            'application': {
                'status': ('upcoming' if as_of < starts_on else
                           'closed' if as_of > ends_on else 'open'),
                'starts_on': starts_on.isoformat(), 'ends_on': ends_on.isoformat(), 'url': None,
            },
            'eligibility_conditions': conditions, 'source': source, 'domain_data': {},
            'benefit_amount': {
                'type': 'lump_sum', 'amount_won': amount, 'period_months': 1,
                'max_total_won': amount, 'pay_timing': 'once', 'amount_text': amount_text,
                'amount_status': 'verified',
            },
            'reviewed': True, 'conflicts': conflicts, 'lifetime_once': None, 'virtual_data': False,
        },
        'conditions': executable, 'payments': [{'month': payment_month, 'amount_won': amount}],
        'payment_evidence': [evidence(start, end) for start, end in amount_spans + timing_spans],
        # Manual eligibility is checked separately; other policy pairs stay
        # unverified in the existing recommendation service, never presumed allowed.
        'unresolved_combination_rules': False, 'source_sha256': doc.content_sha256,
        'collection_sources': [
            {'source': s.source, 'external_id': s.external_id, 'material_sha256': s.material_sha256}
            for s in value.collection_sources
        ],
    }
    normalized.update(status='compiled', calculation_ready=True, amount_won=amount,
                      payment_month=payment_month, basis='one_application',
                      source_sha256=doc.content_sha256)
    return Compilation(payload, (), normalized)

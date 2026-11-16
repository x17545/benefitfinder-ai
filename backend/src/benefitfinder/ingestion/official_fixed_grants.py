"""Compile explicitly stated single grants without a model review call.

Only a fixed grant with an explicit next-month payment rule is supported. All
eligibility, history, exclusions and common terms remain manual conditions;
residence can additionally use the injected official province catalogue.
"""

from datetime import date
import re
from collections.abc import Sequence

from benefitfinder.application.decider import Compilation
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.application.region_options import RegionOption
from benefitfinder.ingestion.decider_normalization import _parse_money


def compile_official_fixed_grant(
    value: PolicyAnalysisInput, *, as_of: date, regions: Sequence[RegionOption] = (),
) -> Compilation:
    normalized = {'calculation_ready': False, 'mode': 'source_facts', 'ai_review_calls': 0}
    if len(value.documents) != 1 or not any(s.source == 'official_documents' for s in value.collection_sources):
        return Compilation(None, ('fixed_grant_official_primary_required',), normalized)
    doc = value.documents[0]
    text = doc.text
    target = next(p for p in value.policies if p.id == value.policy_id)
    if not doc.official or doc.policy_id != value.policy_id:
        return Compilation(None, ('fixed_grant_source_mismatch',), normalized)
    amount_lines = list(re.finditer(r'(?m)^.*\(지원금액\)[^\n]*$', text))
    if len(amount_lines) != 1:
        return Compilation(None, ('fixed_grant_amount_line_ambiguous',), normalized)
    amount_line = amount_lines[0]
    amount_text = amount_line.group()
    if re.search(r'최대|이내|상한|한도|실비|차등|차감|공제|분할|이상|미만|초과|[~∼]', amount_text):
        return Compilation(None, ('fixed_grant_amount_not_fixed',), normalized)
    try:
        money = _parse_money(amount_text)
    except ValueError:
        money = ()
    if len(money) != 1 or money[0].value <= 0:
        return Compilation(None, ('fixed_grant_amount_invalid',), normalized)
    if re.search(r'생애\s*1\s*회|1\s*회\s*(?:정액|일시)', amount_text) is None:
        return Compilation(None, ('fixed_grant_one_off_not_explicit',), normalized)
    timing = re.search(
        r'(?m)^.*매월\s*말(?:일)?까지(?:의)?\s*(?:접수분을?|접수)'
        r'[^\n]*다음\s*달\s*20일\s*이내[^\n]*지급[^\n]*$', text,
    )
    if timing is None:
        return Compilation(None, ('fixed_grant_payment_timing_unknown',), normalized)
    date_pattern = r'(20\d{2})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?'
    application_line = re.search(r'(?m)^.*\(신청기간\)[^\n]*$', text)
    matches = [] if application_line is None else re.findall(date_pattern, application_line.group())
    try:
        dates = [date(*(int(part) for part in groups)) for groups in matches]
    except ValueError:
        dates = []
    if len(dates) != 2 or dates[0] > dates[1]:
        return Compilation(None, ('fixed_grant_application_dates_unknown',), normalized)
    if '□ 자격요건' not in text or '□ 기타 유의사항' not in text:
        return Compilation(None, ('fixed_grant_complete_terms_required',), normalized)
    source = {'url': doc.source_url, 'document_version': doc.document_version,
              'checked_at': doc.collected_at.isoformat()}

    def evidence(start: int, end: int) -> dict:
        return {'quote': text[start:end], 'location': f'chars:{start}-{end}', 'source': source.copy()}

    conditions = []
    executable = []
    for index, start in enumerate(range(0, len(text), 2400), 1):
        identity = f'official_terms_{index}'
        end = min(len(text), start + 2400)
        conditions.append({'id': identity, 'description': '공고의 자격·과거 수혜·제외·서류 요건 확인: ' + text[start:end],
                           'evidence': [evidence(start, end)]})
        executable.append({'condition_id': identity, 'kind': 'manual'})
    lifetime_once = bool(re.search(r'생애\s*1\s*회', amount_text))
    if lifetime_once:
        conditions.append({'id': 'no_previous_grant', 'description': '이 정책의 과거 수혜 이력이 없어야 함',
                           'evidence': [evidence(amount_line.start(), amount_line.end())]})
        executable.append({'condition_id': 'no_previous_grant', 'kind': 'none_of',
                           'field': 'domain_data.received', 'values': [value.policy_id]})
    # Decode only an exact province name from the verified official catalogue.
    for region in regions:
        match = re.search(re.escape(region.sido_name) + r'에?\s*주민등록[^\n]*', text)
        if match:
            conditions.append({'id': 'residence', 'description': match.group(),
                               'evidence': [evidence(match.start(), match.end())]})
            executable.append({'condition_id': 'residence', 'kind': 'selection',
                               'field': 'domain_data.region.sido_code', 'values': [region.sido_code]})
            break
    amount = money[0].value
    # Month 0 is the application month; the source explicitly says next month.
    payment_evidence = [evidence(amount_line.start(), amount_line.end()), evidence(timing.start(), timing.end())]
    payload = {
        'benefit': {'id': value.policy_id, 'domain': 'youth', 'title': target.name,
                    'summary': amount_text.strip() + ' / ' + timing.group().strip(),
                    'provider': next((region.sido_name for region in regions if region.sido_name in text), '공식 공고 기관'),
                    'categories': list(target.categories),
                    'application': {'status': 'upcoming' if as_of < dates[0] else 'closed' if as_of > dates[1] else 'open',
                                    'starts_on': dates[0].isoformat(), 'ends_on': dates[1].isoformat(), 'url': None},
                    'eligibility_conditions': conditions, 'source': source, 'domain_data': {},
                    'benefit_amount': {'type': 'lump_sum', 'amount_won': amount, 'period_months': 1,
                                       'max_total_won': amount, 'pay_timing': 'once',
                                       'amount_text': amount_text.strip() + ' (신청월 기준 다음 달 지급, 자격 확인 필요)',
                                       'amount_status': 'verified'},
                    'reviewed': True, 'conflicts': [],
                    'lifetime_once': lifetime_once, 'virtual_data': False},
        'conditions': executable, 'payments': [{'month': 1, 'amount_won': amount}],
        'payment_evidence': payment_evidence,
        # Every stated historical restriction remains a manual eligibility clause.
        # Other pairs remain unverified in the existing recommendation service.
        'unresolved_combination_rules': False, 'source_sha256': doc.content_sha256,
        'collection_sources': [{'source': s.source, 'external_id': s.external_id,
                                'material_sha256': s.material_sha256} for s in value.collection_sources],
    }
    normalized.update(calculation_ready=True, amount_won=amount,
                      payment_month=1, historical_restrictions='preserved_in_manual_conditions',
                      source_sha256=doc.content_sha256)
    return Compilation(payload, (), normalized)

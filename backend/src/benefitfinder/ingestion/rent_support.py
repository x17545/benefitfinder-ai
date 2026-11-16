"""Compile explicit second-round rent terms into reviewable executable rules.

API scalars are the baseline. A located, source-bound contradiction produces an
auditable replacement candidate, never an unrecorded model overwrite.
"""

from dataclasses import asdict
from datetime import date
from decimal import Decimal
import re
from typing import Any

from benefitfinder.application.decider import DocumentExtraction
from benefitfinder.application.policy_analysis import PolicyAnalysisInput
from benefitfinder.ingestion.decider_source_fields import prepare_source_fields


def rent_support_candidate(
    value: PolicyAnalysisInput, extracted: tuple[DocumentExtraction, ...],
) -> dict[str, Any] | None:
    target = next(p for p in value.policies if p.id == value.policy_id)
    if ('housing' not in target.categories or '청년월세' not in target.name.replace(' ', '')
            or '2차' not in target.name or len(value.documents) != 1):
        return None
    doc = value.documents[0]
    if not doc.official or doc.policy_id != value.policy_id:
        return None
    plan = prepare_source_fields(doc.text)
    if plan.issues:
        return None
    fields = {f.name: f for f in plan.body}
    income = fields.get('earnEtcCn')
    additional = fields.get('addAplyQlfcCndCn')
    support = fields.get('plcySprtCn')
    if not income or not additional or not support:
        return None

    def financial(text: str, household: str) -> tuple[int, int] | None:
        matches = re.findall(
            household + r'\s*:\s*기준\s*중위소득\s*(\d+)%\s*이하'
            r'(?:이며|이면서)\s*재산가액이\s*(\d+(?:\.\d+)?)\s*억원\s*이하', text,
        )
        unique = {(int(ratio) * 100, int(Decimal(asset) * 100_000_000))
                  for ratio, asset in matches}
        return next(iter(unique)) if len(unique) == 1 else None

    youth = financial(income.text, '청년가구')
    original = financial(income.text, '원가구')
    # Both copies must agree, including their household identities.
    if (not youth or not original or financial(additional.text, '청년가구') != youth
            or financial(additional.text, '원가구') != original):
        return None
    age = re.search(r'연령조건\s*:\s*만\s*(\d+)\s*[~～∼]\s*(\d+)세', additional.text)
    exemption = re.search(r'다만,\s*(\d+)세\s*이상이거나\s*혼인[^\n]*청년\s*본인가구의\s*소득·재산만\s*확인', additional.text)
    if not age or not exemption or '청약통장가입' not in additional.text:
        return None
    if '부모로부터 독립한 무주택 청년가구' not in additional.text:
        return None
    cap = re.search(r'실제\s*납부하는\s*임대료\s*범위\s*내에서\s*월\s*최대\s*(\d+)만원씩\s*(\d+)개월\s*동안\s*매월\s*분할\s*지원', support.text)
    deduction = re.search(r'주거급여수급자도\s*월세지원\s*한도액\s*(\d+)만원에서\s*주거급여액을\s*차감하고\s*지원', support.text)
    period = re.search(r'지급기간\s*:\s*(20\d{2})년\s*(\d+)월\s*[~～∼]\s*(20\d{2})년\s*(\d+)월', support.text)
    if not cap or not deduction or not period or cap[1] != deduction[1]:
        return None
    parameters = {
        'youth_income_max_bps': youth[0], 'original_income_max_bps': original[0],
        'youth_asset_max_won': youth[1], 'original_asset_max_won': original[1],
        'parent_exempt_min_age': int(exemption[1]), 'monthly_cap_won': int(cap[1]) * 10000,
        'maximum_months': int(cap[2]),
        'support_starts_month': f'{period[1]}-{int(period[2]):02d}',
        'support_ends_month': f'{period[3]}-{int(period[4]):02d}',
    }
    raw_spans = []
    for extraction in extracted:
        if extraction.document != doc:
            return None
        for span in extraction.raw_spans or ():
            if (span.method != 'locate' or not 0 <= span.start < span.end <= len(doc.text)
                    or doc.text[span.start:span.end] != span.text):
                return None
            raw_spans.append(span)

    def evidence(field: Any) -> dict[str, Any]:
        return {'document_id': doc.id, 'start': field.start, 'end': field.end,
                'text': field.text, 'source_sha256': doc.content_sha256}

    changes = []
    for field_name, new_value in (('sprtTrgtMinAge', int(age[1])), ('sprtTrgtMaxAge', int(age[2]))):
        old = plan.structured.get(field_name, {}).get('value')
        if old != new_value:
            found = [asdict(s) for s in raw_spans
                     if s.kind == 'eligibility'
                     and s.start < additional.start + age.end()
                     and s.end > additional.start + age.start()]
            changes.append({'field': field_name, 'before': old, 'after': new_value,
                            'status': 'needs_review', 'trigger': 'decider' if found else 'code',
                            'model_evidence': found, 'evidence': evidence(additional)})
    changes.append({
        'field': 'income_rule',
        'before': {name: plan.structured.get(name, {}).get('raw')
                   for name in ('earnCndSeCd', 'earnMinAmt', 'earnMaxAmt')},
        'after': {key: parameters[key] for key in (
            'youth_income_max_bps', 'original_income_max_bps',
            'youth_asset_max_won', 'original_asset_max_won')},
        'operation': 'supplement_household_rules_not_zero_income_override',
        'status': 'code_validated_candidate', 'evidence': evidence(income),
    })
    return {
        'rule': parameters,
        'conditions': [
            {'condition_id': 'age', 'kind': 'age', 'minimum': int(age[1]),
             'maximum': int(age[2]), 'military_extension_years': 0},
            {'condition_id': 'home_ownership', 'kind': 'selection',
             'field': 'domain_data.housing.home_ownership', 'values': ['homeless']},
            {'condition_id': 'contract', 'kind': 'selection',
             'field': 'domain_data.contract.type', 'values': ['monthly_rent']},
            {'condition_id': 'rent_support', 'kind': 'rent_support', 'rent_support': parameters},
        ],
        'field_updates': changes,
        'evidence': [evidence(f) for f in (income, additional, support)],
        'source_fields': {name: item['raw'] for name, item in plan.structured.items()},
        'body_fields': [asdict(f) for f in plan.body],
        'publication_ready': False,
        'review_required': ['remaining_source_terms', 'field_updates', 'source_version'],
    }


def rent_support_record(value: PolicyAnalysisInput, candidate: dict[str, Any]) -> dict[str, Any]:
    """An unapproved catalogue candidate; source/field review remains explicit."""
    doc = value.documents[0]
    target = next(p for p in value.policies if p.id == value.policy_id)
    fields = candidate['source_fields']
    ranges = re.fullmatch(r'\s*(20\d{6})\s*[~～∼]\s*(20\d{6})\s*', fields.get('aplyYmd', ''))
    if ranges is None:
        raise ValueError('rent_application_dates_unknown')
    starts, ends = (date.fromisoformat(ranges[i]) for i in (1, 2))
    source = {'url': doc.source_url, 'document_version': doc.document_version,
              'checked_at': doc.collected_at.isoformat()}
    citations = [{'quote': e['text'], 'location': f"chars:{e['start']}-{e['end']}",
                  'source': source.copy()} for e in candidate['evidence']]
    # All residual source text remains attached to the explicit exclusion check.
    for field in candidate['body_fields']:
        for start in range(field['start'], field['end'], 2400):
            end = min(field['end'], start + 2400)
            citations.append({'quote': doc.text[start:end], 'location': f'chars:{start}-{end}',
                              'source': source.copy()})
    conditions = [dict(r) for r in candidate['conditions']]
    codes = fields.get('zipCd', '').split(',')
    if not codes or any(re.fullmatch(r'\d{5}', c.strip()) is None for c in codes):
        raise ValueError('rent_source_region_unknown')
    conditions.append({'condition_id': 'residence', 'kind': 'selection',
                       'field': 'region_code', 'values': [c.strip() for c in codes]})
    rule = candidate['rule']
    return {
        'benefit': {
            'id': value.policy_id, 'domain': 'youth', 'title': target.name,
            'summary': '실제 납부 월세 한도와 주거급여 월차임분 차감을 적용한 월세 지원',
            'provider': fields.get('rgtrInstCdNm') or fields.get('sprvsnInstCdNm') or '공식 제공 기관',
            'categories': list(target.categories),
            'application': {'status': 'upcoming', 'starts_on': starts.isoformat(),
                            'ends_on': ends.isoformat(), 'url': fields.get('aplyUrlAddr') or None},
            'eligibility_conditions': [
                {'id': r['condition_id'], 'description': (
                    '가구별 소득·재산, 원가구 예외, 청약통장·독립·잔여기간과 원문의 기타 제외조건 확인'
                    if r['kind'] == 'rent_support' else r['condition_id']),
                 'evidence': citations} for r in conditions],
            'source': source, 'domain_data': {},
            'benefit_amount': {'type': 'monthly', 'amount_won': rule['monthly_cap_won'],
                               'period_months': rule['maximum_months'],
                               'max_total_won': rule['monthly_cap_won'] * rule['maximum_months'],
                               'pay_timing': 'monthly_from_start', 'amount_status': 'verified',
                               'amount_text': '월 최대 지원 상한. 실제 월세·주거급여 월차임분·잔여기간에 따라 계산'},
            'reviewed': False, 'conflicts': [], 'lifetime_once': None, 'virtual_data': False,
        },
        'conditions': conditions, 'payments': None, 'payment_evidence': citations,
        'unresolved_combination_rules': True, 'source_sha256': doc.content_sha256,
        'collection_sources': [asdict(s) for s in value.collection_sources],
    }

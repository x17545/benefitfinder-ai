"""Replay a retained actual policy with synthetic applicant facts, without DB/model writes."""

import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from benefitfinder.api.schemas.recommendation import UserProfile
from benefitfinder.application.decider import DocumentExtraction, LocatedSpan
from benefitfinder.application.published_recommendations import PublishedRecommendations, map_published_response
from benefitfinder.domains.youth.published_eligibility import evaluate_condition
from benefitfinder.domains.youth.rent_support import assess_rent_support, rent_support_payments
from benefitfinder.infrastructure.policy_analysis_inputs import load_policy_analysis_input
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, validate_source_text
from benefitfinder.ingestion.decider_normalization import compile_policy
from benefitfinder.ingestion.decider_source_fields import prepare_source_fields
from benefitfinder.ingestion.rent_support import rent_support_candidate, rent_support_record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--policy-id', required=True)
    parser.add_argument('--as-of', type=date.fromisoformat, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model-comparison', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    values = (load_policy_analysis_input(args.manifest.parent / p) for p in manifest['inputs'])
    value = next(v for v in values if v.policy_id == args.policy_id)
    doc = value.documents[0]
    raw = ()
    if args.model_comparison:
        comparison = json.loads(args.model_comparison.read_text())
        previous = comparison['structured_first']['outputs'][value.policy_id][0]
        raw = tuple(LocatedSpan(**s) for s in previous['raw_spans'])
    plan = prepare_source_fields(doc.text)
    extraction = DocumentExtraction(doc, (*plan.spans, *raw), {'analysis_mode': 'structured_first'}, raw,
        {'structured': plan.structured, 'body_fields': [asdict(f) for f in plan.body]})
    compiled = compile_policy(value, (extraction,))
    candidate = compiled.normalized.get('executable_rent_support_candidate')
    assert candidate is not None, compiled.reasons
    assert candidate['rule'] == rent_support_candidate(value, (extraction,))['rule']
    payload = rent_support_record(value, candidate)
    unapproved = CatalogRecord.model_validate(payload)
    validate_source_text(unapproved, doc.text)
    assert not unapproved.benefit.reviewed and unapproved.payments is None
    # Explicit local review assumption to exercise the published execution path.
    # This does not create an approval, write to a DB, or publish the candidate.
    payload['benefit']['reviewed'] = True
    payload['unresolved_combination_rules'] = False
    record = CatalogRecord.model_validate(payload)
    policy = record.published(datetime.now(timezone.utc))
    repository = SimpleNamespace(list_published=lambda: (policy,), get_published=lambda key: policy if key == policy.policy_id else None)
    service = PublishedRecommendations(repository, evaluate_condition, today=lambda: args.as_of,
                                       resolve_payments=rent_support_payments)
    r = candidate['rule']
    first = f'{args.as_of.year + (args.as_of.month == 12)}-{args.as_of.month % 12 + 1:02d}'
    facts = dict(youth_income_ratio_bps=r['youth_income_max_bps'], youth_assets_won=r['youth_asset_max_won'],
                 original_income_ratio_bps=r['original_income_max_bps'], original_assets_won=r['original_asset_max_won'],
                 original_household_exempt=False, subscription_account=True, independent_from_parents=True,
                 other_exclusions_clear=True, monthly_rent_won=300000, housing_benefit_rent_won=100000,
                 requested_months=r['maximum_months'], prior_supported_months=0, first_payment_month=first)
    base = {'age': r['parent_exempt_min_age'] - 1, 'region_code': candidate['source_fields']['zipCd'],
            'domain_data': {'marital': 'single', 'housing': {'home_ownership': 'homeless'},
                            'contract': {'type': 'monthly_rent'}, 'cash_facts': {'rent_support': facts}}}
    checks = []

    def run(name, *, changes=None, age=None, marital=None, expected=None, monthly=None):
        source = deepcopy(base)
        source['domain_data']['cash_facts']['rent_support'].update(changes or {})
        if age is not None:
            source['age'] = age
        if marital is not None:
            source['domain_data']['marital'] = marital
        profile = UserProfile.model_validate(source)
        statuses = assess_rent_support(profile, r)
        result = service(SimpleNamespace(domain='youth', profile=profile))
        payments = result['catalog']['payments'].get(value.policy_id)
        entry = {'case': name, 'condition_statuses': statuses,
                 'eligibility': result['eligibility'][value.policy_id]['status'],
                 'payments': payments, 'amounts': {p: result['results'][p]['pv_off']['amounts'].get(value.policy_id) for p in ('1y', '2y', 'all')}}
        if expected:
            for key, status in expected.items():
                assert statuses[key] == status, (name, key, statuses)
        if monthly is not None:
            assert payments and all(a == monthly for _, a in payments), entry
        checks.append(entry)
        return result

    for field, condition, maximum in (
        ('youth_income_ratio_bps', 'youth_income', r['youth_income_max_bps']),
        ('original_income_ratio_bps', 'original_income', r['original_income_max_bps']),
        ('youth_assets_won', 'youth_assets', r['youth_asset_max_won']),
        ('original_assets_won', 'original_assets', r['original_asset_max_won']),
    ):
        for difference, expected in ((-1, 'met'), (0, 'met'), (1, 'not_met')):
            run(f'{field}:{maximum + difference}', changes={field: maximum + difference}, expected={condition: expected})
    run('age_before_exemption', changes={'original_income_ratio_bps': 99999, 'original_assets_won': 999000000},
        expected={'original_income': 'not_met', 'original_assets': 'not_met'})
    run('age_at_exemption_parent_facts_missing', age=r['parent_exempt_min_age'],
        changes={'original_income_ratio_bps': None, 'original_assets_won': None},
        expected={'original_income': 'met', 'original_assets': 'met'})
    run('married_under_exemption_age', marital='married',
        changes={'original_income_ratio_bps': None, 'original_assets_won': None},
        expected={'original_income': 'met', 'original_assets': 'met'})
    run('recognized_other_exemption', changes={'original_income_ratio_bps': 99999, 'original_household_exempt': True}, expected={'original_income': 'met'})
    run('unknown_exemption', changes={'original_income_ratio_bps': 99999, 'original_household_exempt': None}, expected={'original_income': 'needs_review'})
    run('exemption_never_skips_youth_assets', age=r['parent_exempt_min_age'], changes={'youth_assets_won': r['youth_asset_max_won'] + 1}, expected={'youth_assets': 'not_met'})
    for benefit in (0, r['monthly_cap_won'] - 1, r['monthly_cap_won'], r['monthly_cap_won'] + 1):
        run(f'deduction:{benefit}', changes={'housing_benefit_rent_won': benefit}, monthly=max(0, r['monthly_cap_won'] - benefit))
    run('rent_below_cap', changes={'monthly_rent_won': 150000, 'housing_benefit_rent_won': 100000}, monthly=50000)
    for field, condition in (('housing_benefit_rent_won', 'housing_benefit_deduction'),
                             ('youth_income_ratio_bps', 'youth_income'), ('other_exclusions_clear', 'other_exclusions_clear')):
        result = run('missing:' + field, changes={field: None}, expected={condition: 'needs_review'})
        assert value.policy_id not in result['catalog']['payments']
    result = run('remaining_months_exceeded', changes={'prior_supported_months': 1}, expected={'duration': 'not_met'})
    assert value.policy_id not in result['catalog']['payments']
    result = run('payment_period_exceeded', changes={'first_payment_month': r['support_ends_month']}, expected={'payment_period': 'not_met'})
    assert value.policy_id not in result['catalog']['payments']
    example = run('all_at_limit_deduct_100000', monthly=r['monthly_cap_won'] - 100000)
    actual_today = datetime.now(timezone.utc).astimezone().date()
    current_service = PublishedRecommendations(repository, evaluate_condition, today=lambda: actual_today,
                                               resolve_payments=rent_support_payments)
    current = current_service(SimpleNamespace(domain='youth', profile=UserProfile.model_validate(base)))
    assert value.policy_id not in current['catalog']['payments']
    report = {'policy_id': value.policy_id, 'source_sha256': doc.content_sha256,
              'input_sha256': value.fingerprint(), 'as_of': str(args.as_of),
              'actual_policy_synthetic_profiles': True, 'local_review_assumption': True,
              'publication_performed': False, 'new_model_calls': 0, 'rule': r,
              'checks': checks, 'case_count': len(checks), 'all_passed': True,
              'current_application': current['catalog']['details'][value.policy_id]['application'],
              'candidate_reviewed': unapproved.benefit.reviewed,
              'example_response': map_published_response(example, None)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('policy_id', 'case_count', 'all_passed', 'publication_performed', 'new_model_calls')}, ensure_ascii=False))


if __name__ == '__main__':
    main()

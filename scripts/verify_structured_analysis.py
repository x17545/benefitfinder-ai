"""Read-only, offline routing audit and optional local-model policy regression.

Actual inputs/gold/results belong in private Git-ignored data/. Routing preservation
and model semantic recall are deliberately separate measurements. No DB writes,
publication, network API calls, or model downloads are performed by this script.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

from benefitfinder.infrastructure.ai_catalog_review import project_youthcenter_source

from benefitfinder.ai.decider_extraction import analyze_batch, body_projection, text_windows
from benefitfinder.infrastructure.decider_client import CheckpointDecider, LocalDecider, private_json
from benefitfinder.infrastructure.policy_analysis_inputs import load_policy_analysis_input
from benefitfinder.ingestion.decider_source_fields import prepare_source_fields
from benefitfinder.ingestion.decider_normalization import compile_policy


def audit(values, window_chars=900):
    totals = Counter(policies=len(values), documents=0, routed_fields=0,
                     missing_fields=0, legacy_requests=0, planned_requests=0,
                     legacy_question_evaluations=0, planned_question_evaluations=0,
                     legacy_state_characters_lower_bound=0, planned_state_characters=0)
    issues = Counter()
    for value in values:
        plans = [prepare_source_fields(document.text) for document in value.documents]
        projections = [body_projection(plan)[0] for plan in plans]
        policy_windows = [text_windows(text, window_chars) for text in projections]
        executable = (not any(plan.issues for plan in plans)
                      and sum(map(len, projections)) <= 40000
                      and sum(map(len, policy_windows)) <= 32)
        totals['policies_held_before_inference'] += not executable
        for document, plan, projected, windows in zip(value.documents, plans, projections, policy_windows, strict=True):
            old_windows = text_windows(document.text, window_chars)
            totals.update(documents=1, routed_fields=len(plan.structured) + len(plan.body),
                          legacy_requests=len(old_windows) + 1, planned_requests=len(windows) if executable else 0,
                          legacy_question_evaluations=7 * (len(old_windows) + 1),
                          planned_question_evaluations=3 * len(windows) if executable else 0,
                          legacy_state_characters_lower_bound=len(document.text) + sum(
                              end - start for start, end in old_windows),
                          planned_state_characters=sum(end - start for start, end in windows) if executable else 0)
            # Independently reconstruct every non-whitespace source character in
            # field values; field names/separators are structural, not conditions.
            import re
            source = document.text
            headers = list(re.finditer(r'(?m)^\[([A-Za-z][A-Za-z0-9]*)\]\r?\n', source))
            routed = {(item['start'], item['end']) for item in plan.structured.values()}
            routed.update((field.start, field.end) for field in plan.body)
            for index, header in enumerate(headers):
                end = headers[index + 1].start() if index + 1 < len(headers) else len(source)
                raw = source[header.end():end]
                body = raw.strip()
                start = header.end() + len(raw) - len(raw.lstrip())
                if body and (start, start + len(body)) not in routed:
                    totals['missing_fields'] += 1
            for field in plan.body:
                if source[field.start:field.end] != field.text:
                    totals['missing_fields'] += 1
            issues.update(plan.issues)
    return {'totals': dict(totals), 'preparation_issue_counts': dict(issues),
            'routing_preserved': totals['missing_fields'] == 0,
            'notice': 'Planned logical requests/questions and source characters, not billed tokens or HTTP calls. Legacy followup text is a lower bound. Semantic completeness requires separate evidence checks.'}


def audit_raw_conditions(records):
    """Audit before the historical text projection, not only after it.

    These are explicit condition/payment/period fields in the official API. An
    omitted field cannot be excused by passing the projected-text routing check.
    """
    condition_fields = {
        'plcyMajorCd', 'bizPrdSeCd', 'jobCd', 'schoolCd', 'zipCd', 'mrgSttsCd',
        'sbizCd', 'earnCndSeCd', 'earnMinAmt', 'earnMaxAmt', 'earnEtcCn',
        'sprtTrgtAgeLmtYn', 'sprtTrgtMinAge', 'sprtTrgtMaxAge', 'plcySprtCn',
        'addAplyQlfcCndCn', 'ptcpPrpTrgtCn', 'etcMttrCn', 'aplyYmd', 'aplyPrdSeCd',
        'bizPrdBgngYmd', 'bizPrdEndYmd', 'bizPrdEtcCn', 'sprtSclCnt',
        'sprtSclLmtYn', 'sprtArvlSeqYn', 'plcyPvsnMthdCd',
    }
    missing = Counter()
    checked = 0
    for fields in records:
        prepared = prepare_source_fields(project_youthcenter_source(fields))
        preserved = {name: field['raw'] for name, field in prepared.structured.items()}
        preserved.update((field.name, field.text) for field in prepared.body)
        for name in condition_fields:
            raw = fields.get(name)
            if raw is not None and str(raw).strip():
                checked += 1
                if preserved.get(name) != str(raw).strip():
                    missing[name] += 1
    return {'checked_fields': checked, 'missing_field_counts': dict(missing),
            'all_preserved': not missing}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--records', type=Path, help='Raw API fields for audit only, never MVP admission')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--window-chars', type=int, default=900)
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--gold', type=Path)
    parser.add_argument('--model-limit', type=int, default=3)
    args = parser.parse_args()
    if not args.manifest and not args.records:
        parser.error('--manifest or --records is required')
    values = ()
    if args.manifest:
        manifest = json.loads(args.manifest.read_text())
        values = tuple(load_policy_analysis_input(args.manifest.parent / path) for path in manifest['inputs'])
    audit_values = values
    if args.records:
        records = json.loads(args.records.read_text())['records']
        audit_values = tuple(SimpleNamespace(documents=(SimpleNamespace(
            text=project_youthcenter_source(fields)),)) for fields in records)
    report = audit(audit_values, args.window_chars)
    if args.records:
        report['raw_condition_fields'] = audit_raw_conditions(records)
    private_json(args.output, report)
    if args.checkpoint:
        if not args.manifest or not args.gold or not 1 <= args.model_limit <= 10:
            parser.error('--checkpoint requires --gold and --model-limit in 1..10')
        gold = json.loads(args.gold.read_text())['cases']
        wanted = {case['policy_id'] for case in gold}
        selected = tuple(value for value in values if value.policy_id in wanted)
        if len(selected) != len(wanted) or len(selected) > args.model_limit:
            parser.error('gold policies must all exist and fit --model-limit')
        raw = LocalDecider(args.checkpoint, max_length=3072, batch_tokens=2048)
        client = CheckpointDecider(raw, args.output.parent / 'responses')
        outputs, failures = analyze_batch(selected, client, batch_size=1,
            window_chars=args.window_chars, prepare_source=prepare_source_fields)
        compilations = {value.policy_id: compile_policy(value, outputs[value.policy_id])
                        for value in selected if value.policy_id in outputs}
        checks = []
        for case in gold:
            value = next(value for value in selected if value.policy_id == case['policy_id'])
            document = next(doc for doc in value.documents if doc.id == case['document_id'])
            start = document.text.index(case['text'])
            expected = set(range(start, start + len(case['text'])))
            extraction = next((item for item in outputs.get(value.policy_id, ())
                               if item.document.id == document.id), None)
            actual = {i for span in (extraction.raw_spans or ()) if span.kind == case['kind']
                      for i in range(span.start, span.end)} if extraction else set()
            contextualized = {i for span in extraction.spans if span.kind == case['kind']
                              for i in range(span.start, span.end)} if extraction else set()
            compilation = compilations.get(value.policy_id)
            retained = {i for field in (compilation.normalized.get('body_fields', []) if compilation else [])
                        for i in range(field['start'], field['end'])}
            checks.append({'policy_id': value.policy_id, 'kind': case['kind'],
                           'expected_characters': len(expected),
                           'matched_characters': len(expected & actual),
                           'retained_for_review': expected <= retained,
                           'model_full_clause_recall': expected <= actual,
                           'clause_preserved_in_context': expected <= contextualized})
        report.update(model=client.identity, failures=failures, reference_checks=checks,
                      reference_clauses_preserved=all(check['retained_for_review'] for check in checks),
                      compilations={key: {'reasons': list(item.reasons),
                          'calculation_ready': item.payload is not None,
                          'normalized': item.normalized} for key, item in compilations.items()},
                      model_measurements=raw.metrics,
                      extractions={key: [{'document_id': item.document.id,
                          'spans': [asdict(span) for span in item.spans],
                          'raw_spans': [asdict(span) for span in (item.raw_spans or ())],
                          'preparation': item.preparation} for item in items]
                          for key, items in outputs.items()})
        private_json(args.output, report)
    print(json.dumps({key: value for key, value in report.items()
                      if key not in {'extractions', 'model_measurements', 'reference_checks', 'compilations'}}, ensure_ascii=False))
    if (not report['routing_preserved'] or report.get('reference_clauses_preserved') is False
            or report.get('raw_condition_fields', {}).get('all_preserved') is False):
        return 1
    if report.get('failures') or any(not check['model_full_clause_recall']
                                   for check in report.get('reference_checks', [])):
        print('model_semantic_gaps: complete source is retained for review; not verified executable rules')
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

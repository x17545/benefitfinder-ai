"""Read-only bulk selection by official youthcenter categories, retaining supplements."""

from collections import Counter

import psycopg
from psycopg.rows import dict_row

from benefitfinder.application.policy_analysis import (
    AnalysisPolicy, AnalysisSource, PolicyAnalysisInput,
)
from benefitfinder.application.policy_rules import SourceDocument
from benefitfinder.infrastructure.ai_catalog_review import project_youthcenter_source
from benefitfinder.infrastructure.policy_analysis_inputs import analysis_input_from_payload


def load_decider_corpus(database_url: str) -> tuple[tuple[PolicyAnalysisInput, ...], dict]:
    """Never infer mixed/outside categories or discard previously linked official text."""
    counts = Counter(targets=0, selected=0, held=0, out_of_scope=0)
    reasons = Counter()
    values = []
    with psycopg.connect(database_url, connect_timeout=2, row_factory=dict_row,
        options='-c default_transaction_read_only=on -c statement_timeout=8000 -c timezone=UTC') as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        rows = c.execute('''SELECT p.external_id,p.fields,p.material_sha256,
            (SELECT max(r.completed_at) FROM public.collected_policy_changes h
             JOIN public.collected_policy_runs r USING(source,run_id)
             WHERE h.source=p.source AND h.external_id=p.external_id
               AND h.current_sha256=p.material_sha256) AS accepted_at
            FROM public.collected_policies p
            WHERE source='youthcenter' AND missing_detected_at IS NULL
            ORDER BY external_id''').fetchmany(5001)
        if len(rows) > 5000:
            raise ValueError('decider_corpus_size_exceeded')
        previous = {row['policy_id']: row['input_payload'] for row in c.execute('''
            SELECT DISTINCT ON (policy_id) policy_id,input_payload FROM public.f12_analysis_tasks
            ORDER BY policy_id,task_id DESC''').fetchall()}
        for row in rows:
            fields = row['fields']
            labels = set(str(fields.get('lclsfNm', '')).split(','))
            if not labels or not labels <= {'일자리', '주거'}:
                if labels & {'일자리', '주거'}:
                    counts['targets'] += 1
                    counts['held'] += 1
                    reasons['mixed_official_category'] += 1
                else:
                    counts['out_of_scope'] += 1
                continue
            counts['targets'] += 1
            if '일자리' in labels:
                sublabels = set(str(fields.get('mclsfNm', '')).split(','))
                if not sublabels <= {'취업', '재직자', '권익보호'}:
                    counts['held'] += 1
                    reasons['employment_subcategory_needs_scope_review'] += 1
                    continue
            external_id = row['external_id']
            policy_id = 'youth:youthcenter:' + external_id
            try:
                if not external_id.isascii() or not external_id.isdigit() or row['accepted_at'] is None:
                    raise ValueError
                old = previous.get(policy_id)
                if old is not None:
                    old = analysis_input_from_payload(old)
                    # Supplements must go through the existing F12 preparation plan
                    # on change; API-only bulk discovery never removes them.
                    if len(old.documents) > 1 or len(old.collection_sources) > 1:
                        for binding in old.collection_sources:
                            current = c.execute('''SELECT material_sha256 FROM public.collected_policies
                                WHERE source=%s AND external_id=%s AND missing_detected_at IS NULL''',
                                (binding.source, binding.external_id)).fetchone()
                            if current is None or current['material_sha256'] != binding.material_sha256:
                                raise ValueError
                        values.append(old)
                        counts['selected'] += 1
                        continue
                name = fields['plcyNm']
                if fields.get('plcyNo') != external_id:
                    raise ValueError
                document_id = old.documents[0].id if old is not None else 'youthcenter:' + external_id
                document = SourceDocument(document_id, policy_id,
                    'https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/' + external_id,
                    'sha256:' + row['material_sha256'], row['accepted_at'],
                    project_youthcenter_source(fields), official=True)
                categories = tuple(key for label, key in (('일자리', 'employment'), ('주거', 'housing'))
                                   if label in labels)
                values.append(PolicyAnalysisInput(policy_id, (document,),
                    (AnalysisPolicy(policy_id, name, categories),),
                    (AnalysisSource('youthcenter', external_id, row['material_sha256']),)))
                counts['selected'] += 1
            except (ValueError, KeyError, TypeError):
                counts['held'] += 1
                reasons['source_or_supplement_preparation_required'] += 1
    return tuple(values), {'counts': dict(counts), 'reason_counts': dict(reasons)}

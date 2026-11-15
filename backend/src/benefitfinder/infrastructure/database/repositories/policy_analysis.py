"""Short PostgreSQL leases around offline F12 work, never around LLM calls."""

from datetime import datetime, timezone
import json
import re
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from benefitfinder.application.policy_analysis import (
    AnalysisClaim, PolicyAnalysisInput, PolicyAnalysisStoreError,
)
from benefitfinder.infrastructure.policy_analysis_inputs import analysis_input_from_payload

_OPTIONS = '-c statement_timeout=8000 -c lock_timeout=500 -c timezone=UTC'
_CHANGED = """EXISTS (
    SELECT 1 FROM public.f12_analysis_sources s
    LEFT JOIN public.collected_policies p ON p.source=s.source AND p.external_id=s.external_id
    WHERE s.task_id=t.task_id AND (p.external_id IS NULL OR p.missing_detected_at IS NOT NULL
                                  OR p.material_sha256<>s.material_sha256))"""


def _store_error() -> PolicyAnalysisStoreError:
    return PolicyAnalysisStoreError('f12_analysis_storage_unavailable')


class PostgresPolicyAnalysisTasks:
    def __init__(self, database_url: str) -> None:
        try:
            if not database_url.strip() or not conninfo_to_dict(database_url).get('dbname'):
                raise ValueError
        except (psycopg.Error, ValueError, AttributeError):
            raise ValueError('invalid_f12_analysis_database_url') from None
        self._url = database_url

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._url, connect_timeout=2, options=_OPTIONS,
                               application_name='benefitfinder-f12-analysis', row_factory=dict_row)

    @staticmethod
    def _lock_sources(connection: psycopg.Connection, value: PolicyAnalysisInput) -> None:
        for source in sorted({source.source for source in value.collection_sources}):
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                               ('benefitfinder:collected_policy_generations:' + source,))

    def register(self, value: PolicyAnalysisInput, *, origin_recheck_id: int | None = None) -> dict:
        if origin_recheck_id is not None and (type(origin_recheck_id) is not int or origin_recheck_id < 1):
            raise ValueError('invalid_f12_recheck_id')
        digest = value.fingerprint()
        try:
            with self._connect() as connection:
                self._lock_sources(connection, value)
                connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                                   ('benefitfinder:f12_policy:' + value.policy_id,))
                now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
                changed_at = datetime.min.replace(tzinfo=timezone.utc)
                for source in value.collection_sources:
                    row = connection.execute("""SELECT p.material_sha256,p.missing_detected_at,
                        (SELECT max(r.completed_at) FROM public.collected_policy_changes c
                         JOIN public.collected_policy_runs r ON r.source=c.source AND r.run_id=c.run_id
                         WHERE c.source=p.source AND c.external_id=p.external_id
                           AND c.current_sha256=p.material_sha256) AS changed_at
                        FROM public.collected_policies p WHERE p.source=%s AND p.external_id=%s
                        """, (source.source, source.external_id)).fetchone()
                    if (row is None or row['missing_detected_at'] is not None
                        or row['material_sha256'] != source.material_sha256 or row['changed_at'] is None):
                        raise PolicyAnalysisStoreError('f12_analysis_source_stale')
                    changed_at = max(changed_at, row['changed_at'])
                if any(not changed_at <= doc.collected_at <= now for doc in value.documents):
                    raise PolicyAnalysisStoreError('f12_documents_require_refresh')
                if origin_recheck_id is not None:
                    origin = connection.execute('SELECT policy_id,source,external_id FROM public.policy_recheck_queue WHERE task_id=%s', (origin_recheck_id,)).fetchone()
                    if origin is None or origin['policy_id'] != value.policy_id or (
                        origin['source'] is not None and (origin['source'], origin['external_id']) not in
                        {(item.source, item.external_id) for item in value.collection_sources}
                    ):
                        raise PolicyAnalysisStoreError('f12_recheck_mismatch')
                existing = connection.execute('SELECT task_id,status FROM public.f12_analysis_tasks WHERE policy_id=%s AND input_sha256=%s', (value.policy_id, digest)).fetchone()
                if existing:
                    return dict(existing, replayed=True)
                # A new official input supersedes old drafts, including in-flight work.
                connection.execute("""UPDATE public.f12_analysis_tasks SET status='stale',
                    claim_token=NULL,lease_until=NULL,last_error='f12_source_changed',
                    updated_at=clock_timestamp() WHERE policy_id=%s AND status<>'stale'""", (value.policy_id,))
                task = connection.execute("""INSERT INTO public.f12_analysis_tasks
                    (policy_id,input_sha256,input_payload,origin_recheck_id)
                    VALUES (%s,%s,%s,%s) RETURNING task_id,status""",
                    (value.policy_id, digest, Jsonb(value.payload()), origin_recheck_id)).fetchone()
                for source in value.collection_sources:
                    connection.execute('INSERT INTO public.f12_analysis_sources (task_id,source,external_id,material_sha256) VALUES (%s,%s,%s,%s)',
                        (task['task_id'], source.source, source.external_id, source.material_sha256))
                return dict(task, replayed=False)
        except PolicyAnalysisStoreError:
            raise
        except (psycopg.Error, OSError, ValueError, TypeError):
            raise _store_error() from None

    def claim(self) -> AnalysisClaim | None:
        invalid = False
        result = None
        try:
            with self._connect() as connection:
                connection.execute("""UPDATE public.f12_analysis_tasks SET status='failed',
                    claim_token=NULL,lease_until=NULL,last_error='f12_lease_expired',updated_at=clock_timestamp()
                    WHERE status='running' AND attempts=3 AND lease_until<=clock_timestamp()""")
                row = connection.execute("""SELECT t.* FROM public.f12_analysis_tasks t
                    WHERE ((t.status='pending' AND t.next_attempt_at<=clock_timestamp())
                           OR (t.status='running' AND t.lease_until<=clock_timestamp()))
                      AND t.attempts<3 AND NOT """ + _CHANGED + """
                    ORDER BY t.task_id LIMIT 1 FOR UPDATE OF t SKIP LOCKED""").fetchone()
                if row is None:
                    return None
                try:
                    value = analysis_input_from_payload(row['input_payload'])
                    if value.fingerprint() != row['input_sha256']:
                        raise ValueError
                except ValueError:
                    invalid = True
                    connection.execute("""UPDATE public.f12_analysis_tasks SET status='failed',
                        claim_token=NULL,lease_until=NULL,last_error='f12_invalid_input',updated_at=clock_timestamp()
                        WHERE task_id=%s""", (row['task_id'],))
                if not invalid:
                    token = uuid4().hex
                    state = connection.execute("""UPDATE public.f12_analysis_tasks SET status='running',
                        claim_token=%s,lease_until=clock_timestamp()+interval '10 minutes',attempts=attempts+1,
                        updated_at=clock_timestamp(),last_error=NULL WHERE task_id=%s
                        RETURNING attempts,lease_until""", (token, row['task_id'])).fetchone()
                    result = AnalysisClaim(row['task_id'], token, state['attempts'], value,
                                           state['lease_until'], row['origin_recheck_id'])
            if invalid:
                raise PolicyAnalysisStoreError('f12_invalid_input')
            return result
        except PolicyAnalysisStoreError:
            raise
        except (psycopg.Error, OSError, ValueError, TypeError):
            raise _store_error() from None

    def is_current(self, claim: AnalysisClaim) -> bool:
        try:
            with self._connect() as connection:
                connection.execute('SET TRANSACTION READ ONLY')
                return connection.execute("""SELECT 1 FROM public.f12_analysis_tasks t
                    WHERE t.task_id=%s AND t.status='running' AND t.claim_token=%s
                      AND t.lease_until>clock_timestamp() AND NOT """ + _CHANGED,
                    (claim.task_id, claim.token)).fetchone() is not None
        except (psycopg.Error, OSError, ValueError):
            raise _store_error() from None

    def complete(self, claim: AnalysisClaim, *, output_ref: str, output_sha256: str,
                 summary: dict[str, object]) -> str:
        if (output_ref != f'f12-analysis/{claim.task_id}/{claim.token}'
            or re.fullmatch(r'[0-9a-f]{64}', output_sha256) is None):
            raise ValueError('invalid_f12_analysis_output')
        try:
            if not isinstance(summary, dict):
                raise ValueError
            encoded = json.dumps(summary, allow_nan=False)
            if len(encoded.encode()) > 65_536:
                raise ValueError
            with self._connect() as connection:
                self._lock_sources(connection, claim.input)
                row = connection.execute('SELECT *,lease_until>clock_timestamp() AS live FROM public.f12_analysis_tasks WHERE task_id=%s FOR UPDATE', (claim.task_id,)).fetchone()
                if row is None:
                    raise PolicyAnalysisStoreError('f12_analysis_claim_lost')
                if row['status'] == 'stale':
                    return 'stale'
                if row['status'] == 'needs_review' and row['output_ref'] == output_ref and row['output_sha256'] == output_sha256:
                    return 'needs_review'
                if row['status'] != 'running' or row['claim_token'] != claim.token or not row['live']:
                    raise PolicyAnalysisStoreError('f12_analysis_claim_lost')
                changed = connection.execute('SELECT 1 FROM public.f12_analysis_tasks t WHERE t.task_id=%s AND ' + _CHANGED, (claim.task_id,)).fetchone()
                status = 'stale' if changed else 'needs_review'
                connection.execute("""UPDATE public.f12_analysis_tasks SET status=%s,
                    output_ref=%s,output_sha256=%s,result_summary=%s,claim_token=NULL,lease_until=NULL,
                    updated_at=clock_timestamp(),last_error=%s WHERE task_id=%s""",
                    (status, output_ref, output_sha256, Jsonb(summary), 'f12_source_changed' if changed else None, claim.task_id))
                return status
        except PolicyAnalysisStoreError:
            raise
        except (psycopg.Error, OSError, ValueError, TypeError):
            raise _store_error() from None

    def fail(self, claim: AnalysisClaim, code: str = 'f12_analysis_failed') -> str:
        if code not in {'f12_analysis_failed', 'f12_analysis_timeout', 'f12_artifact_failed'}:
            code = 'f12_analysis_failed'
        try:
            with self._connect() as connection:
                row = connection.execute("""UPDATE public.f12_analysis_tasks SET
                    status=CASE WHEN attempts>=3 THEN 'failed' ELSE 'pending' END,
                    next_attempt_at=clock_timestamp()+interval '1 minute',claim_token=NULL,
                    lease_until=NULL,last_error=%s,updated_at=clock_timestamp()
                    WHERE task_id=%s AND status='running' AND claim_token=%s RETURNING status""",
                    (code, claim.task_id, claim.token)).fetchone()
                if row:
                    return row['status']
                row = connection.execute('SELECT status FROM public.f12_analysis_tasks WHERE task_id=%s', (claim.task_id,)).fetchone()
                return 'stale' if row and row['status'] == 'stale' else 'claim_lost'
        except (psycopg.Error, OSError, ValueError):
            raise _store_error() from None

    def status(self) -> dict[str, object]:
        try:
            with self._connect() as connection:
                connection.execute('SET TRANSACTION READ ONLY')
                counts = connection.execute('SELECT status,count(*) AS count FROM public.f12_analysis_tasks GROUP BY status').fetchall()
                awaiting = connection.execute("""SELECT count(*) AS count FROM public.collected_policies p
                    WHERE p.missing_detected_at IS NULL AND NOT EXISTS (
                        SELECT 1 FROM public.f12_analysis_sources s JOIN public.f12_analysis_tasks t
                          ON t.task_id=s.task_id WHERE s.source=p.source AND s.external_id=p.external_id
                          AND t.status IN ('pending','running','needs_review')
                          AND s.material_sha256=p.material_sha256)""").fetchone()['count']
                return {'tasks': {row['status']: row['count'] for row in counts},
                        'source_records_awaiting_scope_or_documents': awaiting}
        except (psycopg.Error, OSError, ValueError):
            raise _store_error() from None

    def get_task(self, task_id: int) -> dict[str, object] | None:
        if type(task_id) is not int or task_id < 1:
            raise ValueError('invalid_f12_task_id')
        try:
            with self._connect() as connection:
                connection.execute('SET TRANSACTION READ ONLY')
                row = connection.execute('''SELECT t.task_id,t.policy_id,t.status,t.attempts,
                    t.origin_recheck_id,t.input_sha256,t.output_ref,t.output_sha256,
                    t.last_error,t.result_summary, NOT ''' + _CHANGED + ''' AS source_current
                    FROM public.f12_analysis_tasks t WHERE t.task_id=%s''', (task_id,)).fetchone()
                return dict(row) if row else None
        except (psycopg.Error, OSError, ValueError):
            raise _store_error() from None

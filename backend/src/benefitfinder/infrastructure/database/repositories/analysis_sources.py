"""Read one bounded snapshot of mapped official API material for F12."""

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from benefitfinder.application.prepare_policy_analysis import PreparationSource
from benefitfinder.application.collected_policies import COLLECTED_POLICY_SOURCES


class PostgresAnalysisSources:
    def __init__(self, database_url: str, *, source: str = 'youthcenter') -> None:
        if source not in COLLECTED_POLICY_SOURCES:
            raise ValueError('invalid_f12_preparation_source')
        self._source = source
        try:
            if not database_url.strip() or not conninfo_to_dict(database_url).get('dbname'):
                raise ValueError
        except (psycopg.Error, ValueError, AttributeError):
            raise ValueError('invalid_f12_preparation_database_url') from None
        self._url = database_url

    def load(self, external_ids: tuple[str, ...]) -> dict[str, PreparationSource]:
        if (not isinstance(external_ids, tuple) or not 1 <= len(external_ids) <= 400
            or any(type(item) is not str or not 1 <= len(item) <= 200 for item in external_ids)
            or len(set(external_ids)) != len(external_ids)):
            raise ValueError('invalid_f12_preparation_source_ids')
        try:
            with psycopg.connect(self._url, connect_timeout=2, row_factory=dict_row,
                application_name='benefitfinder-f12-preparation',
                options='-c default_transaction_read_only=on -c statement_timeout=8000 '
                        '-c lock_timeout=500 -c timezone=UTC') as connection:
                rows = connection.execute('''WITH selected AS MATERIALIZED (
                    SELECT external_id,material_sha256,fields FROM public.collected_policies
                    WHERE source=%s AND missing_detected_at IS NULL AND external_id=ANY(%s)),
                    observed AS (
                    SELECT c.external_id,max(r.completed_at) AS accepted_at
                    FROM public.collected_policy_changes c
                    JOIN public.collected_policy_runs r ON r.source=c.source AND r.run_id=c.run_id
                    JOIN selected p ON p.external_id=c.external_id AND p.material_sha256=c.current_sha256
                    WHERE c.source=%s GROUP BY c.external_id)
                    SELECT p.*,o.accepted_at FROM selected p JOIN observed o USING(external_id)
                    ORDER BY p.external_id''', (self._source, list(external_ids), self._source)).fetchall()
            return {row['external_id']: PreparationSource(**row) for row in rows}
        except (psycopg.Error, OSError, ValueError, TypeError):
            raise ValueError('f12_preparation_sources_unavailable') from None

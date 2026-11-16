"""Read-only source inventory and release identities for source-only imports."""

from collections.abc import Sequence

import psycopg
from psycopg.rows import dict_row


_OPTIONS = '-c default_transaction_read_only=on -c statement_timeout=8000 -c timezone=UTC'


def load_source_inventory(database_url: str) -> dict:
    """Count all collected rows independently from the MVP corpus selection."""
    with psycopg.connect(database_url, connect_timeout=2, row_factory=dict_row,
                         options=_OPTIONS) as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        sources = connection.execute('''SELECT source,count(*) AS total,
            count(*) FILTER (WHERE missing_detected_at IS NULL) AS active
            FROM public.collected_policies GROUP BY source ORDER BY source''').fetchall()
        categories = connection.execute('''SELECT fields->>'lclsfNm' AS category,
            fields->>'mclsfNm' AS subcategory,count(*) AS count
            FROM public.collected_policies
            WHERE source='youthcenter' AND missing_detected_at IS NULL
            GROUP BY fields->>'lclsfNm',fields->>'mclsfNm'
            ORDER BY category,subcategory''').fetchall()
    return {
        'source_counts': {row['source']: {
            'total': row['total'], 'active': row['active'],
            'missing': row['total'] - row['active'],
        } for row in sources},
        'category_counts': categories,
    }


def load_source_release_identities(
    database_url: str, policy_ids: Sequence[str], external_ids: Sequence[str],
) -> tuple[dict, ...]:
    """Use a new DB snapshot each call; include retired history and both aliases."""
    if not policy_ids and not external_ids:
        return ()
    with psycopg.connect(database_url, connect_timeout=2, row_factory=dict_row,
                         options=_OPTIONS) as connection:
        return tuple(connection.execute('''SELECT DISTINCT r.policy_id,r.document_version,
            r.approved_sha256 AS record_sha256,r.publication_status,r.review_type,r.review_status,
            link.source,link.external_id
            FROM public.policy_catalog_releases r
            LEFT JOIN public.policy_catalog_source_links link
              ON link.policy_id=r.policy_id AND link.document_version=r.document_version
            WHERE (r.policy_id=ANY(%s)
              OR (link.source='youthcenter' AND link.external_id=ANY(%s)))
              AND r.publication_status IN ('published','retired')
            ORDER BY r.policy_id,r.document_version''',
            (list(policy_ids), list(external_ids))).fetchall())

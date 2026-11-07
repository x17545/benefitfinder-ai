"""PublishedClauseRepository backed by PostgreSQL, without profile storage or writes."""

from __future__ import annotations

import asyncio

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from benefitfinder.application.policy_explanations import ExplanationRequest, PublishedClause

SEARCH_SQL = """
SELECT c.clause_id, c.policy_id, c.document_version, c.title, c.body,
       c.source_location, c.topics, d.source_url, d.checked_at
FROM public.f13_clauses AS c
JOIN public.f13_policy_documents AS d USING (policy_id, document_version)
WHERE c.policy_id = %s AND c.document_version = %s AND d.category = %s
  AND d.publication_status = 'published'
  AND c.publication_status = 'published' AND c.review_status = 'approved'
  AND c.topics && %s::text[]
ORDER BY (SELECT count(DISTINCT topic) FROM unnest(c.topics) AS t(topic)
          WHERE topic = ANY(%s::text[])) DESC, c.clause_id
LIMIT %s
"""


class PostgresPublishedClauses:
    """Fresh read per policy/version. Connections close before any LLM generation."""

    def __init__(self, database_url: str) -> None:
        try:
            if not database_url.strip() or not conninfo_to_dict(database_url).get("dbname"):
                raise ValueError
        except (psycopg.Error, ValueError):
            raise ValueError("invalid_f13_database_url") from None
        self._database_url = database_url
        self._slots = asyncio.Semaphore(4)

    async def search(
        self, request: ExplanationRequest, *, limit: int
    ) -> tuple[PublishedClause, ...]:
        """Only same-policy/version published rows; never fall back to memory on DB failure."""
        if type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError("invalid_clause_search_limit")
        topics = sorted({key for key, _ in request.conditions} | {"amount", "application"})
        try:
            # Includes queue wait, DNS and connection setup in the existing 5-second budget.
            async with asyncio.timeout(2), self._slots:
                async with await psycopg.AsyncConnection.connect(
                    self._database_url,
                    connect_timeout=2,
                    options="-c default_transaction_read_only=on -c statement_timeout=1000 "
                    "-c lock_timeout=500 -c timezone=UTC",
                    application_name="benefitfinder-f13",
                    row_factory=dict_row,
                ) as connection:
                    cursor = await connection.execute(
                        SEARCH_SQL,
                        (
                            request.policy_id,
                            request.document_version,
                            request.category,
                            topics,
                            topics,
                            limit,
                        ),
                    )
                    rows = await cursor.fetchall()
            return tuple(
                PublishedClause(
                    id=row["clause_id"],
                    policy_id=row["policy_id"],
                    document_version=row["document_version"],
                    title=row["title"],
                    text=row["body"],
                    source_url=row["source_url"],
                    topics=tuple(row["topics"]),
                    published=True,
                    location=row["source_location"],
                    checked_at=row["checked_at"],
                )
                for row in rows
            )
        except (TimeoutError, psycopg.errors.QueryCanceled, psycopg.errors.LockNotAvailable):
            raise TimeoutError("published_clause_lookup_timeout") from None
        except (psycopg.Error, OSError):
            raise RuntimeError("published_clause_lookup_failed") from None

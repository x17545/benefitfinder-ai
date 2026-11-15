"""Atomic PostgreSQL handoff for complete, unreviewed policy collections."""

from __future__ import annotations

from datetime import timezone
import json

import psycopg
from psycopg.conninfo import conninfo_to_dict

from benefitfinder.application.collected_policies import (
    CollectedPolicyBatch,
    CollectedPolicyStoreError,
    CollectionImportResult,
)

_OPTIONS = "-c statement_timeout=8000 -c lock_timeout=500 -c timezone=UTC"
_LOCK_NAMESPACE = "benefitfinder:collected_policy_generations:"
_UNAVAILABLE = "collected_policy_storage_unavailable"
_CONFLICT = "collected_policy_run_conflict"
_REGRESSED = "collected_policy_generation_regressed"


def _incoming_records_json(batch: CollectedPolicyBatch) -> str:
    records = []
    for record in batch.records:
        records.append(
            {
                "source_name": record.source_name,
                "external_id": record.external_id,
                "record_sha256": record.record_sha256,
                "snapshot_ref": record.snapshot_ref,
                "fetched_at": record.fetched_at.astimezone(timezone.utc).isoformat(),
                "fields": record.fields,
            }
        )
    return json.dumps(
        records,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


class PostgresCollectedPolicies:
    """Store one complete source generation and derive its DB change set."""

    def __init__(self, database_url: str) -> None:
        try:
            if not database_url.strip() or not conninfo_to_dict(database_url).get("dbname"):
                raise ValueError
        except (psycopg.Error, ValueError):
            raise ValueError("invalid_collected_policy_database_url") from None
        self._database_url = database_url

    def save(self, batch: CollectedPolicyBatch) -> CollectionImportResult:
        try:
            fingerprint = batch.fingerprint()
            incoming_json = _incoming_records_json(batch)
            with psycopg.connect(
                self._database_url,
                connect_timeout=2,
                options=_OPTIONS,
                application_name="benefitfinder-collected-policy-writer",
            ) as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (_LOCK_NAMESPACE + batch.source_name,),
                )

                existing = connection.execute(
                    """
                    SELECT batch_sha256, new_count, changed_count,
                           unchanged_count, missing_count
                    FROM public.collected_policy_runs
                    WHERE source = %s AND run_id = %s
                    """,
                    (batch.source_name, batch.run_id),
                ).fetchone()
                if existing is not None:
                    if existing[0] != fingerprint:
                        raise CollectedPolicyStoreError(_CONFLICT)
                    return CollectionImportResult(
                        int(existing[1]),
                        int(existing[2]),
                        int(existing[3]),
                        int(existing[4]),
                        replayed=True,
                    )

                latest = connection.execute(
                    """
                    SELECT completed_at
                    FROM public.collected_policy_runs
                    WHERE source = %s
                    ORDER BY completed_at DESC
                    LIMIT 1
                    """,
                    (batch.source_name,),
                ).fetchone()
                if latest is not None and batch.completed_at <= latest[0]:
                    raise CollectedPolicyStoreError(_REGRESSED)

                # Stage the already-validated generation once. The app performs no
                # network, LLM, or file work while this short transaction is open.
                connection.execute(
                    """
                    CREATE TEMP TABLE collected_policy_incoming (
                        source text NOT NULL CHECK (source = 'youthcenter'),
                        external_id text NOT NULL
                            CHECK (length(external_id) BETWEEN 1 AND 200
                                   AND btrim(external_id) <> ''),
                        material_sha256 text NOT NULL
                            CHECK (material_sha256 ~ '^[0-9a-f]{64}$'),
                        snapshot_ref text NOT NULL CHECK (btrim(snapshot_ref) <> ''),
                        fetched_at timestamptz NOT NULL,
                        fields jsonb NOT NULL CHECK (jsonb_typeof(fields) = 'object'),
                        payload jsonb NOT NULL
                            CHECK (jsonb_typeof(payload) = 'object'),
                        PRIMARY KEY (external_id)
                    ) ON COMMIT DROP
                    """
                )
                connection.execute(
                    """
                    INSERT INTO collected_policy_incoming
                        (source, external_id, material_sha256, snapshot_ref,
                         fetched_at, fields, payload)
                    SELECT record.source_name, record.external_id,
                           record.record_sha256, record.snapshot_ref,
                           record.fetched_at, record.fields, to_jsonb(record)
                    FROM jsonb_to_recordset(%s::jsonb) AS record(
                        source_name text,
                        external_id text,
                        record_sha256 text,
                        snapshot_ref text,
                        fetched_at timestamptz,
                        fields jsonb
                    )
                    """,
                    (incoming_json,),
                )

                connection.execute(
                    """
                    CREATE TEMP TABLE collected_policy_incoming_kind
                    ON COMMIT DROP AS
                    SELECT incoming.external_id,
                           CASE
                               WHEN policy.external_id IS NULL
                                    OR policy.missing_detected_at IS NOT NULL THEN 'new'
                               WHEN policy.material_sha256 <> incoming.material_sha256
                                   THEN 'changed'
                               ELSE 'unchanged'
                           END AS kind,
                           policy.material_sha256 AS previous_sha256
                    FROM collected_policy_incoming AS incoming
                    LEFT JOIN public.collected_policies AS policy
                      ON policy.source = incoming.source
                     AND policy.external_id = incoming.external_id
                    """
                )
                connection.execute(
                    """
                    CREATE TEMP TABLE collected_policy_missing
                    ON COMMIT DROP AS
                    SELECT policy.source, policy.external_id,
                           policy.material_sha256, policy.snapshot_ref,
                           policy.fetched_at, policy.fields
                    FROM public.collected_policies AS policy
                    WHERE policy.source = %s
                      AND policy.missing_detected_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM collected_policy_incoming AS incoming
                          WHERE incoming.source = policy.source
                            AND incoming.external_id = policy.external_id
                      )
                    """,
                    (batch.source_name,),
                )

                counts = connection.execute(
                    """
                    SELECT count(*) FILTER (WHERE kind = 'new')::integer,
                           count(*) FILTER (WHERE kind = 'changed')::integer,
                           count(*) FILTER (WHERE kind = 'unchanged')::integer,
                           (SELECT count(*)::integer FROM collected_policy_missing)
                    FROM collected_policy_incoming_kind
                    """
                ).fetchone()
                new_count, changed_count, unchanged_count, missing_count = (
                    int(value) for value in counts
                )

                connection.execute(
                    """
                    INSERT INTO public.collected_policy_runs
                        (source, run_id, completed_at, batch_sha256, record_count,
                         new_count, changed_count, unchanged_count, missing_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        batch.source_name,
                        batch.run_id,
                        batch.completed_at,
                        fingerprint,
                        len(batch.records),
                        new_count,
                        changed_count,
                        unchanged_count,
                        missing_count,
                    ),
                )

                # Unchanged material still refreshes source metadata and raw fields.
                connection.execute(
                    """
                    INSERT INTO public.collected_policies AS policy
                        (source, external_id, material_sha256, snapshot_ref,
                         fetched_at, fields, last_run_id, missing_detected_at)
                    SELECT source, external_id, material_sha256, snapshot_ref,
                           fetched_at, fields, %s, NULL
                    FROM collected_policy_incoming
                    ON CONFLICT (source, external_id) DO UPDATE SET
                        material_sha256 = EXCLUDED.material_sha256,
                        snapshot_ref = EXCLUDED.snapshot_ref,
                        fetched_at = EXCLUDED.fetched_at,
                        fields = EXCLUDED.fields,
                        last_run_id = EXCLUDED.last_run_id,
                        missing_detected_at = NULL
                    """,
                    (batch.run_id,),
                )

                connection.execute(
                    """
                    UPDATE public.collected_policies AS policy
                    SET missing_detected_at = %s
                    FROM collected_policy_missing AS missing
                    WHERE policy.source = missing.source
                      AND policy.external_id = missing.external_id
                      AND policy.missing_detected_at IS NULL
                    """,
                    (batch.completed_at,),
                )

                connection.execute(
                    """
                    INSERT INTO public.collected_policy_changes
                        (source, run_id, external_id, kind, previous_sha256,
                         current_sha256, record_payload)
                    SELECT incoming.source, %s, incoming.external_id, kind.kind,
                           CASE WHEN kind.kind = 'changed'
                                THEN kind.previous_sha256 ELSE NULL END,
                           incoming.material_sha256, incoming.payload
                    FROM collected_policy_incoming_kind AS kind
                    JOIN collected_policy_incoming AS incoming
                      ON incoming.external_id = kind.external_id
                    WHERE kind.kind IN ('new', 'changed')
                    UNION ALL
                    SELECT missing.source, %s, missing.external_id, 'missing',
                           missing.material_sha256, NULL,
                           jsonb_build_object(
                               'source_name', missing.source,
                               'external_id', missing.external_id,
                               'record_sha256', missing.material_sha256,
                               'snapshot_ref', missing.snapshot_ref,
                               'fetched_at', missing.fetched_at,
                               'fields', missing.fields
                           )
                    FROM collected_policy_missing AS missing
                    """,
                    (batch.run_id, batch.run_id),
                )

            return CollectionImportResult(
                new_count,
                changed_count,
                unchanged_count,
                missing_count,
            )
        except CollectedPolicyStoreError:
            raise
        except (
            psycopg.Error,
            OSError,
            ValueError,
            TypeError,
            OverflowError,
            RecursionError,
        ):
            raise CollectedPolicyStoreError(_UNAVAILABLE) from None

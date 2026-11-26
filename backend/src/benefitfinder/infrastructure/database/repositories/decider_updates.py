"""Persist completed local Decider output and protected model catalog updates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import re
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from benefitfinder.application.policy_analysis import (
    PolicyAnalysisInput,
    PolicyAnalysisStoreError,
)
from benefitfinder.infrastructure.ai_catalog_review import (
    _verify_real_source,
    project_youthcenter_source,
)
from benefitfinder.infrastructure.database.repositories.policy_analysis import (
    PostgresPolicyAnalysisTasks,
)
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord
from benefitfinder.infrastructure.unreviewed_catalog import (
    _verify_open_application,
    validate_unreviewed,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_F12_ERROR = re.compile(r"^f12_[a-z0-9_]{1,120}$")
_SAFE_CATALOG_ERROR = re.compile(r"^(?:catalog_|unreviewed_|ai_catalog_)[a-z0-9_]{1,120}$")
_CATALOG_OPTIONS = "-c statement_timeout=8000 -c lock_timeout=500 -c timezone=UTC"
_API_DECIDER_DOCUMENT_SUFFIX = ":api-decider-v2"


@dataclass(frozen=True, slots=True)
class _AnalysisEntry:
    value: PolicyAnalysisInput
    summary: dict
    artifact_sha256: str
    input_sha256: str


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    record: CatalogRecord
    source_text: str
    expected_previous_sha256: str | None
    record_sha256: str
    payload: dict


def _f12_error(error: psycopg.Error) -> PolicyAnalysisStoreError:
    primary = getattr(getattr(error, "diag", None), "message_primary", "")
    if isinstance(primary, str) and _SAFE_F12_ERROR.fullmatch(primary):
        return PolicyAnalysisStoreError(primary)
    return PolicyAnalysisStoreError("f12_analysis_storage_unavailable")


def _catalog_error(error: psycopg.Error) -> ValueError:
    primary = getattr(getattr(error, "diag", None), "message_primary", "")
    if isinstance(primary, str) and _SAFE_CATALOG_ERROR.fullmatch(primary):
        return ValueError(primary)
    return ValueError("model_catalog_storage_unavailable")


def _analysis_entries(
    entries: Sequence[tuple[PolicyAnalysisInput, dict, str]],
) -> tuple[_AnalysisEntry, ...]:
    try:
        size = len(entries)
    except (TypeError, AttributeError):
        raise ValueError("invalid_f12_analysis_batch") from None
    if not 1 <= size <= 32:
        raise ValueError("invalid_f12_analysis_batch")

    checked: list[_AnalysisEntry] = []
    for entry in entries:
        if not isinstance(entry, tuple) or len(entry) != 3:
            raise ValueError("invalid_f12_analysis_batch")
        value, summary, artifact_sha256 = entry
        if not isinstance(value, PolicyAnalysisInput) or not isinstance(summary, dict):
            raise ValueError("invalid_f12_analysis_output")
        if type(artifact_sha256) is not str or _SHA256.fullmatch(artifact_sha256) is None:
            raise ValueError("invalid_f12_analysis_output")
        try:
            encoded = json.dumps(
                summary, ensure_ascii=False, separators=(", ", ": "), allow_nan=False
            )
            if len(encoded.encode()) > 65_536:
                raise ValueError
            normalized_summary = json.loads(encoded)
            if not isinstance(normalized_summary, dict):
                raise ValueError
            input_sha256 = value.fingerprint()
        except (TypeError, ValueError, RecursionError, OverflowError):
            raise ValueError("invalid_f12_analysis_output") from None
        checked.append(_AnalysisEntry(value, normalized_summary, artifact_sha256, input_sha256))

    policy_ids = [entry.value.policy_id for entry in checked]
    if len(policy_ids) != len(set(policy_ids)):
        raise ValueError("invalid_f12_analysis_batch")
    return tuple(checked)


def _lock_keys(connection: psycopg.Connection, keys: Sequence[str]) -> None:
    if keys:
        connection.execute(
            """SELECT pg_advisory_xact_lock(hashtextextended(ordered.key,0))
               FROM (SELECT key FROM unnest(%s::text[]) WITH ORDINALITY AS item(key,ordinality)
                     ORDER BY ordinality) ordered""",
            (list(keys),),
        )


def _observe_sources(
    connection: psycopg.Connection,
    entries: Sequence[_AnalysisEntry | _CatalogEntry],
) -> dict[tuple[str, str], dict]:
    if entries and isinstance(entries[0], _CatalogEntry):
        bindings = [source for entry in entries for source in entry.record.collection_sources]
    else:
        bindings = [source for entry in entries for source in entry.value.collection_sources]
    wanted = {
        (source.source, source.external_id): {
            "source": source.source,
            "external_id": source.external_id,
        }
        for source in bindings
    }
    rows = connection.execute(
        """SELECT w.source,w.external_id,p.material_sha256,p.missing_detected_at,p.fields,
                  (SELECT max(r.completed_at)
                   FROM public.collected_policy_changes c
                   JOIN public.collected_policy_runs r
                     ON r.source=c.source AND r.run_id=c.run_id
                   WHERE c.source=p.source AND c.external_id=p.external_id
                     AND c.current_sha256=p.material_sha256) AS changed_at
           FROM jsonb_to_recordset(%s::jsonb) AS w(source text,external_id text)
           LEFT JOIN public.collected_policies p
             ON p.source=w.source AND p.external_id=w.external_id""",
        (Jsonb(list(wanted.values())),),
    ).fetchall()
    return {(row["source"], row["external_id"]): row for row in rows}


class _ObservedRow:
    def __init__(self, row: dict) -> None:
        self._row = row
        self._ordered = (
            row.get("fields"),
            row.get("material_sha256"),
            row.get("missing_detected_at"),
            row.get("changed_at"),
        )

    def __getitem__(self, key: int | str):
        return self._ordered[key] if isinstance(key, int) else self._row[key]


class _OneRowCursor:
    def __init__(self, row: _ObservedRow | None) -> None:
        self._row = row

    def fetchone(self) -> _ObservedRow | None:
        return self._row


class _ObservedSourceConnection:
    """Serve preloaded source rows to the unchanged validation helpers."""

    def __init__(self, connection: psycopg.Connection, rows: dict[tuple[str, str], dict]) -> None:
        self._connection = connection
        self._rows = rows

    def execute(self, query: str, params: Sequence[object] = ()):
        if "FROM public.collected_policies" in query and len(params) >= 2:
            row = self._rows.get((str(params[0]), str(params[1])))
            return _OneRowCursor(_ObservedRow(row) if row is not None else None)
        return self._connection.execute(query, params)


def _lock_analysis_sources(
    connection: psycopg.Connection, entries: Sequence[_AnalysisEntry]
) -> None:
    sources = {source.source for entry in entries for source in entry.value.collection_sources}
    for source in sorted(sources):
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("benefitfinder:collected_policy_generations:" + source,),
        )


def _verify_analysis_documents(
    connection: psycopg.Connection,
    value: PolicyAnalysisInput,
    now: datetime,
) -> None:
    changed_at = datetime.min.replace(tzinfo=timezone.utc)
    observed_sources: dict[tuple[str, str], dict] = {}
    for source in value.collection_sources:
        observed = connection.execute(
            """SELECT p.material_sha256,p.missing_detected_at,p.fields,
                      (SELECT max(r.completed_at)
                       FROM public.collected_policy_changes c
                       JOIN public.collected_policy_runs r
                         ON r.source=c.source AND r.run_id=c.run_id
                       WHERE c.source=p.source AND c.external_id=p.external_id
                         AND c.current_sha256=p.material_sha256) AS changed_at
               FROM public.collected_policies p
               WHERE p.source=%s AND p.external_id=%s""",
            (source.source, source.external_id),
        ).fetchone()
        if (
            observed is None
            or observed["missing_detected_at"] is not None
            or observed["material_sha256"] != source.material_sha256
            or observed["changed_at"] is None
        ):
            raise PolicyAnalysisStoreError("f12_analysis_source_stale")
        if not isinstance(observed["fields"], dict):
            raise PolicyAnalysisStoreError("f12_document_source_mismatch")
        changed_at = max(changed_at, observed["changed_at"])
        observed_sources[(source.source, source.external_id)] = observed

    references = {item.document_id: item for item in value.document_sources}
    for document in value.documents:
        api_source = next(
            (
                source
                for source in value.collection_sources
                if source.source == "youthcenter"
                and document.id
                == "youthcenter:" + source.external_id + _API_DECIDER_DOCUMENT_SUFFIX
            ),
            None,
        )
        if api_source is not None:
            observed = observed_sources[(api_source.source, api_source.external_id)]
            if (
                document.text != project_youthcenter_source(observed["fields"])
                or document.document_version != "sha256:" + api_source.material_sha256
                or document.source_url
                != "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/"
                + api_source.external_id
            ):
                raise PolicyAnalysisStoreError("f12_document_source_mismatch")
        reference = references.get(document.id)
        required_at = changed_at
        if reference is not None:
            observed = observed_sources[(reference.source, reference.external_id)]
            required_at = observed["changed_at"]
            source_document = document
            if reference.source in {"gov24", "youthcenter"} and document.id.endswith(
                _API_DECIDER_DOCUMENT_SUFFIX
            ):
                source_document = replace(
                    document,
                    id=document.id.removesuffix(_API_DECIDER_DOCUMENT_SUFFIX),
                )
            PostgresPolicyAnalysisTasks._verify_document_source(
                source_document,
                reference,
                observed,
                value.policy_id,
            )
        if not required_at <= document.collected_at <= now:
            raise PolicyAnalysisStoreError("f12_documents_require_refresh")


def store_decider_analyses(
    database_url: str,
    entries: Sequence[tuple[PolicyAnalysisInput, dict, str]],
    *,
    analysis_version: int = 8,
) -> dict:
    """Store completed local artifacts as terminal ``needs_review`` F12 tasks.

    The local model has already run, so this function never claims a queued task.
    It locks every collection source, verifies the exact current projections, and
    commits at most 32 terminal tasks atomically. Existing completed output is
    replayed only when the artifact SHA256 is identical.
    """
    tasks = PostgresPolicyAnalysisTasks(database_url, analysis_version=analysis_version)
    checked = _analysis_entries(entries)
    counts = {"inserted": 0, "replayed": 0}

    try:
        with tasks._connect() as connection:
            source_keys = sorted(
                {
                    "benefitfinder:collected_policy_generations:" + source.source
                    for entry in checked
                    for source in entry.value.collection_sources
                }
            )
            policy_keys = sorted(
                {"benefitfinder:f12_policy:" + entry.value.policy_id for entry in checked}
            )
            _lock_keys(connection, (*source_keys, *policy_keys))
            observations = _observe_sources(connection, checked)
            source_connection = _ObservedSourceConnection(connection, observations)
            now = connection.execute("SELECT clock_timestamp() AS now").fetchone()["now"]

            for entry in checked:
                _verify_analysis_documents(source_connection, entry.value, now)

            wanted = [
                {"policy_id": entry.value.policy_id, "input_sha256": entry.input_sha256}
                for entry in checked
            ]
            existing_rows = connection.execute(
                """SELECT t.task_id,t.policy_id,t.input_sha256,t.status,t.output_sha256,
                          t.analysis_version
                   FROM public.f12_analysis_tasks t
                   JOIN jsonb_to_recordset(%s::jsonb) AS incoming(
                       policy_id text,input_sha256 text)
                     ON t.policy_id=incoming.policy_id
                    AND (t.analysis_version>%s
                         OR (t.analysis_version=%s
                             AND t.input_sha256=incoming.input_sha256))
                   FOR UPDATE OF t""",
                (Jsonb(wanted), analysis_version, analysis_version),
            ).fetchall()
            existing = {}
            for row in existing_rows:
                if row["analysis_version"] > analysis_version:
                    raise PolicyAnalysisStoreError("f12_analysis_version_obsolete")
                existing[(row["policy_id"], row["input_sha256"])] = row

            pending: list[tuple[_AnalysisEntry, str]] = []
            for entry in checked:
                prior = existing.get((entry.value.policy_id, entry.input_sha256))
                if prior is not None:
                    if (
                        prior["status"] == "needs_review"
                        and prior["output_sha256"] == entry.artifact_sha256
                    ):
                        counts["replayed"] += 1
                        continue
                    if prior["output_sha256"] is not None:
                        raise PolicyAnalysisStoreError("f12_analysis_result_conflict")
                    raise PolicyAnalysisStoreError("f12_analysis_task_conflict")
                pending.append((entry, uuid4().hex))

            if pending:
                incoming = [
                    {
                        "policy_id": entry.value.policy_id,
                        "input_sha256": entry.input_sha256,
                        "input_payload": entry.value.payload(),
                        "analysis_version": analysis_version,
                        "output_sha256": entry.artifact_sha256,
                        "result_summary": entry.summary,
                        "output_uuid": token,
                    }
                    for entry, token in pending
                ]
                inserted = connection.execute(
                    """WITH incoming AS (
                         SELECT * FROM jsonb_to_recordset(%s::jsonb) AS item(
                           policy_id text,input_sha256 text,input_payload jsonb,
                           analysis_version integer,output_sha256 text,
                           result_summary jsonb,output_uuid text))
                       INSERT INTO public.f12_analysis_tasks
                         (policy_id,input_sha256,input_payload,analysis_version,status,attempts)
                       SELECT policy_id,input_sha256,input_payload,analysis_version,'pending',1
                       FROM incoming
                       RETURNING task_id,policy_id,input_sha256""",
                    (Jsonb(incoming),),
                ).fetchall()
                if len(inserted) != len(pending):
                    raise PolicyAnalysisStoreError("f12_analysis_task_conflict")

                sources = [
                    {
                        "policy_id": entry.value.policy_id,
                        "input_sha256": entry.input_sha256,
                        "source": source.source,
                        "external_id": source.external_id,
                        "material_sha256": source.material_sha256,
                    }
                    for entry, _ in pending
                    for source in entry.value.collection_sources
                ]
                terminal_sources = connection.execute(
                    """WITH incoming AS (
                         SELECT * FROM jsonb_to_recordset(%s::jsonb) AS item(
                           policy_id text,input_sha256 text,input_payload jsonb,
                           analysis_version integer,output_sha256 text,
                           result_summary jsonb,output_uuid text)),
                       updated AS (
                         UPDATE public.f12_analysis_tasks task
                         SET status='needs_review',
                             output_ref='f12-analysis/'||task.task_id::text||'/'||item.output_uuid,
                             output_sha256=item.output_sha256,
                             result_summary=item.result_summary,
                             last_error=NULL,updated_at=clock_timestamp()
                         FROM incoming item
                         WHERE task.policy_id=item.policy_id
                           AND task.input_sha256=item.input_sha256
                           AND task.analysis_version=item.analysis_version
                           AND task.status='pending' AND task.attempts=1
                         RETURNING task.task_id,task.policy_id,task.input_sha256),
                       source_rows AS (
                         SELECT updated.task_id,item.source,item.external_id,item.material_sha256
                         FROM updated
                         JOIN jsonb_to_recordset(%s::jsonb) AS item(
                           policy_id text,input_sha256 text,source text,
                           external_id text,material_sha256 text)
                           ON item.policy_id=updated.policy_id
                          AND item.input_sha256=updated.input_sha256)
                       INSERT INTO public.f12_analysis_sources
                         (task_id,source,external_id,material_sha256)
                       SELECT task_id,source,external_id,material_sha256 FROM source_rows
                       RETURNING task_id""",
                    (Jsonb(incoming), Jsonb(sources)),
                ).fetchall()
                if len(terminal_sources) != len(sources) or len(
                    {row["task_id"] for row in terminal_sources}
                ) != len(pending):
                    raise PolicyAnalysisStoreError("f12_analysis_task_conflict")
                counts["inserted"] += len(pending)
    except PolicyAnalysisStoreError:
        raise
    except psycopg.Error as error:
        raise _f12_error(error) from None
    except (OSError, TypeError, ValueError):
        raise PolicyAnalysisStoreError("f12_analysis_storage_unavailable") from None
    return counts


def _catalog_entries(
    entries: Sequence[tuple[CatalogRecord, str, str | None]],
) -> tuple[_CatalogEntry, ...]:
    try:
        size = len(entries)
    except (TypeError, AttributeError):
        raise ValueError("invalid_model_catalog_batch") from None
    if not 1 <= size <= 32:
        raise ValueError("invalid_model_catalog_batch")

    checked: list[_CatalogEntry] = []
    for entry in entries:
        if not isinstance(entry, tuple) or len(entry) != 3:
            raise ValueError("invalid_model_catalog_batch")
        record, source_text, expected_previous = entry
        if not isinstance(record, CatalogRecord) or type(source_text) is not str:
            raise ValueError("invalid_model_catalog_batch")
        if expected_previous is not None and (
            type(expected_previous) is not str or _SHA256.fullmatch(expected_previous) is None
        ):
            raise ValueError("invalid_model_catalog_expected_previous")
        record_sha256 = validate_unreviewed(record, source_text)
        checked.append(
            _CatalogEntry(
                record,
                source_text,
                expected_previous,
                record_sha256,
                record.model_dump(mode="json"),
            )
        )

    policy_ids = [entry.record.benefit.id for entry in checked]
    if len(policy_ids) != len(set(policy_ids)):
        raise ValueError("invalid_model_catalog_batch")

    owners: dict[str, str] = {}
    for entry in checked:
        policy_id = entry.record.benefit.id
        aliases = {policy_id}
        aliases.update(
            name
            for source in entry.record.collection_sources
            if source.source == "youthcenter"
            for name in ("youth:" + source.external_id, "youth:youthcenter:" + source.external_id)
        )
        for alias in aliases:
            owner = owners.setdefault(alias, policy_id)
            if owner != policy_id:
                raise ValueError("model_catalog_alias_conflict")
    return tuple(checked)


def _catalog_alias_is_owned_elsewhere(
    connection: psycopg.Connection,
    entry: _CatalogEntry,
) -> bool:
    policy_id = entry.record.benefit.id
    aliases = {policy_id}
    external_ids = set()
    for source in entry.record.collection_sources:
        if source.source == "youthcenter":
            external_ids.add(source.external_id)
            aliases.update(
                ("youth:" + source.external_id, "youth:youthcenter:" + source.external_id)
            )
    matches = connection.execute(
        """SELECT DISTINCT r.policy_id
           FROM public.policy_catalog_releases r
           LEFT JOIN public.policy_catalog_source_links link
             ON link.policy_id=r.policy_id AND link.document_version=r.document_version
           WHERE (r.policy_id=ANY(%s)
                  OR (link.source='youthcenter' AND link.external_id=ANY(%s)))
             AND r.publication_status IN ('published','retired')""",
        (sorted(aliases), sorted(external_ids)),
    ).fetchall()
    return any(row[0] != policy_id for row in matches)


def _insert_unreviewed_release(
    connection: psycopg.Connection,
    entry: _CatalogEntry,
    published_at: datetime,
) -> None:
    record = entry.record
    provenance = {
        "document_version": record.benefit.source.document_version,
        "source_sha256": record.source_sha256,
        "record_sha256": entry.record_sha256,
    }
    connection.execute(
        """INSERT INTO public.policy_catalog_releases
           (policy_id,document_version,payload,approved_sha256,review_status,reviewed_by,
            reviewed_at,publication_status,published_at,review_type,unreviewed_source)
           VALUES (%s,%s,%s,%s,'unreviewed',NULL,NULL,'published',%s,'unreviewed',%s)""",
        (
            record.benefit.id,
            record.benefit.source.document_version,
            Jsonb(entry.payload),
            entry.record_sha256,
            published_at,
            Jsonb(provenance),
        ),
    )


def publish_model_catalog_updates(
    database_url: str,
    entries: Sequence[tuple[CatalogRecord, str, str | None]],
) -> dict:
    """Publish metadata-only unreviewed records without touching approvals.

    ``expected_previous_sha256`` is required only when replacing the current
    unreviewed release. Approved releases and aliases owned by another policy are
    counted as ``protected_existing`` and left unchanged.
    """
    try:
        if not database_url.strip() or not conninfo_to_dict(database_url).get("dbname"):
            raise ValueError
    except (psycopg.Error, ValueError, AttributeError):
        raise ValueError("invalid_model_catalog_database_url") from None

    checked = _catalog_entries(entries)
    counts = {"inserted": 0, "replaced": 0, "replayed": 0, "protected_existing": 0}
    try:
        with psycopg.connect(
            database_url,
            connect_timeout=2,
            options=_CATALOG_OPTIONS,
            application_name="benefitfinder-decider-catalog-updates",
            row_factory=dict_row,
        ) as connection:
            versions = {
                row["version"]
                for row in connection.execute(
                    """SELECT version FROM public.benefitfinder_schema_migrations
                       WHERE version=ANY(%s)""",
                    (["0006_policy_source_rechecks", "0014_unreviewed_catalog"],),
                ).fetchall()
            }
            if not {"0006_policy_source_rechecks", "0014_unreviewed_catalog"} <= versions:
                raise ValueError("model_catalog_migration_required")

            source_keys = [
                "benefitfinder:collected_policy_generations:" + name
                for name in ("gov24", "official_documents", "youthcenter")
            ]
            policy_ids = sorted(entry.record.benefit.id for entry in checked)
            _lock_keys(connection, (*source_keys, *policy_ids))

            observations = _observe_sources(connection, checked)
            source_connection = _ObservedSourceConnection(connection, observations)
            for entry in checked:
                record = entry.record
                for source in record.collection_sources:
                    observed = observations.get((source.source, source.external_id))
                    if (
                        observed is None
                        or observed["missing_detected_at"] is not None
                        or observed["material_sha256"] != source.material_sha256
                        or observed["changed_at"] is None
                        or not isinstance(observed["fields"], dict)
                    ):
                        raise ValueError("catalog_source_is_stale")
                _verify_real_source(source_connection, record, entry.source_text)
                _verify_open_application(source_connection, record)

            aliases = set()
            external_ids = set()
            entry_aliases: dict[str, tuple[set[str], set[str]]] = {}
            for entry in checked:
                policy_id = entry.record.benefit.id
                own_aliases = {policy_id}
                own_external_ids = set()
                for source in entry.record.collection_sources:
                    if source.source == "youthcenter":
                        own_external_ids.add(source.external_id)
                        own_aliases.update(
                            (
                                "youth:" + source.external_id,
                                "youth:youthcenter:" + source.external_id,
                            )
                        )
                aliases.update(own_aliases)
                external_ids.update(own_external_ids)
                entry_aliases[policy_id] = (own_aliases, own_external_ids)
            alias_rows = connection.execute(
                """SELECT DISTINCT r.policy_id,link.source AS link_source,
                                  link.external_id AS link_external_id
                   FROM public.policy_catalog_releases r
                   LEFT JOIN public.policy_catalog_source_links link
                     ON link.policy_id=r.policy_id AND link.document_version=r.document_version
                   WHERE (r.policy_id=ANY(%s)
                          OR (link.source='youthcenter' AND link.external_id=ANY(%s)))
                     AND r.publication_status IN ('published','retired')""",
                (sorted(aliases), sorted(external_ids)),
            ).fetchall()

            release_rows = connection.execute(
                """SELECT policy_id,document_version,approved_sha256,review_status,
                          review_type,publication_status
                   FROM public.policy_catalog_releases
                   WHERE policy_id=ANY(%s) FOR UPDATE""",
                (policy_ids,),
            ).fetchall()
            releases: dict[str, list[dict]] = {policy_id: [] for policy_id in policy_ids}
            for row in release_rows:
                releases[row["policy_id"]].append(row)

            retire: list[_CatalogEntry] = []
            insert: list[tuple[_CatalogEntry, str]] = []
            for entry in checked:
                policy_id = entry.record.benefit.id
                own_aliases, own_external_ids = entry_aliases[policy_id]
                foreign = any(
                    row["policy_id"] != policy_id
                    and (
                        row["policy_id"] in own_aliases
                        or (
                            row["link_source"] == "youthcenter"
                            and row["link_external_id"] in own_external_ids
                        )
                    )
                    for row in alias_rows
                )
                if foreign:
                    counts["protected_existing"] += 1
                    continue

                history = releases[policy_id]
                current_rows = [row for row in history if row["publication_status"] == "published"]
                if len(current_rows) > 1:
                    raise ValueError("unreviewed_existing_release")
                current = current_rows[0] if current_rows else None
                new_version = entry.record.benefit.source.document_version
                if current is not None:
                    if current["review_status"] == "approved":
                        counts["protected_existing"] += 1
                        continue
                    if (
                        current["review_status"] != "unreviewed"
                        or current["review_type"] != "unreviewed"
                    ):
                        raise ValueError("unreviewed_existing_release")
                    if current["approved_sha256"] == entry.record_sha256:
                        counts["replayed"] += 1
                        continue
                    if current["document_version"] == new_version:
                        raise ValueError("catalog_version_hash_conflict")
                    if (
                        entry.expected_previous_sha256 is None
                        or entry.expected_previous_sha256 != current["approved_sha256"]
                    ):
                        raise ValueError("catalog_replacement_conflict")
                    if any(row["document_version"] == new_version for row in history):
                        raise ValueError("catalog_version_hash_conflict")
                    retire.append(entry)
                    insert.append((entry, "replaced"))
                    continue

                if entry.expected_previous_sha256 is not None:
                    raise ValueError("catalog_replacement_conflict")
                if any(row["document_version"] == new_version for row in history):
                    raise ValueError("catalog_version_hash_conflict")
                insert.append((entry, "inserted"))

            published_at = datetime.now(timezone.utc)
            if retire:
                retired = connection.execute(
                    """WITH incoming AS (
                         SELECT * FROM jsonb_to_recordset(%s::jsonb) AS item(
                           policy_id text,document_version text,approved_sha256 text))
                       UPDATE public.policy_catalog_releases r
                       SET publication_status='retired'
                       FROM incoming
                       WHERE r.policy_id=incoming.policy_id
                         AND r.document_version=incoming.document_version
                         AND r.approved_sha256=incoming.approved_sha256
                         AND r.review_type='unreviewed'
                         AND r.review_status='unreviewed'
                         AND r.publication_status='published'
                       RETURNING r.policy_id""",
                    (
                        Jsonb(
                            [
                                {
                                    "policy_id": entry.record.benefit.id,
                                    "document_version": next(
                                        row["document_version"]
                                        for row in releases[entry.record.benefit.id]
                                        if row["publication_status"] == "published"
                                    ),
                                    "approved_sha256": next(
                                        row["approved_sha256"]
                                        for row in releases[entry.record.benefit.id]
                                        if row["publication_status"] == "published"
                                    ),
                                }
                                for entry in retire
                            ]
                        ),
                    ),
                ).fetchall()
                if len(retired) != len(retire):
                    raise ValueError("catalog_replacement_conflict")

            if insert:
                incoming = [
                    {
                        "policy_id": entry.record.benefit.id,
                        "document_version": entry.record.benefit.source.document_version,
                        "payload": entry.payload,
                        "approved_sha256": entry.record_sha256,
                        "unreviewed_source": {
                            "document_version": entry.record.benefit.source.document_version,
                            "source_sha256": entry.record.source_sha256,
                            "record_sha256": entry.record_sha256,
                        },
                    }
                    for entry, _ in insert
                ]
                inserted = connection.execute(
                    """WITH incoming AS (
                         SELECT * FROM jsonb_to_recordset(%s::jsonb) AS item(
                           policy_id text,document_version text,payload jsonb,
                           approved_sha256 text,unreviewed_source jsonb))
                       INSERT INTO public.policy_catalog_releases
                         (policy_id,document_version,payload,approved_sha256,review_status,
                          reviewed_by,reviewed_at,publication_status,published_at,
                          review_type,unreviewed_source)
                       SELECT policy_id,document_version,payload,approved_sha256,
                              'unreviewed',NULL,NULL,'published',%s,
                              'unreviewed',unreviewed_source
                       FROM incoming RETURNING policy_id""",
                    (Jsonb(incoming), published_at),
                ).fetchall()
                if len(inserted) != len(insert):
                    raise ValueError("model_catalog_storage_unavailable")
                for _, kind in insert:
                    counts[kind] += 1
    except psycopg.Error as error:
        raise _catalog_error(error) from None
    return counts

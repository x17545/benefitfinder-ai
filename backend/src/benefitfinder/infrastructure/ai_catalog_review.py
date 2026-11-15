"""Validate and explicitly publish or retire source-bound AI-reviewed batches."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash, validate_source_text
from benefitfinder.infrastructure.service_catalog_records import (
    ServiceCatalogRecord,
    service_record_hash,
    validate_service_sources,
)


class AIReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    writer_agent: str = Field(min_length=1, max_length=200)
    reviewer_agent: str = Field(min_length=1, max_length=200)
    reviewer_model: str = Field(min_length=1, max_length=200)
    verdict: Literal["pass", "revise", "hold"]
    reviewed_at: datetime
    document_version: str = Field(min_length=1, max_length=120)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def identity_and_time(self) -> AIReview:
        identities = (self.writer_agent, self.reviewer_agent, self.reviewer_model)
        if any(not value.strip() or value != value.strip() for value in identities):
            raise ValueError("invalid_ai_review_identity")
        if self.writer_agent == self.reviewer_agent:
            raise ValueError("ai_review_must_be_independent")
        if self.reviewed_at.utcoffset() is None:
            raise ValueError("ai_review_requires_timezone")
        return self


def validate_ai_review(
    record: CatalogRecord | ServiceCatalogRecord,
    review: AIReview,
    *,
    source_text: str,
    price_source_text: str | None = None,
    now: datetime | None = None,
) -> None:
    """Reject non-passing or stale AI review and retain existing source checks."""
    if review.verdict != "pass":
        raise ValueError("ai_review_not_passed")
    current = now or datetime.now(timezone.utc)
    if current.utcoffset() is None or review.reviewed_at > current:
        raise ValueError("invalid_ai_review_time")
    source = record.benefit.source
    checked_at = datetime.fromisoformat(source.checked_at.replace("Z", "+00:00"))
    if review.reviewed_at < checked_at:
        raise ValueError("ai_review_predates_source")
    if review.document_version != source.document_version or review.source_sha256 != record.source_sha256:
        raise ValueError("ai_review_source_mismatch")
    if isinstance(record, ServiceCatalogRecord):
        digest = service_record_hash(record)
        if record.price is not None:
            price_checked_at = datetime.fromisoformat(record.price.source.checked_at.replace("Z", "+00:00"))
            if review.reviewed_at < price_checked_at:
                raise ValueError("ai_review_predates_price_source")
        validate_service_sources(record, source_text, price_source_text)
    else:
        digest = record_hash(record)
        validate_source_text(record, source_text)
    if review.record_sha256 != digest:
        raise ValueError("ai_review_record_mismatch")


# Exact, reproducible projection of the saved youthcenter response. Never add
# guessed values; the source link and the projected text are checked under lock.
YOUTHCENTER_SOURCE_FIELDS = (
    "plcyNo", "plcyNm", "lclsfNm", "mclsfNm", "plcyExplnCn", "plcySprtCn", "aplyYmd",
    "aplyPrdSeCd", "plcyAplyMthdCn", "addAplyQlfcCndCn", "ptcpPrpTrgtCn", "etcMttrCn",
    "srngMthdCn", "sbmsnDcmntCn", "earnEtcCn", "earnMinAmt", "earnMaxAmt", "schoolCd",
    "jobCd", "sprtTrgtAgeLmtYn", "sprtTrgtMinAge", "sprtTrgtMaxAge", "zipCd",
    "aplyUrlAddr", "refUrlAddr1", "refUrlAddr2", "rgtrInstCdNm", "sprvsnInstCdNm",
    "bizPrdBgngYmd", "bizPrdEndYmd", "bizPrdEtcCn",
)


def project_youthcenter_source(fields: dict) -> str:
    fragments = []
    for key in YOUTHCENTER_SOURCE_FIELDS:
        value = fields.get(key)
        if value is None or value == "":
            continue
        if type(value) not in (str, int) or "\0" in str(value):
            raise ValueError("invalid_youthcenter_source_field")
        if str(value).strip():
            fragments.append(f"[{key}]\n{value}")
    if not fragments:
        raise ValueError("empty_youthcenter_source")
    return "\n\n".join(fragments)


def _verify_real_source(connection, record, source_text: str) -> None:
    """Keep AI publication tied to the exact locked youthcenter fields."""
    if record.benefit.virtual_data:
        return
    if len(record.collection_sources) != 1 or record.collection_sources[0].source != "youthcenter":
        raise ValueError("ai_catalog_youthcenter_source_required")
    if isinstance(record, ServiceCatalogRecord) and record.price is not None:
        raise ValueError("ai_catalog_separate_price_source_unsupported")
    binding = record.collection_sources[0]
    if not binding.external_id.isascii() or not binding.external_id.isdigit():
        raise ValueError("invalid_youthcenter_policy_id")
    observed = connection.execute(
        "SELECT fields,material_sha256,missing_detected_at FROM public.collected_policies "
        "WHERE source=%s AND external_id=%s", (binding.source, binding.external_id),
    ).fetchone()
    if observed is None or observed[2] is not None or observed[1] != binding.material_sha256:
        raise ValueError("ai_catalog_source_stale")
    fields = observed[0]
    expected_url = "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/" + binding.external_id
    if (fields.get("plcyNo") != binding.external_id or fields.get("plcyNm") != record.benefit.title
        or record.benefit.id != "youth:" + binding.external_id
        or record.benefit.source.url != expected_url
        or record.benefit.source.document_version != "sha256:" + binding.material_sha256
        or source_text != project_youthcenter_source(fields)):
        raise ValueError("ai_catalog_source_projection_mismatch")


def publish_ai_batch(database_url: str, entries: list[tuple[
    CatalogRecord | ServiceCatalogRecord, AIReview, str, str | None
]]) -> None:
    """Publish validated AI-reviewed records atomically; existing guards stay active."""
    import psycopg
    from psycopg.types.json import Jsonb

    if not 1 <= len(entries) <= 50:
        raise ValueError("invalid_ai_catalog_batch_size")
    ids = [record.benefit.id for record, _, _, _ in entries]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate_ai_catalog_policy")
    for record, review, source, price_source in entries:
        validate_ai_review(record, review, source_text=source, price_source_text=price_source)
        if record.f12_review is not None and review.reviewed_at < datetime.fromisoformat(
            record.f12_review.latest_reviewed_at.replace("Z", "+00:00")
        ):
            raise ValueError("ai_review_predates_f12_review")
    with psycopg.connect(database_url, connect_timeout=2) as connection:
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL lock_timeout = '500ms'")
        versions = {row[0] for row in connection.execute(
            "SELECT version FROM public.benefitfinder_schema_migrations WHERE version = ANY(%s)",
            (["0006_policy_source_rechecks", "0008_service_catalog", "0010_catalog_explanation_publication", "0013_ai_catalog_review"],),
        ).fetchall()}
        required = {"0006_policy_source_rechecks", "0013_ai_catalog_review"}
        if any(isinstance(record, ServiceCatalogRecord) for record, _, _, _ in entries):
            required.add("0008_service_catalog")
        if any(record.explanation_clauses is not None for record, _, _, _ in entries):
            required.add("0010_catalog_explanation_publication")
        if not required <= versions:
            raise ValueError("ai_catalog_migration_required")
        for source_name in ("gov24", "official_documents", "youthcenter"):
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                               ("benefitfinder:collected_policy_generations:" + source_name,))
        for policy_id in sorted(ids):
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (policy_id,))
        published_at = datetime.now(timezone.utc)
        for record, review, source_text, _ in entries:
            _verify_real_source(connection, record, source_text)
            if connection.execute(
                "SELECT 1 FROM public.policy_catalog_releases WHERE policy_id=%s AND publication_status='published'",
                (record.benefit.id,),
            ).fetchone():
                raise ValueError("ai_catalog_existing_release_requires_separate_review")
            connection.execute(
                """INSERT INTO public.policy_catalog_releases
                (policy_id,document_version,payload,approved_sha256,review_status,reviewed_by,
                 reviewed_at,publication_status,published_at,review_type,ai_review)
                VALUES (%s,%s,%s,%s,'approved',%s,%s,'published',%s,'ai',%s)""",
                (record.benefit.id, record.benefit.source.document_version,
                 Jsonb(record.model_dump(mode="json")), review.record_sha256,
                 "ai:" + review.reviewer_agent, review.reviewed_at, published_at,
                 Jsonb(review.model_dump(mode="json"))),
            )


def retire_ai_batch(database_url: str, releases: list[tuple[str, str, str]]) -> None:
    """Retire only the exact AI releases supplied; fail atomically on any mismatch."""
    import psycopg

    if not 1 <= len(releases) <= 50 or len({item[0] for item in releases}) != len(releases):
        raise ValueError("invalid_ai_catalog_batch")
    with psycopg.connect(database_url, connect_timeout=2) as connection:
        connection.execute("SET LOCAL statement_timeout = '5s'")
        connection.execute("SET LOCAL lock_timeout = '500ms'")
        for source_name in ("gov24", "official_documents", "youthcenter"):
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                               ("benefitfinder:collected_policy_generations:" + source_name,))
        for policy_id, _, _ in sorted(releases):
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (policy_id,))
        for policy_id, version, digest in releases:
            result = connection.execute(
                "UPDATE public.policy_catalog_releases SET publication_status='retired' "
                "WHERE policy_id=%s AND document_version=%s AND approved_sha256=%s "
                "AND review_type='ai' AND publication_status='published'",
                (policy_id, version, digest),
            )
            if result.rowcount != 1:
                raise ValueError("ai_catalog_release_not_current")

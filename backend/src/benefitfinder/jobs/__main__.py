"""수집·로컬 검수 작업 실행 진입점 (조립 지점).

    uv run --locked python -m benefitfinder.jobs collect-youthcenter

인증키는 환경 변수 ``YOUTHCENTER_API_KEY``에서 읽고 출력하지 않는다. 결과는 기본으로
``data/raw``(원본)와 ``data/processed``(최신 레코드·변경 이력·CSV)에 쓰며 둘 다 Git 제외
경로다. rule-review는 검수 자료를 새 파일로 보관하며, 운영 게시나 스케줄은 연결하지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import threading
from contextlib import contextmanager
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

# The source-only CLI must not load model clients or AI quality gates.
if sys.argv[1:3] != ['decider-batch', 'import-source']:
    from benefitfinder.ai.chunked_evidence import extract_chunked_evidence
    from benefitfinder.ai.policy_facts_agent import extract_policy_facts
    from benefitfinder.ai.policy_rule_agent import extract_rule_batch
    from benefitfinder.application.f12_review import F12ReviewDecision
    from benefitfinder.infrastructure.collection_files import read_collection_file
    from benefitfinder.infrastructure.database.repositories.collected_policies import (
        PostgresCollectedPolicies,
    )
    from benefitfinder.ingestion.collected_generations import load_youthcenter_generation
    from benefitfinder.infrastructure.ecos_base_rate import EcosBaseRate
    from benefitfinder.infrastructure.database.repositories.base_rates import PostgresBaseRates
    from benefitfinder.application.policy_rules import RuleReviewSnapshot
    from benefitfinder.infrastructure.chunk_e4b import LocalE4BChat
    from benefitfinder.infrastructure.chunk_evidence_files import write_chunk_checkpoint, write_chunked_run
    from benefitfinder.infrastructure.discord_webhook import DiscordWebhook, DiscordWebhookError
    from benefitfinder.infrastructure.local_files import (
        JsonFileSnapshotStore,
        JsonFileVersionStore,
        write_field_summary_csv,
        write_records_csv,
    )
    from benefitfinder.infrastructure.f12_evaluation_files import (
        EvaluationFileError,
        evaluate_manifest,
        write_evaluation_report,
    )
    from benefitfinder.infrastructure.f12_catalog_files import prepare_f12_release, write_f12_catalog
    from benefitfinder.infrastructure.ai_catalog_review import AIReview, publish_ai_batch, retire_ai_batch, validate_ai_review
    from benefitfinder.infrastructure.unreviewed_catalog import publish_unreviewed, retire_unreviewed, validate_unreviewed
    from benefitfinder.infrastructure.ollama_chat import OllamaChat
    from benefitfinder.infrastructure.compatible_llm import (
        CompatibleChat,
        CompatibleExplanationCompletion,
    )
    from benefitfinder.infrastructure.llm_settings import LLMSettings
    from benefitfinder.application.source_chunks import evidence_schema
    from benefitfinder.infrastructure.policy_rule_files import (
        RULE_REVIEW_OUTPUT_EXISTS,
        RuleReviewFileError,
        load_f12_decisions,
        load_f12_review_pair,
        load_rule_batch,
        load_rule_snapshot,
        write_f12_approved_export,
        write_f12_decisions,
        write_f12_review_pair,
        write_policy_facts,
        write_rule_batch,
        write_rule_snapshot,
    )
    from benefitfinder.infrastructure.youthcenter_http import YouthcenterHttpFetcher
    from benefitfinder.ingestion.collect import CollectionResult, collect_youthcenter
    from benefitfinder.ingestion.rule_review_workflow import (
        RuleReviewWorkflowError,
        approve_snapshot,
        inspect_snapshot,
        prepare_snapshot,
        refresh_snapshot,
    )
    from benefitfinder.ingestion.youthcenter import SOURCE_NAME, YOUTHCENTER_POLICY_LIST
    from benefitfinder.jobs.f12_review_display import format_f12_review
    from benefitfinder.jobs.daily_refresh import DailyRefreshError, run_daily_refresh
    from benefitfinder.jobs.rule_review_example import example_rule_batch
    from benefitfinder.jobs.youthcenter_refresh import (
        STATUS_FILE,
        RefreshError,
        refresh_notification,
        run_youthcenter_refresh,
    )

    from benefitfinder.ai.policy_explanation import GroundedPolicyExplainer
    from benefitfinder.application.policy_explanations import explain_policy
    from benefitfinder.infrastructure.ollama_explanations import OllamaExplanationCompletion
    from benefitfinder.infrastructure.published_clauses import InMemoryPublishedClauses
    from benefitfinder.jobs.explanation_example import explanation_example
    from benefitfinder.application.policy_analysis import AnalysisClaim, PolicyAnalysisInput, PolicyAnalysisStoreError
    from benefitfinder.application.policy_rules import PolicyReference, RuleReviewBatch
    from benefitfinder.application.run_policy_analysis import run_pending_analysis
    from benefitfinder.infrastructure.database.repositories.policy_analysis import PostgresPolicyAnalysisTasks
    from benefitfinder.infrastructure.policy_analysis_files import PolicyAnalysisFiles
    from benefitfinder.infrastructure.policy_analysis_inputs import load_policy_analysis_input
    from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash, validate_source_text
    from benefitfinder.infrastructure.database.repositories.published_policies import publish_policy
    from benefitfinder.infrastructure.database.repositories.policy_rechecks import list_policy_rechecks
    from benefitfinder.infrastructure.policy_recheck_files import write_recheck_page
    from benefitfinder.infrastructure.service_catalog_records import (
        ServiceCatalogRecord, service_record_hash, validate_service_sources,
    )
    from benefitfinder.infrastructure.database.repositories.published_services import publish_service
    from benefitfinder.application.prepare_policy_analysis import prepare_analysis_inputs
    from benefitfinder.infrastructure.policy_preparation_plans import load_preparation_plan
    from benefitfinder.infrastructure.database.repositories.analysis_sources import PostgresAnalysisSources
    from benefitfinder.ingestion.versioning import record_sha256
    from benefitfinder.ingestion.youthcenter import hashed_fields

    from benefitfinder.ai.decider_extraction import analyze_batch as analyze_decider_batch
    from benefitfinder.ingestion.decider_source_fields import prepare_source_fields
    from benefitfinder.application.decider import Compilation, Decisions, DocumentExtraction, canonical_hash
    from benefitfinder.application.decider_evaluation import evaluate_report, require_evaluation
    from benefitfinder.infrastructure.decider_client import (
        CheckpointDecider, HostedDecider, LocalDecider, private_json,
    )
    from benefitfinder.jobs.decider_batch import run_batch as run_decider_batch
    from benefitfinder.jobs.refresh_lock import acquire_refresh_lock
    from benefitfinder.infrastructure.database.repositories.decider_inputs import load_decider_corpus

API_KEY_ENV = "YOUTHCENTER_API_KEY"
KST = ZoneInfo("Asia/Seoul")

DECIDER_ANALYSIS_VERSION = 7


def _add_source_import_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--publish', action='store_true', help='새 원문만 미검수 상태로 적재')


def import_source_command(args: argparse.Namespace) -> int:
    """Assemble source-only import without constructing or loading model clients."""
    from tempfile import NamedTemporaryFile

    from benefitfinder.infrastructure.database.repositories.decider_inputs import load_decider_corpus
    from benefitfinder.infrastructure.database.repositories.source_catalog_import import (
        load_source_inventory, load_source_release_identities,
    )
    from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord
    from benefitfinder.infrastructure.unreviewed_catalog import publish_unreviewed, validate_unreviewed
    from benefitfinder.ingestion.decider_pending import compile_pending_source
    from benefitfinder.jobs.refresh_lock import acquire_refresh_lock
    from benefitfinder.jobs.source_catalog_import import run_source_catalog_import

    def save(path: Path, value: object) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                    prefix='.source-import-', delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(value, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
                handle.write('\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def load(path: Path) -> dict | None:
        if not path.exists():
            return None
        with path.open('rb') as handle:
            encoded = handle.read(2 * 1024 * 1024 + 1)
        if len(encoded) > 2 * 1024 * 1024:
            raise ValueError('source_import_checkpoint_size_exceeded')
        value = json.loads(encoded)
        if not isinstance(value, dict):
            raise ValueError('source_import_checkpoint_invalid')
        return value

    def validate(payload: dict, source: str) -> tuple[CatalogRecord, str]:
        record = CatalogRecord.model_validate(payload)
        return record, validate_unreviewed(record, source)

    try:
        publish_url = os.environ.get('POLICY_PUBLISH_DATABASE_URL', '').strip()
        read_url = publish_url or os.environ.get('F12_WRITE_DATABASE_URL', '').strip()
        if args.publish and not publish_url:
            raise ValueError('source_import_publish_database_required')
        if not read_url:
            raise ValueError('source_import_read_database_required')
        args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(args.output / '.source-import-lock',
                             os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            acquire_refresh_lock(descriptor)
            inventory = load_source_inventory(read_url)
            values, selection = load_decider_corpus(read_url)
            today = datetime.now(KST).date()
            report = run_source_catalog_import(
                values, root=args.output, inventory=inventory, selection=selection,
                compile_source=lambda value: compile_pending_source(value, as_of=today),
                validate=validate,
                load_existing=lambda ids, external: load_source_release_identities(read_url, ids, external),
                save=save, load=load,
                publish=(lambda entries: publish_unreviewed(publish_url, entries)) if args.publish else None,
            )
        finally:
            os.close(descriptor)
    except Exception as error:
        code = str(error)
        if not code.startswith('source_import_') or not code.replace('_', '').isalnum():
            code = 'source_import_failed'
        print(code, file=sys.stderr)
        return 2
    print(json.dumps({'mode': 'source_only', 'model_analysis': 'not_run',
                      'counts': report['counts'], 'reason_counts': report['reason_counts']},
                     ensure_ascii=False, sort_keys=True))
    return 1 if report['counts']['failed'] or report['counts']['held'] else 0


def source_cash_command(args: argparse.Namespace) -> int:
    """Compile current youthcenter cash facts without loading a model."""
    from benefitfinder.infrastructure.ai_catalog_review import AIReview, publish_ai_batch
    from benefitfinder.infrastructure.database.repositories.decider_inputs import load_decider_corpus
    from benefitfinder.infrastructure.database.repositories.source_catalog_import import (
        load_source_release_identities,
    )
    from benefitfinder.infrastructure.decider_client import private_json
    from benefitfinder.infrastructure.policy_catalog_records import (
        CatalogRecord, record_hash, validate_source_text,
    )
    from benefitfinder.infrastructure.region_codes import OfficialRegionCatalog
    from benefitfinder.ingestion.source_cash import compile_source_cash
    from benefitfinder.ingestion.source_cash_dynamic import compile_source_cash_dynamic
    from benefitfinder.ingestion.source_rent_cash import compile_source_rent_cash
    from benefitfinder.jobs.refresh_lock import acquire_refresh_lock
    from benefitfinder.jobs.source_cash_batch import (
        PreparedSourceCash, SOURCE_CASH_REVIEWER_AGENT, SOURCE_CASH_REVIEW_METADATA,
        SOURCE_CASH_SOURCE_VERSION, SOURCE_CASH_WRITER_AGENT,
        restore_checked_at_only, run_source_cash_batch,
        source_cash_review_timestamp,
    )

    def load_checkpoint(path: Path) -> dict | None:
        if not path.exists():
            return None
        with path.open("rb") as handle:
            encoded = handle.read(2 * 1024 * 1024 + 1)
        if len(encoded) > 2 * 1024 * 1024:
            raise ValueError("source_cash_batch_checkpoint_size_exceeded")
        value = json.loads(encoded)
        if not isinstance(value, dict):
            raise ValueError("source_cash_batch_checkpoint_invalid")
        return value

    try:
        database_url = os.environ.get("POLICY_PUBLISH_DATABASE_URL", "").strip()
        if not database_url:
            raise ValueError("source_cash_batch_database_url_required")
        args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            args.output / ".source-cash-lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            acquire_refresh_lock(descriptor)
            values, selection = load_decider_corpus(database_url)
            regions = OfficialRegionCatalog().get_regions()
            as_of = args.as_of or datetime.now(KST).date()

            def compile_source(value):
                target = next(policy for policy in value.policies if policy.id == value.policy_id)
                if "청년월세" in target.name.replace(" ", "") and "2차" in target.name:
                    return compile_source_rent_cash(value, as_of=as_of, regions=regions)
                result = compile_source_cash(value, as_of=as_of, regions=regions)
                if result.payload is None and args.applicant_cash_facts:
                    return compile_source_cash_dynamic(value, as_of=as_of, regions=regions)
                return result

            def prepare(compiled, source_text, local_date) -> PreparedSourceCash:
                record = CatalogRecord.model_validate(compiled.payload)
                digest = record_hash(record)
                validate_source_text(record, source_text)
                checked_at = datetime.fromisoformat(
                    record.benefit.source.checked_at.replace("Z", "+00:00")
                )
                reviewed_at = source_cash_review_timestamp(checked_at=checked_at)
                review = AIReview(
                    writer_agent=SOURCE_CASH_WRITER_AGENT,
                    reviewer_agent=SOURCE_CASH_REVIEWER_AGENT,
                    reviewer_model=SOURCE_CASH_REVIEW_METADATA,
                    verdict="pass",
                    reviewed_at=reviewed_at,
                    document_version=record.benefit.source.document_version,
                    source_sha256=record.source_sha256,
                    record_sha256=digest,
                )
                source_version = record.benefit.source.document_version.rsplit(":", 1)[-1]
                if source_version != SOURCE_CASH_SOURCE_VERSION:
                    raise ValueError("source_cash_batch_source_version_invalid")
                return PreparedSourceCash(
                    record.benefit.id, record.benefit.source.document_version, digest,
                    source_version, record, review, source_text,
                )

            import psycopg
            from psycopg.rows import dict_row

            # Load source-fact replay candidates together. Fresh identities are
            # still checked by the batch writer before every transaction.
            with psycopg.connect(
                database_url, connect_timeout=2, row_factory=dict_row,
                options="-c default_transaction_read_only=on -c statement_timeout=4000 "
                "-c timezone=UTC",
            ) as connection:
                replay_rows = connection.execute(
                    "SELECT policy_id,payload,approved_sha256,document_version,review_type,"
                    "review_status,publication_status FROM public.policy_catalog_releases "
                    "WHERE policy_id=ANY(%s) AND publication_status='published' "
                    "AND document_version LIKE %s",
                    ([value.policy_id for value in values], "%:source-facts-v1"),
                ).fetchmany(5001)
            if len(replay_rows) > 5000:
                raise ValueError("source_cash_batch_current_release_size_exceeded")
            replay_payloads = {
                (row["policy_id"], row["document_version"]): dict(row) for row in replay_rows
            }

            def load_current_release(policy_id: str, document_version: str) -> dict | None:
                return replay_payloads.get((policy_id, document_version))

            def timestamp_replay(prepared, current) -> bool:
                if (
                    current.get("publication_status") != "published"
                    or current.get("review_type") != "ai"
                    or current.get("review_status") != "approved"
                    or current.get("document_version") != prepared.document_version
                    or not isinstance(prepared.record, CatalogRecord)
                ):
                    return False
                try:
                    approved = CatalogRecord.model_validate(current["payload"])
                    approved_digest = current["approved_sha256"]
                    if record_hash(approved) != approved_digest:
                        return False
                    restored = restore_checked_at_only(
                        prepared.record.model_dump(mode="json"),
                        approved.model_dump(mode="json"),
                    )
                    if restored is None:
                        return False
                    return record_hash(CatalogRecord.model_validate(restored)) == approved_digest
                except (KeyError, TypeError, ValueError):
                    return False

            def publish(entries, expected_previous, allow_replace_ai, allow_replace_unreviewed):
                return publish_ai_batch(
                    database_url,
                    [(entry.record, entry.review, entry.source_text, None) for entry in entries],
                    allow_replace_ai=allow_replace_ai,
                    allow_replace_unreviewed=allow_replace_unreviewed,
                    expected_previous=dict(expected_previous),
                )

            report = run_source_cash_batch(
                values, output=args.output, as_of=as_of,
                compile_source=compile_source, prepare=prepare,
                load_existing=lambda ids, external: load_source_release_identities(
                    database_url, ids, external
                ),
                load_current_release=load_current_release,
                timestamp_replay=timestamp_replay,
                publish=publish if args.publish else None,
                save=private_json, load=load_checkpoint, selection=selection,
                batch_size=args.batch_size,
            )
        finally:
            os.close(descriptor)
    except Exception as error:
        code = str(error)
        allowed = code.startswith("source_cash_batch_") and code.replace("_", "").isalnum()
        print(code if allowed else "source_cash_batch_failed", file=sys.stderr)
        return 2

    print(json.dumps({
        "mode": report["mode"], "dry_run": report["dry_run"],
        "source_version": report["source_version"],
        "review_metadata": report["review_metadata"],
        "model_calls": report["model_calls"], "ai_review_calls": report["ai_review_calls"],
        "ai_verification": report["ai_verification"],
        "counts": report["counts"], "reason_counts": report["reason_counts"],
    }, ensure_ascii=False, sort_keys=True))
    return 1 if report["counts"]["failed"] or report["counts"]["held"] else 0


def _analysis_version() -> int:
    engine = os.environ.get('F12_ANALYSIS_ENGINE', 'tools')
    if engine not in {'tools', 'decider'}:
        raise ValueError('invalid_f12_analysis_engine')
    return DECIDER_ANALYSIS_VERSION if engine == 'decider' else 4


@contextmanager
def _decider_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(root / '.batch-lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        acquire_refresh_lock(descriptor)
        yield
    finally:
        os.close(descriptor)


def _decider_client(root: Path) -> tuple[LocalDecider | HostedDecider, CheckpointDecider]:
    provider = os.environ.get('DECIDER_PROVIDER', 'local')
    if provider == 'local':
        raw = LocalDecider(Path(os.environ.get('DECIDER_CHECKPOINT', 'data/local/decider/checkpoint')),
            max_length=int(os.environ.get('DECIDER_MAX_LENGTH', '3072')),
            batch_tokens=int(os.environ.get('DECIDER_BATCH_TOKENS', '2048')),
            mode=os.environ.get('DECIDER_MODE', 'separate'),
            locate_threshold=float(os.environ.get('DECIDER_LOCATE_THRESHOLD', '0.5')))
    elif provider == 'external_api':
        raw = HostedDecider(os.environ.get('DECIDER_ENDPOINT_URL', ''),
            os.environ.get('DECIDER_API_KEY', ''), budget_path=root / 'api-budget.json',
            daily_calls=int(os.environ.get('DECIDER_DAILY_CALLS', '100')))
    else:
        raise ValueError('decider_provider_required')
    return raw, CheckpointDecider(raw, root / 'responses')


def _decider_publisher(client: Decisions, *, tasks: PostgresPolicyAnalysisTasks | None = None,
                      claims: dict[str, AnalysisClaim] | None = None
                      ) -> Callable[[dict, PolicyAnalysisInput], str]:
    evaluation_path = os.environ.get('DECIDER_EVALUATION_PATH')
    if not evaluation_path:
        raise ValueError('decider_evaluation_required')
    require_evaluation(json.loads(Path(evaluation_path).read_text()), client.identity)
    database_url = os.environ.get('POLICY_PUBLISH_DATABASE_URL', '')
    if not database_url:
        raise ValueError('decider_publication_database_required')

    def publish(payload: dict, value: PolicyAnalysisInput) -> str:
        if tasks is not None and (claims is None or not tasks.is_current(claims[value.policy_id])):
            raise ValueError('decider_source_changed')
        record = CatalogRecord.model_validate(payload)
        source = value.documents[0].text
        validate_source_text(record, source)
        review = AIReview(writer_agent='decider-locate:' + client.identity['questions'],
            reviewer_agent='decider-followup-code:' + client.identity['questions'],
            reviewer_model=client.identity['id'] + '@' + client.identity['revision'],
            verdict='pass', reviewed_at=datetime.now(timezone.utc),
            document_version=record.benefit.source.document_version,
            source_sha256=record.source_sha256, record_sha256=record_hash(record))
        result = publish_ai_batch(database_url, [(record, review, source, None)])
        return next(key for key in ('published', 'replayed', 'replaced') if result.get(key))
    return publish


def _compile_decider(value: PolicyAnalysisInput, extracted: tuple[DocumentExtraction, ...]) -> Compilation:
    from benefitfinder.ingestion.decider_normalization import compile_policy

    result = compile_policy(value, extracted)
    if result.payload is not None:
        record = CatalogRecord.model_validate(result.payload)
        validate_source_text(record, value.documents[0].text)
    return result


def _run_decider_values(values: Sequence[PolicyAnalysisInput], client: Decisions, root: Path, *,
                       publish: Callable[[dict, PolicyAnalysisInput], str] | None = None) -> dict:
    def analyze(values, client, **settings):
        return analyze_decider_batch(values, client, prepare_source=prepare_source_fields,
            max_policy_requests=int(os.environ.get('DECIDER_MAX_POLICY_REQUESTS', '32')),
            max_policy_characters=int(os.environ.get('DECIDER_MAX_POLICY_CHARACTERS', '40000')),
            **settings)

    return run_decider_batch(tuple(values), client, root=root,
        analyze=analyze, compile_policy=_compile_decider, save=private_json,
        publish=publish, batch_size=int(os.environ.get('DECIDER_BATCH_SIZE', '4')),
        window_chars=int(os.environ.get('DECIDER_WINDOW_CHARS', '900')))


def run_decider_queue(data_dir: Path, *, limit: int = 20, daily: bool = False,
                      publish: bool | None = None) -> dict:
    """Reuse F12 leases and source invalidation; source/LLM work is never online."""
    if not 1 <= limit <= 10000:
        raise ValueError('decider_invalid_limit')
    if daily and os.environ.get('DECIDER_PROVIDER') != 'external_api':
        raise ValueError('decider_daily_requires_external_api')
    root = data_dir / 'processed' / 'decider'
    tasks = PostgresPolicyAnalysisTasks(os.environ.get('F12_WRITE_DATABASE_URL', ''),
                                       analysis_version=DECIDER_ANALYSIS_VERSION)
    combined = Counter(dict.fromkeys(('targets', 'published', 'replayed', 'replaced',
                                      'dry_run_ready', 'held', 'failed'), 0))
    with _decider_lock(root):
        _raw, client = _decider_client(root)
        # Validate all operational settings before taking a queue lease.
        auto = os.environ.get('DECIDER_AUTO_PUBLISH', 'false') if publish is None else str(publish).lower()
        if auto not in {'true', 'false'}:
            raise ValueError('decider_invalid_auto_publish')
        if auto == 'true':
            _decider_publisher(client)
        attempted = 0
        while attempted < limit:
            group = []
            for _ in range(min(int(os.environ.get('DECIDER_BATCH_SIZE', '4')), limit - attempted)):
                claim = tasks.claim()
                if claim is None:
                    break
                group.append(claim)
            if not group:
                break
            claims = {claim.input.policy_id: claim for claim in group}
            stop = threading.Event()

            def renew() -> None:
                while not stop.wait(30):
                    for claim in group:
                        try:
                            tasks.renew(claim)
                        except Exception:
                            pass  # is_current and complete still fail closed.

            heartbeat = threading.Thread(target=renew, daemon=True)
            heartbeat.start()
            try:
                report = _run_decider_values([claim.input for claim in group], client, root,
                    publish=_decider_publisher(client, tasks=tasks, claims=claims)
                    if auto == 'true' else None)
                for entry in report['entries']:
                    claim = claims[entry['policy_id']]
                    if entry['status'] == 'failed':
                        tasks.fail(claim)
                        continue
                    reference = f'f12-analysis/{claim.task_id}/{claim.token}'
                    private_json(data_dir / reference / 'decider-summary.json', entry)
                    tasks.complete(claim, output_ref=reference, output_sha256=canonical_hash(entry),
                                   summary={'engine': 'decider', 'status': entry['status'],
                                            'reasons': entry['reasons']})
                combined.update(report['counts'])
            except Exception:
                for claim in group:
                    tasks.fail(claim)
                combined['failed'] += len(group)
                combined['targets'] += len(group)
            finally:
                stop.set()
                heartbeat.join(timeout=1)
            attempted += len(group)
        summary = {'format_version': 1, 'model': client.identity, 'counts': dict(combined)}
        private_json(root / 'queue-summary.json', summary)
    return dict(combined)


def decider_command(args: argparse.Namespace) -> int:
    try:
        if args.decider_action == 'export':
            values, selection = load_decider_corpus(os.environ.get('F12_WRITE_DATABASE_URL', ''))
            inputs = []
            tasks = PostgresPolicyAnalysisTasks(os.environ.get('F12_WRITE_DATABASE_URL', ''),
                analysis_version=DECIDER_ANALYSIS_VERSION) if args.register else None
            for value in values:
                name = 'inputs/' + value.fingerprint() + '.json'
                private_json(args.output / name, {'format_version': 1, **value.payload()})
                inputs.append(name)
                if tasks is not None:
                    tasks.register_prepared(value)
            private_json(args.output / 'manifest.json', {'format_version': 1, 'inputs': inputs})
            private_json(args.output / 'selection.json', selection)
            print(json.dumps(selection, sort_keys=True))
            return 1 if selection['counts']['held'] else 0
        if args.decider_action == 'evaluate':
            report = json.loads(args.report.read_text())
            extractions = {entry['policy_id']: json.loads((Path(entry['artifact']) / 'extraction.json').read_text())
                for entry in report['entries'] if (Path(entry['artifact']) / 'extraction.json').exists()}
            result = evaluate_report(report, json.loads(args.gold.read_text()), extractions)
            private_json(args.output, result)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result['eligible_for_publication'] else 1
        if args.queue:
            result = run_decider_queue(args.data_dir, limit=args.limit, publish=args.publish)
            print(json.dumps(result, sort_keys=True))
            return 1 if result.get('failed', 0) or result.get('held', 0) else 0
        manifest = json.loads(args.manifest.read_text())
        if (manifest.get('format_version') != 1 or not isinstance(manifest.get('inputs'), list)
            or not 1 <= len(manifest['inputs']) <= 10000):
            raise ValueError('decider_invalid_manifest')
        values = [load_policy_analysis_input(args.manifest.parent / path) for path in manifest['inputs']]
        root = args.data_dir / 'processed' / 'decider'
        with _decider_lock(root):
            raw, client = _decider_client(root)
            report = _run_decider_values(values, client, root,
                publish=_decider_publisher(client) if args.publish else None)
            if isinstance(raw, LocalDecider):
                private_json(root / 'gpu-metrics.json', raw.metrics)
        print(json.dumps({'counts': report['counts'], 'reason_counts': report['reason_counts']}, sort_keys=True))
        return 1 if report['counts']['failed'] or report['counts']['held'] else 0
    except Exception:
        print('decider_batch_failed: 설정·비공개 실행 기록을 확인하세요.', file=sys.stderr)
        return 2


def prepare_f12_inputs(plan_path: Path) -> dict[str, int]:
    plan = load_preparation_plan(plan_path)
    url = os.environ.get('F12_WRITE_DATABASE_URL', '')
    tasks = PostgresPolicyAnalysisTasks(url, analysis_version=_analysis_version())
    official = {}
    if any(target.official_documents for target in plan.targets):
        from benefitfinder.ingestion.official_documents import official_document_material_hash

        official = {'official_sources': PostgresAnalysisSources(url, source='official_documents'),
                    'official_material_hash': official_document_material_hash}
    if plan.source == 'gov24':
        from benefitfinder.ingestion.gov24 import analysis_fields, gov24_hashed_fields

        return prepare_analysis_inputs(plan, PostgresAnalysisSources(url, source='gov24'),
            tasks.register_prepared, lambda fields: record_sha256(gov24_hashed_fields(fields)), analysis_fields,
            **official)
    return prepare_analysis_inputs(plan, PostgresAnalysisSources(url), tasks.register_prepared,
        lambda fields: record_sha256(hashed_fields(YOUTHCENTER_POLICY_LIST, fields)), **official)


def run_f12_queue(data_dir: Path, *, limit: int = 5) -> dict[str, object]:
    if _analysis_version() == DECIDER_ANALYSIS_VERSION:
        return run_decider_queue(data_dir, limit=limit)
    # Lazy import keeps non-LLM CLI commands independent of model initialization.
    from benefitfinder.ai.policy_analysis_agent import analyze_policy

    settings = LLMSettings.from_env()
    if settings.provider == 'disabled':
        raise ValueError('llm_not_configured')
    chat = CompatibleChat(settings.endpoint(), require_tool_calls=True)
    tasks = PostgresPolicyAnalysisTasks(os.environ.get('F12_WRITE_DATABASE_URL', ''))

    def analyze(claim, on_step):
        deadline = monotonic() + 300

        def guarded_chat(messages, tools):
            if not tasks.is_current(claim):
                raise PolicyAnalysisStoreError('f12_source_changed')
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ValueError('f12_analysis_timeout')
            return chat(messages, tools, timeout_seconds=min(45, remaining))

        return analyze_policy(claim.input, chat=guarded_chat, budget_seconds=300, on_step=on_step)

    def write_reviews(directory, claim, result):
        policies = tuple(PolicyReference(policy.id, 'youth', policy.categories)
                         for policy in claim.input.policies)
        for index, facts in enumerate(result.facts, 1):
            rules = tuple(rule for rule in result.rules
                          if {cite.document_id for cite in rule.citations} == {facts.document.id})
            if rules or facts.eligibility or facts.amounts or facts.payment_timing:
                snapshot = prepare_snapshot(RuleReviewBatch((facts.document,), policies, rules),
                                            at=datetime.now(KST))
                write_f12_review_pair(directory / f'd{index:02}', snapshot, facts)

    counts = run_pending_analysis(tasks, PolicyAnalysisFiles(data_dir, write_reviews, model={
        'provider': settings.provider, 'name': settings.model,
    }),
                                  analyze, limit=limit)
    return {**counts, **tasks.status()}


def f12_analysis_command(args: argparse.Namespace) -> int:
    try:
        tasks = PostgresPolicyAnalysisTasks(os.environ.get('F12_WRITE_DATABASE_URL', ''),
                                           analysis_version=_analysis_version())
        if args.analysis_action == 'prepare':
            result = prepare_f12_inputs(args.plan)
        elif args.analysis_action == 'register':
            result = tasks.register(load_policy_analysis_input(args.input),
                                    origin_recheck_id=args.recheck_id)
        elif args.analysis_action == 'status':
            result = tasks.get_task(args.task_id) if args.task_id is not None else tasks.status()
        else:
            result = run_f12_queue(args.data_dir, limit=args.limit)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1 if isinstance(result, dict) and result.get('failed', 0) else 0
    except Exception:
        print('F12 처리 실패: 입력·DB·LLM 설정과 로컬 실행 기록을 확인하세요.', file=sys.stderr)
        return 2


def f12_catalog_command(args: argparse.Namespace) -> int:
    """Prepare a review-bound release without creating approval or touching the DB."""
    try:
        record = prepare_f12_release(
            pair_dir=args.pair_dir, decisions_path=args.decisions, approved_path=args.approved,
            record_path=args.input, plan_path=args.plan, source_text=args.source.read_text(encoding='utf-8'),
            price_source_text=args.price_source.read_text(encoding='utf-8') if args.price_source else None,
        )
        write_f12_catalog(args.output, record)
        digest = service_record_hash(record) if isinstance(record, ServiceCatalogRecord) else record_hash(record)
        print(json.dumps({'record_sha256': digest, 'policy_id': record.benefit.id,
                          'requires_publication_approval': True}, ensure_ascii=False))
        return 0
    except Exception:
        print('F12 게시 입력 준비 실패: 원본 후보·최신 검수·내보내기·실행 규칙과 새 출력 경로를 확인하세요.', file=sys.stderr)
        return 2


def ai_catalog_command(args: argparse.Namespace) -> int:
    """Validate, publish, or retire one explicitly specified AI-reviewed batch."""
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, list) or not 1 <= len(manifest) <= 50:
            raise ValueError("invalid_ai_batch_manifest")
        entries = []
        for item in manifest:
            if not isinstance(item, dict) or not {"input", "review", "source"} <= set(item) or set(item) - {"input", "review", "source", "price_source"}:
                raise ValueError("invalid_ai_batch_entry")
            base = args.manifest.parent
            data = json.loads((base / item["input"]).read_text(encoding="utf-8"))
            model = ServiceCatalogRecord if data.get("kind") == "service" else CatalogRecord
            record = model.model_validate(data)
            review = AIReview.model_validate_json((base / item["review"]).read_text(encoding="utf-8"))
            source = (base / item["source"]).read_text(encoding="utf-8")
            price = (base / item["price_source"]).read_text(encoding="utf-8") if item.get("price_source") else None
            entries.append((record, review, source, price))
        if args.catalog_action == "validate":
            for record, review, source, price in entries:
                validate_ai_review(record, review, source_text=source, price_source_text=price)
            print(json.dumps({"validated": len(entries), "published": False}))
            return 0
        database_url = os.environ.get("POLICY_PUBLISH_DATABASE_URL", "")
        if not database_url:
            raise ValueError("missing_publish_database_url")
        if args.catalog_action == "publish":
            publish_ai_batch(database_url, entries)
        else:
            retire_ai_batch(database_url, [(record.benefit.id, record.benefit.source.document_version,
                                          review.record_sha256) for record, review, _, _ in entries])
    except Exception:
        print("AI 카탈로그 처리 실패: 입력·원천·검수·DB 상태를 확인하세요.", file=sys.stderr)
        return 2
    print(json.dumps({"action": args.catalog_action, "count": len(entries)}))
    return 0


def unreviewed_catalog_command(args: argparse.Namespace) -> int:
    """Handle a manifest without inventing review metadata."""
    if args.catalog_action == 'retire-exact':
        from benefitfinder.infrastructure.unreviewed_catalog import retire_unreviewed_releases
        try:
            releases = json.loads(args.releases.read_text(encoding='utf-8'))
            if not isinstance(releases, list) or any(
                not isinstance(item, dict) or set(item) != {'policy_id', 'document_version', 'approved_sha256'}
                for item in releases
            ):
                raise ValueError('invalid_unreviewed_releases_file')
            database_url = os.environ.get('POLICY_PUBLISH_DATABASE_URL', '')
            if not database_url:
                raise ValueError('missing_publish_database_url')
            retire_unreviewed_releases(database_url, [
                (item['policy_id'], item['document_version'], item['approved_sha256']) for item in releases
            ])
        except Exception:
            print('미검수 게시본 철회 실패: 철회 목록과 현재 게시 상태·DB 설정을 확인하세요.', file=sys.stderr)
            return 2
        print(json.dumps({'action': 'retire-exact', 'count': len(releases)}))
        return 0
    try:
        manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
        if not isinstance(manifest, list) or not 1 <= len(manifest) <= 50:
            raise ValueError('invalid_unreviewed_manifest')
        entries = []
        for item in manifest:
            if not isinstance(item, dict) or set(item) != {'input', 'source'}:
                raise ValueError('invalid_unreviewed_entry')
            data = json.loads((args.manifest.parent / item['input']).read_text(encoding='utf-8'))
            model = ServiceCatalogRecord if data.get('kind') == 'service' else CatalogRecord
            entries.append((model.model_validate(data), (args.manifest.parent / item['source']).read_text(encoding='utf-8')))
        if args.catalog_action == 'validate':
            for record, source in entries:
                validate_unreviewed(record, source)
        else:
            database_url = os.environ.get('POLICY_PUBLISH_DATABASE_URL', '')
            if not database_url:
                raise ValueError('missing_publish_database_url')
            (publish_unreviewed if args.catalog_action == 'publish' else retire_unreviewed)(database_url, entries)
    except Exception:
        print('미검수 카탈로그 처리 실패: 입력·원천·DB 상태를 확인하세요.', file=sys.stderr)
        return 2
    print(json.dumps({'action': args.catalog_action, 'count': len(entries)}))
    return 0


def service_source_facts_command(args: argparse.Namespace) -> int:
    """Add proven residence/age rules and closed status to service records offline.

    Input is an ``unreviewed-catalog`` manifest, or ``--from-database`` to rebuild the
    published unreviewed services read-only. For changed records only, writes
    ``before/releases.json`` (exact current releases for ``unreviewed-catalog
    retire-exact``) and ``after/manifest.json`` (new versions for ``unreviewed-catalog
    publish``) under ``--output``. A record whose publication-time projection can no
    longer be rebuilt is re-hashed against the current projection only when every
    citation is still present. It never writes the database.
    """
    import hashlib

    from benefitfinder.infrastructure.region_codes import OfficialRegionCatalog
    from benefitfinder.ingestion.service_source_facts import bind_service_source_facts

    try:
        skipped: list[dict[str, str]] = []
        loaded: list[tuple] = []  # (record, source, approved_sha256, rebased)
        if args.from_database:
            import psycopg
            from benefitfinder.infrastructure.unreviewed_catalog import load_published_unreviewed_services

            database_url = os.environ.get(args.database_url_env, '').strip()
            if not database_url:
                raise ValueError('missing_service_facts_database_url')
            with psycopg.connect(database_url, connect_timeout=10,
                                 options='-c default_transaction_read_only=on') as connection:
                services, skipped = load_published_unreviewed_services(connection)
            loaded = [(item.record, item.source, item.approved_sha256, item.rebased) for item in services]
        else:
            manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
            if not isinstance(manifest, list) or not 1 <= len(manifest) <= 50:
                raise ValueError('invalid_service_facts_manifest')
            for item in manifest:
                if not isinstance(item, dict) or set(item) != {'input', 'source'}:
                    raise ValueError('invalid_service_facts_entry')
                data = json.loads((args.manifest.parent / item['input']).read_text(encoding='utf-8'))
                if data.get('kind') != 'service':
                    raise ValueError('service_facts_requires_service_record')
                record = ServiceCatalogRecord.model_validate(data)
                source = (args.manifest.parent / item['source']).read_text(encoding='utf-8')
                validate_service_sources(record, source, None)
                loaded.append((record, source, service_record_hash(record), False))
        if not 1 <= len(loaded) <= 50:
            raise ValueError('service_facts_batch_size')
        regions = OfficialRegionCatalog().get_regions()
        before, after, summary = [], [], []
        for record, source, approved, rebased in loaded:
            payload = record.model_dump(mode='json')
            if rebased:
                payload['source_sha256'] = hashlib.sha256(source.encode('utf-8')).hexdigest()
            facts = bind_service_source_facts(payload, source, regions=regions)
            summary.append({'policy_id': record.benefit.id, 'title': record.benefit.title,
                            'changed': facts.changed, 'rebased': rebased,
                            'added_conditions': list(facts.added_condition_ids), 'closed': facts.closed})
            if facts.changed:
                bound = ServiceCatalogRecord.model_validate(facts.payload)
                validate_service_sources(bound, source, None)
                before.append({'policy_id': record.benefit.id,
                               'document_version': record.benefit.source.document_version,
                               'approved_sha256': approved})
                after.append((bound, source))
        if args.output is not None:
            args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
            if after:
                (args.output / 'before').mkdir(mode=0o700)
                (args.output / 'before' / 'releases.json').write_text(
                    json.dumps(before, indent=2), encoding='utf-8')
                directory = args.output / 'after'
                directory.mkdir(mode=0o700)
                manifest = []
                for index, (bound, source) in enumerate(after):
                    name = f'{index:03d}'
                    (directory / f'{name}.json').write_text(
                        json.dumps(bound.model_dump(mode='json'), ensure_ascii=False, indent=2),
                        encoding='utf-8')
                    (directory / f'{name}.source.txt').write_text(source, encoding='utf-8')
                    manifest.append({'input': f'{name}.json', 'source': f'{name}.source.txt'})
                (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    except Exception:
        print('서비스 원천 조건 연결 실패: 입력·원천·지역 목록·DB 설정과 새 출력 경로를 확인하세요.', file=sys.stderr)
        return 2
    print(json.dumps({'count': len(summary), 'changed': len(after), 'skipped': skipped,
                      'records': summary}, ensure_ascii=False))
    return 0


def policy_catalog_command(args: argparse.Namespace) -> int:
    """Validate a curated release; publish only with a separate human approval file."""
    try:
        record = CatalogRecord.model_validate_json(args.input.read_text(encoding="utf-8"))
        source = args.source.read_text(encoding="utf-8")
        validate_source_text(record, source)
        digest = record_hash(record)
        if args.catalog_action == "validate":
            print(json.dumps({"record_sha256": digest, "policy_id": record.benefit.id,
                              "document_version": record.benefit.source.document_version}, ensure_ascii=False))
            return 0
        approval = json.loads(args.approval.read_text(encoding="utf-8"))
        if set(approval) != {"record_sha256", "reviewed_by", "reviewed_at"}:
            raise ValueError("invalid_approval_fields")
        database_url = os.environ.get("POLICY_PUBLISH_DATABASE_URL", "")
        if not database_url:
            raise ValueError("missing_publish_database_url")
        publish_policy(
            database_url, record, source_text=source, approved_sha256=approval["record_sha256"],
            reviewer=approval["reviewed_by"],
            reviewed_at=datetime.fromisoformat(approval["reviewed_at"].replace("Z", "+00:00")),
        )
    except Exception:
        # Validation/driver errors may contain original text, credentials or local paths.
        print("정책 카탈로그 처리 실패: 입력·근거·승인 파일과 DB 설정을 확인해 주세요.", file=sys.stderr)
        return 2
    print("승인된 정책 버전을 게시했습니다.")
    return 0


def service_catalog_command(args: argparse.Namespace) -> int:
    """Validate service/price sources; publish only a separately supplied approval digest."""
    try:
        record = ServiceCatalogRecord.model_validate_json(args.input.read_text(encoding='utf-8'))
        source = args.source.read_text(encoding='utf-8')
        price_source = args.price_source.read_text(encoding='utf-8') if args.price_source is not None else None
        validate_service_sources(record, source, price_source)
        digest = service_record_hash(record)
        if args.catalog_action == 'validate':
            print(json.dumps({'record_sha256': digest, 'policy_id': record.benefit.id,
                              'document_version': record.benefit.source.document_version}))
            return 0
        approval = json.loads(args.approval.read_text(encoding='utf-8'))
        if set(approval) != {'record_sha256', 'reviewed_by', 'reviewed_at'}:
            raise ValueError('invalid_approval_fields')
        database_url = os.environ.get('POLICY_PUBLISH_DATABASE_URL', '')
        if not database_url:
            raise ValueError('missing_publish_database_url')
        publish_service(database_url, record, source_text=source, price_source_text=price_source,
                        approved_sha256=approval['record_sha256'], reviewer=approval['reviewed_by'],
                        reviewed_at=datetime.fromisoformat(approval['reviewed_at']))
    except Exception:
        print('서비스 카탈로그 처리 실패: 공고·단가 근거·승인 파일과 DB 설정을 확인하세요.', file=sys.stderr)
        return 2
    print('승인된 서비스 버전을 게시했습니다.')
    return 0


def policy_rechecks_command(args: argparse.Namespace) -> int:
    """Export an immutable pending handoff with a dedicated read-only role."""
    try:
        page = list_policy_rechecks(
            os.environ.get("POLICY_RECHECK_DATABASE_URL", ""),
            after_id=args.after_id, limit=args.limit,
        )
        write_recheck_page(args.output, page)
    except Exception:
        print("재검수 목록 처리 실패: DB 설정·커서·새 출력 경로를 확인해 주세요.", file=sys.stderr)
        return 2
    print(json.dumps({"exported_count": len(page["items"]),
                      "next_after_id": page["next_after_id"]}))
    return 0


def extraction_chat(model: str | None):
    settings = LLMSettings.from_env()
    if settings.provider != "disabled":
        return CompatibleChat(settings.endpoint(model=model))
    if model is not None:
        return OllamaChat(model)
    raise ValueError("llm_not_configured")


def chunk_chat():
    settings = LLMSettings.from_env()
    if settings.provider == "disabled":
        return LocalE4BChat()
    chat = CompatibleChat(settings.endpoint())

    def complete(messages, visible_lines):
        return json.dumps(
            chat.complete_json(
                messages, evidence_schema(visible_lines), timeout_seconds=60, max_tokens=4096
            ),
            ensure_ascii=False,
        )

    return complete

def refresh_ecos_rate_command() -> int:
    """Fetch once, then atomically replace only a successful ECOS observation."""
    try:
        api_key = os.environ.get("ECOS_API_KEY", "")
        database_url = os.environ.get("BASE_RATE_WRITE_DATABASE_URL", "")
        if not api_key or not database_url:
            raise ValueError("missing_base_rate_settings")
        observation = EcosBaseRate(api_key).fetch()
        PostgresBaseRates(database_url).save(observation)
    except Exception:
        # HTTP/DB errors can include a key in a URL or a password in a DSN.
        print("기준금리 갱신 실패: ECOS·DB 설정을 확인하세요. 마지막 성공값은 유지됩니다.",
              file=sys.stderr)
        return 2
    print("ECOS 기준금리 갱신 완료")
    return 0


def collect_youthcenter_command(data_dir: Path) -> int:
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        print(f"환경 변수 {API_KEY_ENV}에 인증키를 설정하세요.", file=sys.stderr)
        return 2

    raw_root = data_dir / "raw"
    processed_root = data_dir / "processed"
    versions = JsonFileVersionStore(processed_root)
    result = collect_youthcenter(
        YOUTHCENTER_POLICY_LIST,
        YouthcenterHttpFetcher(api_key),
        JsonFileSnapshotStore(raw_root, private=True),
        versions,
        lambda: datetime.now(KST),
    )

    records = versions.load_records(SOURCE_NAME)
    output_dir = processed_root / SOURCE_NAME
    write_records_csv(records, output_dir / "records.csv")
    write_field_summary_csv(records, output_dir / "fields.csv")

    kinds = Counter(change.kind.value for change in result.changes)
    print(f"수집 레코드 {len(result.records)}건, 저장된 전체 {len(records)}건")
    print("변경: " + ", ".join(f"{kind} {count}" for kind, count in sorted(kinds.items())))
    print(f"결과 파일: {output_dir}")
    for error in result.errors:
        target = f" ID {error.external_id}" if error.external_id else ""
        print(f"오류: {error.page}페이지{target} {error.code} ({error.detail})", file=sys.stderr)
    if not result.complete:
        print("일부 실패: 성공한 레코드만 저장했고 삭제 감지는 하지 않았습니다.", file=sys.stderr)
        return 1
    print("전체 수집 완료")
    return 0


def _refresh_youthcenter(data_dir: Path) -> int:
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        print(f"환경 변수 {API_KEY_ENV}에 인증키를 설정하세요.", file=sys.stderr)
        return 2

    database_enabled = "COLLECTION_WRITE_DATABASE_URL" in os.environ
    if database_enabled and not os.environ.get("COLLECTION_WRITE_DATABASE_URL", "").strip():
        print("수집 DB 인계 실패: collection_database_url_missing", file=sys.stderr)
        return 2

    root = data_dir / "processed" / "f08-youthcenter"
    fetcher = YouthcenterHttpFetcher(api_key)
    snapshots = JsonFileSnapshotStore(data_dir / "raw" / "f08-youthcenter", private=True)

    def clock() -> datetime:
        return datetime.now(KST)

    def build(staging: Path, previous: Path | None) -> CollectionResult:
        versions = JsonFileVersionStore(staging)
        records_path = versions.records_path(SOURCE_NAME)
        if previous is not None:
            records_path.parent.mkdir(parents=True)
            shutil.copyfile(JsonFileVersionStore(previous).records_path(SOURCE_NAME), records_path)

        result = collect_youthcenter(YOUTHCENTER_POLICY_LIST, fetcher, snapshots, versions, clock)
        if result.complete:
            records = versions.load_records(SOURCE_NAME)
            if not records_path.exists():  # 첫 정상 응답이 0건이면 저장할 레코드가 없다.
                records_path.parent.mkdir(parents=True, exist_ok=True)
                records_path.write_text("{}\n", encoding="utf-8")
            write_records_csv(records, records_path.with_name("records.csv"))
            write_field_summary_csv(records, records_path.with_name("fields.csv"))
        return result

    try:
        complete = run_youthcenter_refresh(
            root,
            build,
            clock,
            ingest_complete=(
                (lambda: ingest_youthcenter_command(data_dir) == 0) if database_enabled else None
            ),
        )
    except RefreshError as exc:
        print(f"온통청년 갱신 실패: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("온통청년 갱신 실패: refresh_io_error", file=sys.stderr)
        return 1
    if not complete:
        print(f"온통청년 갱신 실패. 마지막 성공본 유지: {root / STATUS_FILE}", file=sys.stderr)
        return 1
    print(f"온통청년 갱신 완료: {root / STATUS_FILE}")
    if not database_enabled:
        print("수집 DB 미설정: 완전한 파일 성공본만 보존했습니다.")
    return 0


def ingest_youthcenter_command(data_dir: Path) -> int:
    """Replay the last complete local generation without fetching or approving."""
    database_url = os.environ.get("COLLECTION_WRITE_DATABASE_URL", "").strip()
    if not database_url:
        print("수집 DB 인계 실패: collection_database_url_missing", file=sys.stderr)
        return 2
    try:
        batch = load_youthcenter_generation(
            lambda relative, limit: read_collection_file(data_dir, relative, limit)
        )
        result = PostgresCollectedPolicies(database_url).save(batch)
    except Exception:
        # Driver/validation exceptions can include DSNs, source fields or paths.
        print("수집 DB 인계 실패: 성공본·원본·DB 설정을 확인하세요.", file=sys.stderr)
        return 1
    print(
        f"수집 DB 인계 완료: 신규 {result.new}건, 변경 {result.changed}건, "
        f"동일 {result.unchanged}건, 누락 {result.missing}건"
    )
    if result.replayed:
        print("이미 인계한 같은 생성본입니다. DB 성공본과 변경 이력은 유지했습니다.")
    return 0


def ingest_gov24_command(data_dir: Path) -> int:
    """Verify all preserved API responses before replaying a complete generation."""
    from benefitfinder.ingestion.gov24_generations import load_gov24_generation

    database_url = os.environ.get('COLLECTION_WRITE_DATABASE_URL', '').strip()
    if not database_url:
        print('보조금24 DB 인계 실패: collection_database_url_missing', file=sys.stderr)
        return 2
    try:
        batch = load_gov24_generation(
            lambda relative, limit: read_collection_file(data_dir, relative, limit))
        result = PostgresCollectedPolicies(database_url).save(batch)
    except Exception:
        print('보조금24 DB 인계 실패: 성공본·원본·DB 설정을 확인하세요.', file=sys.stderr)
        return 1
    print(f'보조금24 DB 인계 완료: 신규 {result.new}건, 변경 {result.changed}건, '
          f'동일 {result.unchanged}건, 누락 {result.missing}건')
    return 0


def refresh_gov24_command(data_dir: Path, plan_path: Path) -> int:
    """Collect only explicitly mapped youth employment/housing service IDs."""
    from benefitfinder.infrastructure.gov24_http import Gov24HttpFetcher
    from benefitfinder.ingestion.gov24 import collect_gov24

    try:
        plan = load_preparation_plan(plan_path)
        if plan.source != 'gov24':
            raise ValueError
        api_key = os.environ.get('GOV24_API_KEY', '').strip()
        if not api_key:
            raise ValueError
        database_enabled = 'COLLECTION_WRITE_DATABASE_URL' in os.environ
        if database_enabled and not os.environ['COLLECTION_WRITE_DATABASE_URL'].strip():
            raise ValueError
        external_ids = tuple(sorted({doc.external_id for target in plan.targets
                                     for doc in target.documents}))
        if len(external_ids) > 100:
            raise ValueError
        category_values = dict(plan.scope.category_values)
        scope = json.dumps({'external_ids': external_ids, 'category_values': category_values},
                           ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        root = data_dir / 'processed' / 'f08-gov24'
        fetcher = Gov24HttpFetcher(api_key)
        snapshots = JsonFileSnapshotStore(data_dir / 'raw' / 'f08-gov24')

        def clock() -> datetime:
            return datetime.now(KST)

        def build(staging: Path, previous: Path | None) -> CollectionResult:
            # A changed selection must not turn formerly selected sources into
            # deletions. Use a new data root for a deliberate mapping migration.
            scope_path = root / 'scope.json'
            try:
                with scope_path.open('rb') as handle:
                    if handle.read(256 * 1024 + 1) != scope:
                        raise ValueError('gov24_collection_scope_changed')
            except FileNotFoundError:
                if previous is not None:
                    raise ValueError('gov24_collection_scope_missing') from None
                descriptor = os.open(scope_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, 'wb') as handle:
                    handle.write(scope)
                    handle.flush()
                    os.fsync(handle.fileno())
            versions = JsonFileVersionStore(staging)
            records_path = versions.records_path('gov24')
            records_path.parent.mkdir(parents=True, exist_ok=True)
            if previous is not None:
                shutil.copyfile(JsonFileVersionStore(previous).records_path('gov24'), records_path)
            result = collect_gov24(external_ids, category_values, fetcher, snapshots, versions, clock)
            if result.complete and not records_path.exists():
                records_path.write_text('{}\n', encoding='utf-8')
            return result

        complete = run_youthcenter_refresh(root, build, clock, ingest_complete=(
            lambda: ingest_gov24_command(data_dir) == 0) if database_enabled else None)
    except Exception:
        print('보조금24 갱신 실패: 계획·키·원본·DB 설정을 확인하세요.', file=sys.stderr)
        return 1
    print('보조금24 갱신 완료' if complete else '보조금24 갱신 실패: 마지막 성공본 유지')
    return 0 if complete else 1


def ingest_official_documents_command(data_dir: Path) -> int:
    from benefitfinder.ingestion.official_document_generations import load_official_document_generation
    from benefitfinder.infrastructure.pdf_text import extract_pdf_pages

    try:
        batch = load_official_document_generation(
            lambda relative,limit: read_collection_file(data_dir,relative,limit),extract_pdf_pages)
        result = PostgresCollectedPolicies(os.environ.get('COLLECTION_WRITE_DATABASE_URL','')).save(batch)
    except Exception:
        print('공식 문서 DB 인계 실패: official_document_import_failed',file=sys.stderr)
        return 1
    print(f'공식 문서 DB 인계: 신규 {result.new}건, 변경 {result.changed}건, '
          f'동일 {result.unchanged}건, 누락 {result.missing}건')
    return 0


def refresh_official_documents_command(data_dir: Path, plan_path: Path) -> int:
    from benefitfinder.infrastructure.official_document_http import OfficialDocumentHttpFetcher
    from benefitfinder.infrastructure.official_document_plans import load_official_document_plan
    from benefitfinder.infrastructure.pdf_text import extract_pdf_pages
    from benefitfinder.jobs.official_document_refresh import refresh_official_documents

    try:
        targets = load_official_document_plan(plan_path)
        raw = data_dir/'raw/f08-official-documents'
        raw.mkdir(parents=True,exist_ok=True,mode=0o700)
        raw.chmod(0o700)
        complete = refresh_official_documents(data_dir,targets,
            fetcher=OfficialDocumentHttpFetcher(
                allowed_urls=tuple(dict.fromkeys(target.url for target in targets))),
            snapshots=JsonFileSnapshotStore(raw,private=True),versions_factory=JsonFileVersionStore,
            clock=lambda:datetime.now(KST),pdf_pages=extract_pdf_pages,
            ingest_complete=(lambda:ingest_official_documents_command(data_dir)==0)
                if 'COLLECTION_WRITE_DATABASE_URL' in os.environ else None)
    except Exception:
        print('공식 문서 갱신 실패: official_document_refresh_failed',file=sys.stderr)
        return 1
    print('공식 문서 갱신 완료' if complete else '공식 문서 갱신 실패: 마지막 성공본 유지')
    return 0 if complete else 1


def refresh_youthcenter_command(data_dir: Path, *, notify_discord: bool = False) -> int:
    started_at = datetime.now(KST)
    code = _refresh_youthcenter(data_dir)
    if notify_discord:
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        if not webhook:
            print("F08 알림 실패: discord_webhook_missing", file=sys.stderr)
            return code or 1
        try:
            message = refresh_notification(
                data_dir / "processed" / "f08-youthcenter", code, started_at
            )
            DiscordWebhook(webhook).send(message)
        except DiscordWebhookError as exc:
            print(f"F08 알림 실패: {exc}", file=sys.stderr)
            return code or 1
        print("F08 디스코드 결과 알림 전송 완료")
    return code


def refresh_data_command(data_dir: Path, *, notify_discord: bool = False) -> int:
    """Run configured F08 sources without publishing unreviewed policies."""

    def notify(message: str) -> None:
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        if not webhook:
            raise DiscordWebhookError("discord_webhook_missing")
        DiscordWebhook(webhook).send(message)

    def analyze_pending():
        if _analysis_version() == DECIDER_ANALYSIS_VERSION:
            return run_decider_queue(data_dir, limit=int(os.environ.get('DECIDER_LIMIT', '20')),
                                     daily=True)
        result = run_f12_queue(data_dir, limit=5)
        return {key: result[key] for key in ('attempted', 'needs_review', 'stale', 'failed',
                                            'source_records_awaiting_scope_or_documents')}

    def prepare_analysis() -> dict[str, int]:
        if 'F12_WRITE_DATABASE_URL' not in os.environ:
            raise ValueError('f12_preparation_requires_database')
        combined = dict.fromkeys(('targets', 'registered', 'replayed', 'failed'), 0)
        if os.environ.get('DECIDER_DISCOVER_YOUTHCENTER') == 'true':
            if _analysis_version() != DECIDER_ANALYSIS_VERSION:
                raise ValueError('decider_discovery_requires_decider_engine')
            values, selection = load_decider_corpus(os.environ['F12_WRITE_DATABASE_URL'])
            tasks = PostgresPolicyAnalysisTasks(os.environ['F12_WRITE_DATABASE_URL'],
                                               analysis_version=DECIDER_ANALYSIS_VERSION)
            combined['targets'] += selection['counts']['targets']
            combined['failed'] += selection['counts']['held']
            for value in values:
                try:
                    result = tasks.register_prepared(value)
                    combined['replayed' if result['replayed'] else 'registered'] += 1
                except Exception:
                    combined['failed'] += 1
        paths = [Path(os.environ[name]) for name in
                 ('F12_PREPARATION_PLAN', 'GOV24_PREPARATION_PLAN') if name in os.environ]
        policies = [target.policy_id for path in paths
                    for target in load_preparation_plan(path).targets]
        if len(policies) != len(set(policies)):
            # One canonical policy must not be silently replaced by two separate
            # inputs. Explicit multi-source registration binds both materials.
            raise ValueError('f12_preparation_duplicate_policy')
        for name in ('F12_PREPARATION_PLAN', 'GOV24_PREPARATION_PLAN'):
            if name in os.environ:
                result = prepare_f12_inputs(Path(os.environ[name]))
                for key in combined:
                    combined[key] += result[key]
        return combined

    try:
        code = run_daily_refresh(
            data_dir / "processed" / "f08",
            refresh_policies=lambda: _refresh_youthcenter(data_dir),
            refresh_rate=refresh_ecos_rate_command,
            refresh_gov24=(lambda: refresh_gov24_command(data_dir,
                Path(os.environ['GOV24_PREPARATION_PLAN'])))
                if 'GOV24_PREPARATION_PLAN' in os.environ else None,
            refresh_documents=(lambda:refresh_official_documents_command(data_dir,
                Path(os.environ['OFFICIAL_DOCUMENT_PLAN'])))
                if 'OFFICIAL_DOCUMENT_PLAN' in os.environ else None,
            clock=lambda: datetime.now(KST),
            notify=notify if notify_discord else None,
            prepare_analysis=prepare_analysis if (any(name in os.environ for name in
                ('F12_PREPARATION_PLAN', 'GOV24_PREPARATION_PLAN'))
                or os.environ.get('DECIDER_DISCOVER_YOUTHCENTER') == 'true') else None,
            analyze_pending=analyze_pending if 'F12_WRITE_DATABASE_URL' in os.environ else None,
        )
    except DailyRefreshError as error:
        print(f"F08 통합 갱신 실패: {error}", file=sys.stderr)
        if notify_discord:
            try:
                notify(f"[F08] 정책·기준금리 갱신 준비 실패: {error}")
            except DiscordWebhookError:
                print("F08 알림 실패: discord_notification_failed", file=sys.stderr)
        return 1
    if code:
        print("F08 통합 갱신 실패: 소스별 상태·실행 기록·알림 설정을 확인하세요.", file=sys.stderr)
    else:
        print("F08 정책·기준금리 갱신 완료")
    return code


def _print_review_summary(snapshot: RuleReviewSnapshot) -> None:
    approved = sum(review.status == "approved" for review in snapshot.reviews)
    pending = len(snapshot.reviews) - approved
    recorded = {review.candidate for review in snapshot.reviews}
    unreviewed = sum(candidate not in recorded for candidate in snapshot.batch.candidates)
    print(f"검수 자료: {snapshot.operation.value}, 생성 시각 {snapshot.created_at.isoformat()}")
    print(f"기록 {len(snapshot.reviews)}건: 승인 {approved}건, 검수 대기 {pending}건")
    if unreviewed:
        print(f"새로 준비할 후보 {unreviewed}건: 별도 prepare 결과가 필요합니다.")
    for review in snapshot.reviews:
        print(f"{review.candidate.id}: {review.status} (검수 문제 {len(review.issues)}건)")
    print("로컬 검수 기록이며 정책 게시·계산에는 연결되지 않습니다.")


def rule_review_command(args: argparse.Namespace) -> int:
    """Assemble local extraction and review operations."""
    try:
        if args.review_action == "example":
            write_rule_batch(args.output, example_rule_batch())
            print("[가상] 규칙 검수 입력 예제를 생성했습니다. 실제 정책 자료가 아닙니다.")
            return 0
        if args.review_action in {"extract", "extract-all"}:
            if args.review_action == "extract-all" and os.path.lexists(args.output_dir):
                raise RuleReviewFileError(RULE_REVIEW_OUTPUT_EXISTS)
            started = monotonic()
            original = load_rule_batch(args.input)
            chat = extraction_chat(args.model)
            model_name = args.model or LLMSettings.from_env().model
            batch, trace = extract_rule_batch(
                original,
                target_name=args.target_name,
                target_id=args.target_id,
                target_aliases=tuple(args.target_alias),
                chat=chat,
            )
            if args.review_action == "extract-all":
                draft = extract_policy_facts(original, chat=chat)
                snapshot = prepare_snapshot(batch, at=datetime.now(KST))
                write_f12_review_pair(args.output_dir, snapshot, draft)
                print(f"모델 {model_name}: {' → '.join(trace)} ({monotonic() - started:.1f}초)")
                print("rules-pending.json · facts-pending.json 저장. 둘 다 검수 대기.")
                return 0
            write_rule_batch(args.output, batch)
            print(f"모델 {model_name}: {' → '.join(trace)} ({monotonic() - started:.1f}초)")
            print(f"규칙 후보 {len(batch.candidates)}건. 다음 단계: rule-review prepare")
            return 0
        if args.review_action == "extract-facts":
            started = monotonic()
            model_name = args.model or LLMSettings.from_env().model
            draft = extract_policy_facts(
                load_rule_batch(args.input), chat=extraction_chat(args.model)
            )
            write_policy_facts(args.output, draft)
            print(
                f"모델 {model_name} ({monotonic() - started:.1f}초): "
                f"자격 {len(draft.eligibility)}건, 금액 {len(draft.amounts)}건, "
                f"지급 시점 {len(draft.payment_timing)}건. 검수 대기."
            )
            return 0
        if args.review_action == "prepare":
            batch = load_rule_batch(args.input)
            result = prepare_snapshot(batch, at=datetime.now(KST))
        elif args.review_action == "approve":
            snapshot = load_rule_snapshot(args.snapshot)
            result = approve_snapshot(
                snapshot,
                candidate_id=args.candidate_id,
                reviewer=args.reviewer,
                at=datetime.now(KST),
            )
        elif args.review_action == "refresh":
            snapshot = load_rule_snapshot(args.snapshot)
            batch = load_rule_batch(args.input)
            result = refresh_snapshot(snapshot, batch, at=datetime.now(KST))
        elif args.review_action == "show":
            result = inspect_snapshot(load_rule_snapshot(args.snapshot))
            _print_review_summary(result)
            return 0
        elif args.review_action in {"show-all", "decide", "export-approved"}:
            snapshot, draft = load_f12_review_pair(args.input_dir)
            checked = inspect_snapshot(snapshot)
            if args.review_action == "export-approved":
                count = write_f12_approved_export(args.output, snapshot, draft, args.decisions)
                print(f"승인 항목 {count}건을 로컬 파일로 내보냈습니다.")
                return 0
            decisions = (
                load_f12_decisions(args.decisions, snapshot, draft) if args.decisions else ()
            )
            if args.review_action == "decide":
                entry = F12ReviewDecision(
                    args.item_id,
                    args.decision,
                    args.reviewer.strip(),
                    args.reason.strip(),
                    datetime.now(KST),
                )
                write_f12_decisions(args.output, snapshot, draft, (*decisions, entry))
                print(f"{entry.item_id}: 로컬 검수 판단 저장 ({entry.decision})")
            else:
                print(format_f12_review(checked, draft, decisions))
            return 0
        else:
            return 2
        write_rule_snapshot(args.output, result)
        _print_review_summary(result)
        return 0
    except (RuleReviewFileError, RuleReviewWorkflowError, ValueError) as exc:
        print(f"검수 작업 실패: {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("검수 작업 실패: rule_review_io_error", file=sys.stderr)
        return 1


def _add_overlap_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", type=Path, required=True, help="공식 문서 한 건과 빈 후보 목록")
    parser.add_argument("--target-name", required=True, help="상대 정책의 확인된 이름")
    parser.add_argument(
        "--target-alias", action="append", default=[], help="같은 정책의 확인된 별칭 (반복 가능)"
    )
    parser.add_argument("--target-id", required=True, help="입력 정책 목록에 있는 상대 정책 ID")
    parser.add_argument(
        "--model", help="설정한 제공자의 모델 이름. 제공자 미설정 시 명시적 Ollama 호환"
    )


def _add_rule_review_commands(commands: argparse._SubParsersAction) -> None:
    review = commands.add_parser("rule-review", help="로컬 규칙 검수 자료 준비·승인·재검수")
    actions = review.add_subparsers(dest="review_action", required=True)
    example = actions.add_parser("example", help="실제 정책이 아닌 가상 입력 파일 생성")
    example.add_argument("--output", type=Path, required=True, help="새 파일 경로")
    extract = actions.add_parser("extract", help="로컬 모델로 공식 원문 한 건의 규칙 후보 생성")
    _add_overlap_arguments(extract)
    extract.add_argument("--output", type=Path, required=True, help="새 파일 경로")
    combined = actions.add_parser(
        "extract-all", help="중복 제한과 자격·금액·지급 시점 검수 자료 생성"
    )
    _add_overlap_arguments(combined)
    combined.add_argument(
        "--output-dir", type=Path, required=True, help="새 로컬 검수 결과 디렉터리"
    )
    facts = actions.add_parser("extract-facts", help="공식 원문의 자격·금액·지급 시점 후보 생성")
    facts.add_argument("--input", type=Path, required=True, help="공식 문서 한 건이 있는 입력")
    facts.add_argument("--model", help="설정한 제공자의 모델 이름. 생략 시 LLM_MODEL")
    facts.add_argument("--output", type=Path, required=True, help="새 로컬 검수 후보 파일 경로")
    prepare = actions.add_parser("prepare", help="전체 원문·정책·후보 파일로 검수 자료 준비")
    prepare.add_argument("--input", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True, help="새 파일 경로")
    approve = actions.add_parser("approve", help="사람이 원문·예외를 검토한 뒤 승인 기록")
    approve.add_argument("--snapshot", type=Path, required=True)
    approve.add_argument("--candidate-id", required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--output", type=Path, required=True, help="새 파일 경로")
    refresh = actions.add_parser("refresh", help="최신 전체 입력으로 승인 해제·검수 사유 보존")
    refresh.add_argument("--snapshot", type=Path, required=True)
    refresh.add_argument("--input", type=Path, required=True)
    refresh.add_argument("--output", type=Path, required=True, help="새 파일 경로")
    show = actions.add_parser("show", help="저장된 전체 입력으로 상태 재검사·요약 (읽기 전용)")
    show.add_argument("--snapshot", type=Path, required=True)
    show_all = actions.add_parser(
        "show-all", help="두 검수 파일의 후보와 원문 근거 조회 (읽기 전용)"
    )
    show_all.add_argument("--input-dir", type=Path, required=True, help="extract-all 결과 디렉터리")
    show_all.add_argument("--decisions", type=Path, help="함께 표시할 로컬 판단 기록")
    decide = actions.add_parser("decide", help="F12 항목 하나의 사람 검수 판단을 새 파일에 기록")
    decide.add_argument("--input-dir", type=Path, required=True, help="extract-all 결과 디렉터리")
    decide.add_argument("--item-id", required=True, help="show-all에 표시된 항목 ID")
    decide.add_argument("--decision", choices=("approve", "reject"), required=True)
    decide.add_argument("--reviewer", required=True, help="검수자 식별자")
    decide.add_argument("--reason", required=True, help="승인 또는 반려 사유")
    decide.add_argument("--decisions", type=Path, help="이어 쓸 이전 로컬 판단 기록")
    decide.add_argument("--output", type=Path, required=True, help="새 판단 기록 파일 경로")
    export = actions.add_parser(
        "export-approved", help="사람이 승인한 항목과 근거만 로컬 파일로 저장"
    )
    export.add_argument("--input-dir", type=Path, required=True, help="extract-all 결과 디렉터리")
    export.add_argument("--decisions", type=Path, required=True, help="최신 로컬 판단 기록")
    export.add_argument("--output", type=Path, required=True, help="새 로컬 내보내기 파일 경로")


def evaluate_f12_command(manifest: Path, output: Path, *, view: str | None = None) -> int:
    """Write read-only evaluation results; console contains counts, never source text."""
    try:
        report = evaluate_manifest(manifest, view=view)
        write_evaluation_report(output, report)
    except EvaluationFileError as error:
        print(f"F12 평가 실패: {error}", file=sys.stderr)
        return 2
    except FileExistsError:
        print("F12 평가 실패: evaluation_output_exists", file=sys.stderr)
        return 2
    except OSError:
        print("F12 평가 실패: evaluation_io_error", file=sys.stderr)
        return 2
    reports = report['views'] if 'views' in report else {report.get('view', 'provided'): report}
    for label, result in reports.items():
        summary = result["summary"]
        prefix = f"[{label}] " if 'views' in report or 'view' in report else ''
        print(
            f"{prefix}승인 근거 {summary['reference_items']}건: 완전 포함 {summary['covered_items']}, "
            f"부분 {summary['partial_items']}, 누락 {summary['missing_items']}, "
            f"비교 불가 {summary['not_comparable_items']}"
        )
        print(f"예외 일치 {summary['matched_exceptions']}/{summary['reference_exceptions']}, "
              f"원문 인용 오류 {summary['citation_errors']}건. 평가가 승인을 바꾸지 않습니다.")
        if 'input_accounting' in result:
            accounting = result['input_accounting']
            print(f"{prefix}후보 입력 {accounting['candidate_items_read']} / "
                  f"평가 {accounting['candidate_items_evaluated']}, "
                  f"별도 예외 인용 오류 {result['auxiliary_exceptions']['citation_errors']}건")
    return 1 if report["assessment"] == "findings" else 0


def extract_chunked_command(source: Path, output: Path) -> int:
    """Run one local E4B extraction over every window; never approve or publish."""
    try:
        batch = load_rule_batch(source)
        output.mkdir(parents=True, exist_ok=False)
        facts, provenance = extract_chunked_evidence(
            batch,
            chat=chunk_chat(),
            progress=lambda done, total: print(f"문맥 묶음 {done}/{total} 완료", flush=True),
            checkpoint=lambda record: write_chunk_checkpoint(output, record),
        )
        snapshot = prepare_snapshot(batch, at=datetime.now(ZoneInfo("Asia/Seoul")))
        write_chunked_run(output, snapshot, facts, provenance)
    except FileExistsError:
        print("분할 추출 실패: chunk_output_exists", file=sys.stderr)
        return 1
    except (ValueError, OSError, RuleReviewWorkflowError, RuleReviewFileError) as error:
        allowed = {
            "chunk_input_invalid",
            "chunk_context_limit",
            "chunk_response_invalid",
            "chunk_model_failed",
        }
        code = str(error) if str(error) in allowed else "chunk_extraction_failed"
        print(f"분할 추출 실패: {code}", file=sys.stderr)
        return 1
    print("분할 추출 완료: 검수 대기. 승인·DB 적재·게시 상태를 바꾸지 않습니다.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments[:2] == ['decider-batch', 'import-source']:
        parser = argparse.ArgumentParser(prog='python -m benefitfinder.jobs decider-batch import-source')
        _add_source_import_arguments(parser)
        return import_source_command(parser.parse_args(arguments[2:]))
    parser = argparse.ArgumentParser(prog="python -m benefitfinder.jobs")
    commands = parser.add_subparsers(dest="command", required=True)
    decider = commands.add_parser('decider-batch', help='공식 Decider 일괄 추출·검증·명시적 게시')
    decider_actions = decider.add_subparsers(dest='decider_action', required=True)
    source_import = decider_actions.add_parser('import-source', help='모델 없이 API 원문을 미검수 상태로 준비')
    _add_source_import_arguments(source_import)
    decider_export = decider_actions.add_parser('export', help='공식 취업·주거 분류 전체를 읽기 전용으로 준비')
    decider_export.add_argument('--output', type=Path, required=True)
    decider_export.add_argument('--register', action='store_true', help='F12 v5 큐에도 멱등 등록')
    decider_run = decider_actions.add_parser('run')
    decider_input = decider_run.add_mutually_exclusive_group(required=True)
    decider_input.add_argument('--manifest', type=Path)
    decider_input.add_argument('--queue', action='store_true')
    decider_run.add_argument('--data-dir', type=Path, default=Path('data'))
    decider_run.add_argument('--limit', type=int, default=20)
    decider_run.add_argument('--publish', action='store_true', help='통과한 평가·검증 항목만 운영 게시')
    decider_eval = decider_actions.add_parser('evaluate')
    decider_eval.add_argument('--report', type=Path, required=True)
    decider_eval.add_argument('--gold', type=Path, required=True)
    decider_eval.add_argument('--output', type=Path, required=True)
    source_cash = commands.add_parser(
        'source-cash', help='현재 원천으로 결정론적 현금 사실을 준비하고 명시적으로 게시'
    )
    source_cash.add_argument('--output', type=Path, required=True)
    source_cash.add_argument('--publish', action='store_true', help='검증된 항목만 새 버전으로 게시')
    source_cash.add_argument('--batch-size', type=int, default=10)
    source_cash.add_argument('--as-of', type=date.fromisoformat)
    source_cash.add_argument(
        '--applicant-cash-facts', action='store_true',
        help='대응 reader 배포 후에만 사용: 실제 지출액·지급예정월 입력을 받는 1건 계산 경로 포함',
    )
    commands.add_parser("refresh-ecos-rate", help="ECOS 기준금리의 마지막 성공값을 DB에 갱신")
    daily = commands.add_parser("refresh-data", help="정책·기준금리 통합 갱신과 단일 결과 보고")
    daily.add_argument("--data-dir", type=Path, default=Path("data"))
    daily.add_argument("--notify-discord", action="store_true", help="디스코드로 소스별 결과 보고")
    analysis = commands.add_parser('f12-analysis', help='공식 문서 등록·오프라인 분석·검수 대기 조회')
    analysis_actions = analysis.add_subparsers(dest='analysis_action', required=True)
    prepare = analysis_actions.add_parser('prepare', help='확인된 API 본문 매핑으로 분석 입력 자동 등록')
    prepare.add_argument('--plan', type=Path, required=True)
    register = analysis_actions.add_parser('register')
    register.add_argument('--input', type=Path, required=True)
    register.add_argument('--recheck-id', type=int)
    run = analysis_actions.add_parser('run')
    run.add_argument('--data-dir', type=Path, default=Path('data'))
    run.add_argument('--limit', type=int, default=5)
    status = analysis_actions.add_parser('status')
    status.add_argument('--task-id', type=int)
    collect = commands.add_parser("collect-youthcenter", help="온통청년 정책 목록 전체 수집")
    collect.add_argument("--data-dir", type=Path, default=Path("data"))
    ingest = commands.add_parser("ingest-youthcenter", help="완전한 수집 성공본만 DB에 재적재")
    ingest.add_argument("--data-dir", type=Path, default=Path("data"))
    gov24 = commands.add_parser('refresh-gov24', help='확인된 청년 취업·주거 정책 API 수집')
    gov24.add_argument('--plan', type=Path, required=True)
    gov24.add_argument('--data-dir', type=Path, default=Path('data'))
    gov24_ingest = commands.add_parser('ingest-gov24', help='보조금24 완전한 성공본 DB 재인계')
    gov24_ingest.add_argument('--data-dir', type=Path, default=Path('data'))
    official = commands.add_parser('refresh-official-documents',help='확인된 공식 HTML·PDF 수집')
    official.add_argument('--plan',type=Path,required=True)
    official.add_argument('--data-dir',type=Path,default=Path('data'))
    official_ingest = commands.add_parser('ingest-official-documents',help='공식 문서 성공본 DB 재인계')
    official_ingest.add_argument('--data-dir',type=Path,default=Path('data'))
    refresh = commands.add_parser(
        "refresh-youthcenter", help="완전한 온통청년 수집만 최신본으로 전환"
    )
    refresh.add_argument("--data-dir", type=Path, default=Path("data"))
    refresh.add_argument(
        "--notify-discord", action="store_true", help="디스코드 웹훅으로 결과 알림"
    )
    evaluate = commands.add_parser("evaluate-f12", help="사람 승인 근거 기준의 로컬 추출 평가")
    evaluate.add_argument("--manifest", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument(
        "--view",
        choices=("model", "context", "both"),
        help="v2 입력의 모델 원선택/문맥 보존 결과 (기본 both)",
    )
    chunked = commands.add_parser("extract-chunked-facts", help="앞뒤 문맥을 보존한 로컬 E4B 추출")
    chunked.add_argument("--input", type=Path, required=True)
    chunked.add_argument("--output-dir", type=Path, required=True)
    f12_catalog = commands.add_parser('catalog-from-f12', help='사람 검수 후보와 명시적 실행 규칙을 게시 입력으로 연결')
    for flag in ('pair-dir', 'decisions', 'approved', 'input', 'plan', 'source', 'output'):
        f12_catalog.add_argument('--' + flag, type=Path, required=True)
    f12_catalog.add_argument('--price-source', type=Path)
    ai_catalog = commands.add_parser("ai-catalog", help="AI 독립 검수 배치 검증·게시·철회")
    ai_actions = ai_catalog.add_subparsers(dest="catalog_action", required=True)
    for action in ("validate", "publish", "retire"):
        command = ai_actions.add_parser(action)
        command.add_argument("--manifest", type=Path, required=True)
    unreviewed_catalog = commands.add_parser('unreviewed-catalog', help='미검수 원천 연결 배치 검증·게시·철회')
    unreviewed_actions = unreviewed_catalog.add_subparsers(dest='catalog_action', required=True)
    for action in ('validate', 'publish', 'retire'):
        command = unreviewed_actions.add_parser(action)
        command.add_argument('--manifest', type=Path, required=True)
    retire_exact = unreviewed_actions.add_parser(
        'retire-exact', help='정책·문서 버전·승인 해시가 정확히 일치하는 미검수 게시본만 철회')
    retire_exact.add_argument('--releases', type=Path, required=True)
    service_facts = commands.add_parser(
        'service-source-facts', help='서비스 기록에 원천 지역·연령 조건과 마감 상태를 오프라인으로 연결')
    service_facts_input = service_facts.add_mutually_exclusive_group(required=True)
    service_facts_input.add_argument('--manifest', type=Path)
    service_facts_input.add_argument('--from-database', action='store_true',
                                     help='게시 중인 미검수 서비스를 읽기 전용으로 다시 구성')
    service_facts.add_argument('--database-url-env', default='POLICY_PUBLISH_DATABASE_URL')
    service_facts.add_argument('--output', type=Path, help='생략하면 미리보기 요약만 출력')
    catalog = commands.add_parser("policy-catalog", help="검수된 정책 카탈로그 검증·명시적 게시")
    catalog_actions = catalog.add_subparsers(dest="catalog_action", required=True)
    for action in ("validate", "publish"):
        command = catalog_actions.add_parser(action)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--source", type=Path, required=True)
        if action == "publish":
            command.add_argument("--approval", type=Path, required=True)
    rechecks = catalog_actions.add_parser("rechecks", help="재검수 대기 기록을 새 로컬 파일로 내보내기")
    rechecks.add_argument("--output", type=Path, required=True)
    rechecks.add_argument("--after-id", type=int, default=0)
    rechecks.add_argument("--limit", type=int, default=100)
    service_catalog = commands.add_parser('service-catalog', help='검수한 서비스·단가 근거 검증과 명시적 게시')
    service_actions = service_catalog.add_subparsers(dest='catalog_action', required=True)
    for action in ('validate', 'publish'):
        command = service_actions.add_parser(action)
        command.add_argument('--input', type=Path, required=True)
        command.add_argument('--source', type=Path, required=True)
        command.add_argument('--price-source', type=Path)
        if action == 'publish':
            command.add_argument('--approval', type=Path, required=True)
    _add_rule_review_commands(commands)
    explain = commands.add_parser(
        "explain-example", help="F13 가상 게시 조항으로 설명 생성·폴백 확인"
    )
    explain.add_argument("--category", choices=("employment", "housing"), default="employment")
    explain.add_argument("--model", help="설정한 제공자 모델. 제공자 미설정 시 명시적 Ollama 호환")
    args = parser.parse_args(argv)
    if args.command == 'decider-batch':
        return decider_command(args)
    if args.command == 'source-cash':
        return source_cash_command(args)
    if args.command == 'catalog-from-f12':
        return f12_catalog_command(args)
    if args.command == 'refresh-gov24':
        return refresh_gov24_command(args.data_dir, args.plan)
    if args.command == 'refresh-official-documents':
        return refresh_official_documents_command(args.data_dir,args.plan)
    if args.command == 'ingest-official-documents':
        return ingest_official_documents_command(args.data_dir)
    if args.command == 'ingest-gov24':
        return ingest_gov24_command(args.data_dir)
    if args.command == 'f12-analysis':
        return f12_analysis_command(args)
    if args.command == "ai-catalog":
        return ai_catalog_command(args)
    if args.command == 'unreviewed-catalog':
        return unreviewed_catalog_command(args)
    if args.command == 'service-source-facts':
        return service_source_facts_command(args)
    if args.command == 'service-catalog':
        return service_catalog_command(args)
    if args.command == "refresh-data":
        return refresh_data_command(args.data_dir, notify_discord=args.notify_discord)
    if args.command == "ingest-youthcenter":
        return ingest_youthcenter_command(args.data_dir)
    if args.command == "refresh-ecos-rate":
        return refresh_ecos_rate_command()
    if args.command == "policy-catalog":
        if args.catalog_action == "rechecks":
            return policy_rechecks_command(args)
        return policy_catalog_command(args)
    if args.command == "collect-youthcenter":
        return collect_youthcenter_command(args.data_dir)
    if args.command == "refresh-youthcenter":
        return refresh_youthcenter_command(args.data_dir, notify_discord=args.notify_discord)
    if args.command == "extract-chunked-facts":
        return extract_chunked_command(args.input, args.output_dir)
    if args.command == "rule-review":
        return rule_review_command(args)
    if args.command == "explain-example":
        request, clauses = explanation_example(args.category)
        try:
            settings = LLMSettings.from_env()
            if settings.provider != "disabled":
                generator = GroundedPolicyExplainer(
                    CompatibleExplanationCompletion(settings.endpoint(model=args.model))
                )
            else:
                generator = (
                    GroundedPolicyExplainer(OllamaExplanationCompletion(args.model))
                    if args.model
                    else None
                )
        except ValueError:
            print("설명 예제 실행 실패: local_explanation_model_required", file=sys.stderr)
            return 2
        result = asyncio.run(explain_policy(request, InMemoryPublishedClauses(clauses), generator))
        print(json.dumps({"virtual_data": True, **asdict(result)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "evaluate-f12":
        return evaluate_f12_command(args.manifest, args.output, view=args.view)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

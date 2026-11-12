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
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

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
from benefitfinder.application.policy_analysis import PolicyAnalysisStoreError
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

API_KEY_ENV = "YOUTHCENTER_API_KEY"
KST = ZoneInfo("Asia/Seoul")


def prepare_f12_inputs(plan_path: Path) -> dict[str, int]:
    plan = load_preparation_plan(plan_path)
    url = os.environ.get('F12_WRITE_DATABASE_URL', '')
    tasks = PostgresPolicyAnalysisTasks(url)
    if plan.source == 'gov24':
        from benefitfinder.ingestion.gov24 import analysis_fields, gov24_hashed_fields

        return prepare_analysis_inputs(plan, PostgresAnalysisSources(url, source='gov24'),
            tasks.register, lambda fields: record_sha256(gov24_hashed_fields(fields)), analysis_fields)
    return prepare_analysis_inputs(plan, PostgresAnalysisSources(url), tasks.register,
        lambda fields: record_sha256(hashed_fields(YOUTHCENTER_POLICY_LIST, fields)))


def run_f12_queue(data_dir: Path, *, limit: int = 5) -> dict[str, object]:
    # Lazy import keeps non-LLM CLI commands independent of model initialization.
    from benefitfinder.ai.policy_analysis_agent import analyze_policy

    settings = LLMSettings.from_env()
    if settings.provider == 'disabled':
        raise ValueError('llm_not_configured')
    chat = CompatibleChat(settings.endpoint())
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
        tasks = PostgresPolicyAnalysisTasks(os.environ.get('F12_WRITE_DATABASE_URL', ''))
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
        JsonFileSnapshotStore(raw_root),
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
    snapshots = JsonFileSnapshotStore(data_dir / "raw" / "f08-youthcenter")

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
        result = run_f12_queue(data_dir, limit=5)
        return {key: result[key] for key in ('attempted', 'needs_review', 'stale', 'failed',
                                            'source_records_awaiting_scope_or_documents')}

    def prepare_analysis() -> dict[str, int]:
        if 'F12_WRITE_DATABASE_URL' not in os.environ:
            raise ValueError('f12_preparation_requires_database')
        combined = dict.fromkeys(('targets', 'registered', 'replayed', 'failed'), 0)
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
            clock=lambda: datetime.now(KST),
            notify=notify if notify_discord else None,
            prepare_analysis=prepare_analysis if any(name in os.environ for name in
                ('F12_PREPARATION_PLAN', 'GOV24_PREPARATION_PLAN')) else None,
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
    parser = argparse.ArgumentParser(prog="python -m benefitfinder.jobs")
    commands = parser.add_subparsers(dest="command", required=True)
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
    if args.command == 'catalog-from-f12':
        return f12_catalog_command(args)
    if args.command == 'refresh-gov24':
        return refresh_gov24_command(args.data_dir, args.plan)
    if args.command == 'ingest-gov24':
        return ingest_gov24_command(args.data_dir)
    if args.command == 'f12-analysis':
        return f12_analysis_command(args)
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

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
from benefitfinder.infrastructure.ollama_chat import OllamaChat
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
from benefitfinder.infrastructure.policy_catalog_records import CatalogRecord, record_hash, validate_source_text
from benefitfinder.infrastructure.database.repositories.published_policies import publish_policy

API_KEY_ENV = "YOUTHCENTER_API_KEY"
KST = ZoneInfo("Asia/Seoul")


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
        complete = run_youthcenter_refresh(root, build, clock)
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
    return 0


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
            chat = OllamaChat(args.model)
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
                print(f"모델 {args.model}: {' → '.join(trace)} ({monotonic() - started:.1f}초)")
                print("rules-pending.json · facts-pending.json 저장. 둘 다 검수 대기.")
                return 0
            write_rule_batch(args.output, batch)
            print(f"모델 {args.model}: {' → '.join(trace)} ({monotonic() - started:.1f}초)")
            print(f"규칙 후보 {len(batch.candidates)}건. 다음 단계: rule-review prepare")
            return 0
        if args.review_action == "extract-facts":
            started = monotonic()
            draft = extract_policy_facts(load_rule_batch(args.input), chat=OllamaChat(args.model))
            write_policy_facts(args.output, draft)
            print(
                f"모델 {args.model} ({monotonic() - started:.1f}초): "
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
    parser.add_argument("--model", required=True, help="로컬 Ollama에 설치된 모델 이름")


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
    facts.add_argument("--model", required=True, help="로컬 Ollama에 설치된 모델 이름")
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
            batch, chat=LocalE4BChat(),
            progress=lambda done, total: print(f"문맥 묶음 {done}/{total} 완료", flush=True),
            checkpoint=lambda record: write_chunk_checkpoint(output, record),
        )
        snapshot = prepare_snapshot(batch, at=datetime.now(ZoneInfo("Asia/Seoul")))
        write_chunked_run(output, snapshot, facts, provenance)
    except FileExistsError:
        print("분할 추출 실패: chunk_output_exists", file=sys.stderr)
        return 1
    except (ValueError, OSError, RuleReviewWorkflowError, RuleReviewFileError) as error:
        allowed = {"chunk_input_invalid", "chunk_context_limit", "chunk_response_invalid",
                   "chunk_model_failed"}
        code = str(error) if str(error) in allowed else "chunk_extraction_failed"
        print(f"분할 추출 실패: {code}", file=sys.stderr)
        return 1
    print("분할 추출 완료: 검수 대기. 승인·DB 적재·게시 상태를 바꾸지 않습니다.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benefitfinder.jobs")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("refresh-ecos-rate", help="ECOS 기준금리의 마지막 성공값을 DB에 갱신")
    collect = commands.add_parser("collect-youthcenter", help="온통청년 정책 목록 전체 수집")
    collect.add_argument("--data-dir", type=Path, default=Path("data"))
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
    evaluate.add_argument("--view", choices=("model", "context", "both"),
                          help="v2 입력의 모델 원선택/문맥 보존 결과 (기본 both)")
    chunked = commands.add_parser("extract-chunked-facts", help="앞뒤 문맥을 보존한 로컬 E4B 추출")
    chunked.add_argument("--input", type=Path, required=True)
    chunked.add_argument("--output-dir", type=Path, required=True)
    catalog = commands.add_parser("policy-catalog", help="검수된 정책 카탈로그 검증·명시적 게시")
    catalog_actions = catalog.add_subparsers(dest="catalog_action", required=True)
    for action in ("validate", "publish"):
        command = catalog_actions.add_parser(action)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--source", type=Path, required=True)
        if action == "publish":
            command.add_argument("--approval", type=Path, required=True)
    _add_rule_review_commands(commands)
    explain = commands.add_parser(
        "explain-example", help="F13 가상 게시 조항으로 설명 생성·폴백 확인"
    )
    explain.add_argument("--category", choices=("employment", "housing"), default="employment")
    explain.add_argument("--model", help="로컬 Ollama 모델; 생략하면 템플릿 사용")
    args = parser.parse_args(argv)
    if args.command == "refresh-ecos-rate":
        return refresh_ecos_rate_command()
    if args.command == "policy-catalog":
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

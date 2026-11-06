"""수집·로컬 검수 작업 실행 진입점 (조립 지점).

    uv run --locked python -m benefitfinder.jobs collect-youthcenter

인증키는 환경 변수 ``YOUTHCENTER_API_KEY``에서 읽고 출력하지 않는다. 결과는 기본으로
``data/raw``(원본)와 ``data/processed``(최신 레코드·변경 이력·CSV)에 쓰며 둘 다 Git 제외
경로다. rule-review는 검수 자료를 새 파일로 보관하며, 운영 게시나 스케줄은 연결하지 않는다.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from time import monotonic
from zoneinfo import ZoneInfo

from benefitfinder.ai.policy_facts_agent import extract_policy_facts
from benefitfinder.ai.policy_rule_agent import extract_rule_batch
from benefitfinder.application.policy_rules import RuleReviewSnapshot
from benefitfinder.infrastructure.local_files import (
    JsonFileSnapshotStore,
    JsonFileVersionStore,
    write_field_summary_csv,
    write_records_csv,
)
from benefitfinder.infrastructure.ollama_chat import OllamaChat
from benefitfinder.infrastructure.policy_rule_files import (
    RULE_REVIEW_OUTPUT_EXISTS,
    RuleReviewFileError,
    load_rule_batch,
    load_rule_snapshot,
    write_f12_review_pair,
    write_policy_facts,
    write_rule_batch,
    write_rule_snapshot,
)
from benefitfinder.infrastructure.youthcenter_http import YouthcenterHttpFetcher
from benefitfinder.ingestion.collect import collect_youthcenter
from benefitfinder.ingestion.rule_review_workflow import (
    RuleReviewWorkflowError,
    approve_snapshot,
    inspect_snapshot,
    prepare_snapshot,
    refresh_snapshot,
)
from benefitfinder.ingestion.youthcenter import SOURCE_NAME, YOUTHCENTER_POLICY_LIST
from benefitfinder.jobs.rule_review_example import example_rule_batch

API_KEY_ENV = "YOUTHCENTER_API_KEY"
KST = ZoneInfo("Asia/Seoul")


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m benefitfinder.jobs")
    commands = parser.add_subparsers(dest="command", required=True)
    collect = commands.add_parser("collect-youthcenter", help="온통청년 정책 목록 전체 수집")
    collect.add_argument("--data-dir", type=Path, default=Path("data"))
    _add_rule_review_commands(commands)
    args = parser.parse_args(argv)
    if args.command == "collect-youthcenter":
        return collect_youthcenter_command(args.data_dir)
    if args.command == "rule-review":
        return rule_review_command(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

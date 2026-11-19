import type { JSX, ReactNode } from 'react';
import { paths } from '../../app/router.ts';
import { uniqueConditionLabels } from '../../domains/youth/conditionLabels.ts';
import { StatusChip } from '../../shared/ui/StatusChip.tsx';
import { AmountBreakdown } from './AmountBreakdown.tsx';
import { formatManWon } from './format.ts';
import { PolicyExplanation } from './PolicyExplanation.tsx';
import { policyName, portfolioExclusions } from './resultModel.ts';
import type { PolicySummaries, Portfolio, ResultView } from './resultModel.ts';
import { foldElementId } from './resultViewMemory.ts';
import type { FoldId } from './resultViewMemory.ts';

interface FoldProps {
  id: FoldId;
  title: string;
  /** 제목 아래 한 줄 안내 */
  note?: string;
  /** 처음에 펼쳐 둘지 (상세에서 돌아왔을 때) */
  defaultOpen: boolean;
  onToggle?: (id: FoldId, open: boolean) => void;
  children: ReactNode;
}

/** 누르면 펼쳐지는 목록 (기획서 4.5 14번) */
function Fold({ id, title, note, defaultOpen, onToggle, children }: FoldProps): JSX.Element {
  return (
    <details
      id={foldElementId(id)}
      className="s3-fold"
      open={defaultOpen}
      onToggle={(event) => {
        onToggle?.(id, event.currentTarget.open);
      }}
    >
      <summary className="s3-fold__summary">
        <span className="s3-fold__title">{title}</span>
        {note !== undefined ? <span className="s3-fold__note">{note}</span> : null}
      </summary>
      <ul className="s3-fold__list">{children}</ul>
    </details>
  );
}

interface ResultListsProps {
  view: ResultView;
  /** 기회비용을 보여 줄 지금 고른 조합 */
  selected: Portfolio | undefined;
  info: PolicySummaries;
  /** preview 설명의 AI 배지 기준. 생략하면 기본 안내로 해석한다. */
  aiGenerated?: boolean;
  /** 처음부터 펼쳐 둘 목록 */
  openFolds?: ReadonlySet<FoldId>;
  onToggleFold?: (id: FoldId, open: boolean) => void;
}

type NotReviewedItem = NonNullable<ResultView['not_yet_reviewed']>[number];

/** preview가 있으면 실제 정책명과 검증 표시를 쓴다. 없으면 기존 동작을 유지한다. */
function notReviewedName(item: NotReviewedItem, info: PolicySummaries): string {
  return item.policy_name ?? policyName(item.policy_id, info);
}

/** 접기 목록 4개: 빠진 정책 · 모집 끝남 · 조건 안 맞음 · 계산 전. 0개인 목록은 숨긴다 */
export function ResultLists({
  view, selected, info, aiGenerated = false, openFolds = new Set(), onToggleFold,
}: ResultListsProps): JSX.Element {
  const excluded = portfolioExclusions(view, selected, info);
  const closed = view.closed_now ?? [];
  const notEligible = view.not_eligible ?? [];
  const notReviewed = view.not_yet_reviewed ?? [];

  return (
    <div className="s3-folds">
      {excluded.length > 0 ? (
        <Fold id="excluded" defaultOpen={openFolds.has('excluded')} onToggle={onToggleFold}
          title={`이 조합에서 빠진 정책 ${String(excluded.length)}개`}>
          {excluded.map((item) => (
            <li key={item.policy_id}>
              <a className="s3-fold__name" href={paths.policy(item.policy_id)}>
                {policyName(item.policy_id, info)}
              </a>
              <span>
                {item.reason ?? item.evidence[0]?.quote ?? '함께 받을 수 없는 근거는 정책 상세에서 확인해 주세요.'}
                {item.evidence.length > 0 ? ' (공고 원문 근거)' : ''}
                {item.amount_won !== null ? `. 이 조합의 기회비용 ${formatManWon(item.amount_won)}` : ''}
              </span>
              {item.amount_breakdown !== undefined ? (
                <AmountBreakdown value={item.amount_breakdown} variant="compact" />
              ) : null}
            </li>
          ))}
        </Fold>
      ) : null}

      {closed.length > 0 ? (
        <Fold id="closed" defaultOpen={openFolds.has('closed')} onToggle={onToggleFold}
          title={`지금은 모집이 끝난 정책 ${String(closed.length)}개`} note="다음 모집 때 받을 수 있어요">
          {closed.map((item) => (
            <li key={item.policy_id}>
              <a className="s3-fold__name" href={paths.policy(item.policy_id)}>
                {policyName(item.policy_id, info)}
              </a>
              <span>
                지난 모집 {item.last_period ?? '확인 필요'} · 다음 모집 {item.next_expected ?? '미정'}
              </span>
            </li>
          ))}
        </Fold>
      ) : null}

      {notEligible.length > 0 ? (
        <Fold id="notEligible" defaultOpen={openFolds.has('notEligible')} onToggle={onToggleFold}
          title={`조건이 맞지 않는 정책 ${String(notEligible.length)}개`}>
          {notEligible.map((item) => (
            <li key={item.policy_id}>
              <a className="s3-fold__name" href={paths.policy(item.policy_id)}>
                {policyName(item.policy_id, info)}
              </a>
              <span className="s3-fold__chips">
                {uniqueConditionLabels(item.failed).map((label) => (
                  <StatusChip key={label} status="not_met">
                    {label}
                  </StatusChip>
                ))}
              </span>
            </li>
          ))}
        </Fold>
      ) : null}

      {notReviewed.length > 0 ? (
        <Fold
          id="notReviewed"
          defaultOpen={openFolds.has('notReviewed')}
          onToggle={onToggleFold}
          title={`아직 조합 계산 전인 정책 ${String(notReviewed.length)}개`}
          note={notReviewed.some((item) => item.verification_only === true)
            ? '승인한 근거와 확인 범위를 볼 수 있어요. 자격·금액·조합은 확인 전이에요.'
            : '금액·중복 여부를 검수 중이에요. 정책별 자격 진단은 볼 수 있어요.'}
        >
          {notReviewed.map((item) => {
            if (item.review_explanation === undefined) {
              return (
                <li key={item.policy_id}>
                  <a className="s3-fold__name" href={paths.policy(item.policy_id)}>
                    {notReviewedName(item, info)}
                  </a>
                  <span className="s3-fold__chips">
                    <StatusChip status={item.eligibility_summary}>자격 진단</StatusChip>
                  </span>
                </li>
              );
            }
            return (
              <li key={item.policy_id}>
                <a className="s3-fold__name" href={paths.policy(item.policy_id)}>
                  {notReviewedName(item, info)}
                </a>
                <span>검증용 · 조합/자격/금액 확정 전</span>
                <span className="s3-fold__chips">
                  <StatusChip status={item.eligibility_summary}>자격 진단</StatusChip>
                </span>
                {item.review_scope !== undefined ? <span>확인 범위: {item.review_scope}</span> : null}
                <PolicyExplanation policy={item.review_explanation} aiGenerated={aiGenerated} />
              </li>
            );
          })}
        </Fold>
      ) : null}
    </div>
  );
}

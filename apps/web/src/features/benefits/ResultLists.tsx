import type { JSX, ReactNode } from 'react';
import { paths } from '../../app/router.ts';
import { conditionLabel } from '../../domains/youth/conditionLabels.ts';
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import { StatusChip } from '../../shared/ui/StatusChip.tsx';
import { formatManWon } from './format.ts';
import { policyName } from './resultModel.ts';
import type { Portfolio, ResultView } from './resultModel.ts';

interface FoldProps {
  title: string;
  /** 제목 아래 한 줄 안내 */
  note?: string;
  children: ReactNode;
}

/** 누르면 펼쳐지는 목록 (기획서 4.5 14번) */
function Fold({ title, note, children }: FoldProps): JSX.Element {
  return (
    <details className="s3-fold">
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
  info: ReadonlyMap<string, NormalizedBenefit>;
}

/** 접기 목록 4개: 빠진 정책 · 모집 끝남 · 조건 안 맞음 · 계산 전. 0개인 목록은 숨긴다 */
export function ResultLists({ view, selected, info }: ResultListsProps): JSX.Element {
  const excluded = view.excluded ?? [];
  const closed = view.closed_now ?? [];
  const notEligible = view.not_eligible ?? [];
  const notReviewed = view.not_yet_reviewed ?? [];

  return (
    <div className="s3-folds">
      {excluded.length > 0 ? (
        <Fold title={`이 조합에서 빠진 정책 ${String(excluded.length)}개`}>
          {excluded.map((item) => {
            const cost = selected?.given_up?.find((given) => given.policy_id === item.policy_id)?.amount_won;
            return (
              <li key={item.policy_id}>
                <a className="s3-fold__name" href={paths.policy(item.policy_id)}>
                  {policyName(item.policy_id, info)}
                </a>
                <span>
                  {item.reason} (공고 원문 근거)
                  {cost !== null && cost !== undefined ? `. 이 조합의 기회비용 ${formatManWon(cost)}` : ''}
                </span>
              </li>
            );
          })}
        </Fold>
      ) : null}

      {closed.length > 0 ? (
        <Fold title={`지금은 모집이 끝난 정책 ${String(closed.length)}개`} note="다음 모집 때 받을 수 있어요">
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
        <Fold title={`조건이 맞지 않는 정책 ${String(notEligible.length)}개`}>
          {notEligible.map((item) => (
            <li key={item.policy_id}>
              <a className="s3-fold__name" href={paths.policy(item.policy_id)}>
                {policyName(item.policy_id, info)}
              </a>
              <span className="s3-fold__chips">
                {item.failed.map((conditionId) => (
                  <StatusChip key={conditionId} status="not_met">
                    {conditionLabel(conditionId)}
                  </StatusChip>
                ))}
              </span>
            </li>
          ))}
        </Fold>
      ) : null}

      {notReviewed.length > 0 ? (
        <Fold
          title={`아직 조합 계산 전인 정책 ${String(notReviewed.length)}개`}
          note="금액·중복 여부를 검수 중이예요. 정책별 자격 진단은 볼 수 있어요."
        >
          {notReviewed.map((item) => (
            <li key={item.policy_id}>
              <a className="s3-fold__name" href={paths.policy(item.policy_id)}>
                {policyName(item.policy_id, info)}
              </a>
              <span className="s3-fold__chips">
                <StatusChip status={item.eligibility_summary}>자격 진단</StatusChip>
              </span>
            </li>
          ))}
        </Fold>
      ) : null}
    </div>
  );
}

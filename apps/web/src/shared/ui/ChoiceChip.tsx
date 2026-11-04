import type { JSX, ReactNode } from 'react';
import './ui.css';

/** radio: 하나만 고르기, checkbox: 여러 개 고르기 */
type ChoiceKind = 'radio' | 'checkbox';

interface ChoiceGroupProps {
  /** 화면 읽기 도구가 읽는 묶음 이름. 예: 군 복무 기간 */
  label: string;
  kind?: ChoiceKind;
  /** chips: 옆으로 줄바꿈, rows: 한 줄에 하나씩 크게 */
  layout?: 'chips' | 'rows';
  children: ReactNode;
}

export function ChoiceGroup({
  label,
  kind = 'radio',
  layout = 'chips',
  children,
}: ChoiceGroupProps): JSX.Element {
  return (
    <div
      role={kind === 'radio' ? 'radiogroup' : 'group'}
      aria-label={label}
      className={layout === 'rows' ? 'ui-choice-group ui-choice-group--rows' : 'ui-choice-group'}
    >
      {children}
    </div>
  );
}

interface ChoiceChipProps {
  selected: boolean;
  onSelect: () => void;
  kind?: ChoiceKind;
  /** chip: 작은 버튼, row: 가로로 꽉 찬 큰 버튼 */
  layout?: 'chip' | 'row';
  children: ReactNode;
}

export function ChoiceChip({
  selected,
  onSelect,
  kind = 'radio',
  layout = 'chip',
  children,
}: ChoiceChipProps): JSX.Element {
  const checkBefore = selected && kind === 'checkbox' && layout === 'chip';
  const checkAfter = selected && layout === 'row';

  return (
    <button
      type="button"
      role={kind}
      aria-checked={selected}
      onClick={onSelect}
      className={`ui-choice ui-choice--${layout}`}
    >
      {checkBefore ? <span aria-hidden="true">✓ </span> : null}
      <span>{children}</span>
      {checkAfter ? <span aria-hidden="true">✓</span> : null}
    </button>
  );
}

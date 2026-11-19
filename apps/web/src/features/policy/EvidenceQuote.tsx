import { useId, useState } from 'react';
import type { JSX } from 'react';
import { evidenceLocationLabel } from '../../domains/youth/conditionLabels.ts';
import { quotePreview } from './policyDetailModel.ts';

interface EvidenceQuoteProps {
  quote: string;
  location?: string | null;
  /** 인용문 글자에 더할 class (신청 방법 인용 등) */
  textClassName?: string;
}

/**
 * 원문 근거 인용. 사람이 읽을 수 없는 내부 위치(chars:…)는 숨기고,
 * 긴 인용은 앞부분만 보인 뒤 [원문 전체 보기]로 펼친다. 원문 자체는 그대로 둔다.
 */
export function EvidenceQuote({ quote, location, textClassName }: EvidenceQuoteProps): JSX.Element {
  const [expanded, setExpanded] = useState(false);
  const textId = useId();
  const where = evidenceLocationLabel(location);
  const preview = quotePreview(quote);
  const shown = preview === null || expanded ? quote : preview;

  return (
    <blockquote className="s5-quote">
      {where !== null ? <span className="s5-quote__where">「{where}」</span> : null}
      <span id={textId} className={textClassName}>{shown}</span>
      {preview !== null ? (
        <button
          type="button"
          className="s5-quote__toggle"
          aria-expanded={expanded}
          aria-controls={textId}
          onClick={() => {
            setExpanded((open) => !open);
          }}
        >
          {expanded ? '접기' : '원문 전체 보기'}
        </button>
      ) : null}
    </blockquote>
  );
}

import type { JSX } from 'react';
import { sourceName } from './format.ts';
import type { PortfolioPolicy } from './resultModel.ts';

interface PolicyExplanationProps {
  policy: PortfolioPolicy;
  aiGenerated: boolean;
}

/** 서버 설명과 인용을 그대로 표시한다. 기본 안내에 없는 원문은 만들지 않는다. */
export function PolicyExplanation({ policy, aiGenerated }: PolicyExplanationProps): JSX.Element | null {
  const reason = policy.ai_reason;
  const sources = policy.ai_sources ?? [];
  const hasReason = reason !== null && reason !== undefined && reason.trim() !== '';
  const generated = policy.ai_generated ?? aiGenerated;

  if (!hasReason && sources.length === 0) {
    return null;
  }

  return (
    <section className="s3-explanation" aria-label="추천 이유와 원문 근거">
      {hasReason ? (
        <p className="s3-policy__reason">
          <span className={generated ? 's3-ai' : 's3-template'}>{generated ? 'AI' : '기본 안내'}</span>
          {reason}
        </p>
      ) : null}

      {sources.length > 0 ? (
        <details className="s3-evidence">
          <summary>원문 근거 {sources.length}개</summary>
          <ul className="s3-evidence__list">
            {sources.map((evidence, index) => (
              <li key={`${evidence.location}-${String(index)}`}>
                <blockquote>{evidence.quote}</blockquote>
                <p className="s3-evidence__location">{evidence.location}</p>
                <p className="s3-evidence__metadata">
                  문서 버전 {evidence.source.document_version} ·{' '}
                  <time dateTime={evidence.source.checked_at}>
                    {new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium', timeZone: 'Asia/Seoul' })
                      .format(new Date(evidence.source.checked_at))}
                  </time>{' '}확인
                </p>
                <a href={evidence.source.url} target="_blank" rel="noopener noreferrer">
                  원문 보기 ↗ · {sourceName(evidence.source.url)}
                </a>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

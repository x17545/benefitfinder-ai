import type { JSX } from 'react';
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import { applyPeriod } from './policyDetailModel.ts';

type Guide = NonNullable<NormalizedBenefit['application_guide']>;
type Excerpt = NonNullable<Guide['method']>;

function Citation({ item }: { item: Excerpt }): JSX.Element {
  return (
    <blockquote className="s5-quote">
      <span className="s5-quote__where">「{item.location}」</span>
      <span className="s5-guide-quote">{item.quote}</span>
    </blockquote>
  );
}

/** 승인한 원문만 표시한다. 운영 기관과 접수 기관은 서로 추정하지 않는다. */
export function ApplicationGuide({ benefit, virtualData }: {
  benefit: NormalizedBenefit; virtualData: boolean;
}): JSX.Element {
  const guide = benefit.application_guide;
  const documents = guide?.documents;
  return (
    <>
      <section className="ui-card s5-card">
        <h2 className="s5-card__title">신청 방법</h2>
        <dl className="s5-rows">
          <dt>신청 기간</dt>
          <dd>{virtualData ? '가상 정책에는 실제 모집 일정이 없어요.' : applyPeriod(benefit.application)}</dd>
          <dt>신청 방법</dt>
          <dd>{guide?.method ? <Citation item={guide.method} /> : '신청 방법은 공식 페이지에서 확인해 주세요.'}</dd>
          <dt>접수 기관</dt>
          <dd>{guide?.office ? <Citation item={guide.office} /> : '접수 기관은 공식 페이지에서 확인해 주세요.'}</dd>
        </dl>
      </section>
      <section className="ui-card s5-card">
        <h2 className="s5-card__title">준비 서류</h2>
        {documents != null && documents.length > 0 ? (
          <ul className="s5-conditions">
            {documents.map((item, index) => <li key={index}><Citation item={item} /></li>)}
          </ul>
        ) : (
          <p className="s5-muted">{virtualData
            ? '가상 정책에는 실제 제출 서류가 없어요.'
            : documents?.length === 0
              ? '공식 자료에 서류 안내가 없어요. 신청 페이지에서 확인해 주세요.'
              : '준비 서류 안내를 아직 확인하지 못했어요. 공식 페이지에서 확인해 주세요.'}</p>
        )}
      </section>
    </>
  );
}

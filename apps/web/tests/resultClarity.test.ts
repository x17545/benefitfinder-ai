import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import emptyText from '../../../contracts/fixtures/empty-response.json?raw';
import resultText from '../../../contracts/fixtures/recommendation-response.json?raw';
import {
  conditionChips,
  conditionLabel,
  evidenceLocationLabel,
  readableReason,
  sortByStatus,
  uniqueConditionLabels,
} from '../src/domains/youth/conditionLabels.ts';
import {
  foldElementId,
  rememberFold,
  rememberScroll,
  resetResultViewMemory,
  startResultView,
} from '../src/features/benefits/resultViewMemory.ts';
import { EvidenceQuote } from '../src/features/policy/EvidenceQuote.tsx';
import { policyStatusInResult, QUOTE_PREVIEW_LIMIT, quotePreview } from '../src/features/policy/policyDetailModel.ts';
import type { RecommendationResponse } from '../src/shared/api/recommendations.ts';

const ONE_YEAR = { period: '1y', present_value: false } as const;

// 11/19 배포 사이트 응답에서 실제로 나온 내부 조건 식별자
const SERVER_IDS = [
  'selection_region_code', 'location', 'residence', 'selection_domain_data_marital',
  'selection_domain_data_region_sido_code', 'selection_employment_status', 'selection_domain_data_school',
  'minimum-age-and-professional-qualification', 'specialist-qualification', 'youth-audience-manual',
  'age-limit-manual', 'youth-housing-target', 'youth-interview-suit-rental-manual', 'eligibility_manual',
  'eligibility:1', 'condition:duplication', 'youth-intern-target', 'citizenship', 'language', 'age-19-39',
  'youth-and-nationality', 'participation-exclusions', 'preference', 'eligibility',
  'pending:zipCd', 'pending:addAplyQlfcCndCn', 'source:ptcpPrpTrgtCn:2', 'official_source_terms_3',
  'calculated_component',
];

describe('조건 이름 (내부 코드 노출 방지)', () => {
  it('서버의 내부 식별자를 화면에 그대로 보이지 않는다', () => {
    for (const id of SERVER_IDS) {
      const label = conditionLabel(id);
      expect(label).not.toBe(id);
      expect(/[A-Za-z_:]/.test(label)).toBe(false);
    }
  });

  it('알아볼 수 있는 조건은 쉬운 이름으로, 모르는 것은 기타 조건으로 보여 준다', () => {
    expect(conditionLabel('age')).toBe('나이');
    expect(conditionLabel('selection_region_code')).toBe('지역');
    expect(conditionLabel('selection_employment_status')).toBe('구직 상태');
    expect(conditionLabel('selection_domain_data_school')).toBe('학력');
    expect(conditionLabel('age-limit-manual')).toBe('나이');
    expect(conditionLabel('minimum-age-and-professional-qualification')).toBe('나이');
    expect(conditionLabel('language')).toBe('언어');
    expect(conditionLabel('youth-and-nationality')).toBe('국적');
    expect(conditionLabel('specialist-qualification')).toBe('자격 요건');
    expect(conditionLabel('youth-housing-target')).toBe('주거 조건');
    expect(conditionLabel('youth-interview-suit-rental-manual')).toBe('지원 대상');
    expect(conditionLabel('participation-exclusions')).toBe('참여 제한');
    expect(conditionLabel('pending:zipCd')).toBe('지역');
    expect(conditionLabel('pending:addAplyQlfcCndCn')).toBe('추가 신청 자격');
    expect(conditionLabel('eligibility:1')).toBe('기타 조건');
  });

  it('같은 이름은 한 번만, 칩은 미충족 → 확인 필요 → 충족 순서로 합친다', () => {
    expect(uniqueConditionLabels(['selection_region_code', 'location', 'residence'])).toEqual(['지역']);
    const chips = conditionChips([
      { condition_id: 'age', status: 'met' },
      { condition_id: 'eligibility', status: 'needs_review' },
      { condition_id: 'eligibility:1', status: 'needs_review' },
      { condition_id: 'region', status: 'not_met' },
    ] as const);
    expect(chips.map((chip) => `${chip.status}:${chip.label}`)).toEqual([
      'not_met:지역', 'needs_review:기타 조건 2개', 'met:나이',
    ]);
    expect(sortByStatus([
      { id: 1, status: 'met' }, { id: 2, status: 'needs_review' }, { id: 3, status: 'met' },
    ] as const).map((item) => item.id)).toEqual([2, 1, 3]);
  });
});

describe('조건 이름이 겹칠 때 정보가 사라지지 않게', () => {
  it('서로 다른 조건이 뭉뚱그린 이름으로 합쳐지면 개수를 붙이고, 같은 조건을 다르게 부른 것은 하나로 둔다', () => {
    expect(uniqueConditionLabels(['eligibility', 'eligibility:1', 'eligibility'])).toEqual(['기타 조건 2개']);
    expect(uniqueConditionLabels(['youth-audience-manual', 'youth-intern-target', 'selection_region_code']))
      .toEqual(['지원 대상 2개', '지역']);
    expect(uniqueConditionLabels(['age', 'age-limit-manual', 'age-19-39'])).toEqual(['나이']);
  });

  it('상태가 다르면 합치지 않아 미충족이 충족에 가려지지 않고, 합친 조건 식별자를 모두 남긴다', () => {
    const chips = conditionChips([
      { condition_id: 'region', status: 'met' },
      { condition_id: 'selection_region_code', status: 'not_met' },
      { condition_id: 'location', status: 'not_met' },
      { condition_id: 'participation-exclusions', status: 'needs_review' },
      { condition_id: 'condition:duplication', status: 'needs_review' },
    ] as const);
    expect(chips.map((chip) => `${chip.status}:${chip.label}`)).toEqual([
      'not_met:지역', 'needs_review:참여 제한 2개', 'met:지역',
    ]);
    expect(chips[0]?.conditionIds).toEqual(['selection_region_code', 'location']);
    expect(chips[1]?.conditionIds).toEqual(['participation-exclusions', 'condition:duplication']);
  });
});

describe('원문 위치와 판정 사유', () => {
  it('기계용 위치는 숨기고, 원문 항목은 이름으로, 사람이 쓴 위치는 그대로 둔다', () => {
    expect(evidenceLocationLabel('chars:14018-14085')).toBeNull();
    expect(evidenceLocationLabel('fields.srngMthdCn')).toBe('선정 방법');
    expect(evidenceLocationLabel('fields.unknownField')).toBeNull();
    expect(evidenceLocationLabel('가상 공고: 신청 방법')).toBe('가상 공고: 신청 방법');
    expect(evidenceLocationLabel('\n')).toBeNull();
    expect(evidenceLocationLabel(undefined)).toBeNull();
  });

  it('내부 규칙 판정 값은 사유에서 뺀다', () => {
    expect(readableReason('정책 근거 문서 확인이 필요합니다. 내부 규칙 판정: needs_review'))
      .toBe('정책 근거 문서 확인이 필요합니다.');
    expect(readableReason('입력 조건이 없어요.')).toBe('입력 조건이 없어요.');
    expect(readableReason(null)).toBe('');
  });
});

describe('긴 원문 근거', () => {
  const LONG = '신청일 현재 주민등록상 해당 지역에 거주하는 만 19세 이상 34세 이하 무주택 청년으로서 '.repeat(4) + '끝 문장';

  it('짧은 인용은 그대로, 긴 인용은 앞부분만 줄여 보인다', () => {
    expect(quotePreview('짧은 원문')).toBeNull();
    const preview = quotePreview(LONG) ?? '';
    expect(preview.endsWith('…')).toBe(true);
    expect(preview.length <= QUOTE_PREVIEW_LIMIT).toBe(true);
    expect(LONG.startsWith(preview.slice(0, -1))).toBe(true);
  });

  it('긴 인용은 [원문 전체 보기]로 펼칠 수 있고, 내부 위치는 숨긴다', () => {
    const long = renderToStaticMarkup(createElement(EvidenceQuote, { quote: LONG, location: 'chars:10-900' }));
    expect(long).toContain('원문 전체 보기');
    expect(long).toContain('aria-expanded="false"');
    expect(long).not.toContain('끝 문장');
    expect(long).not.toContain('chars:');
    const short = renderToStaticMarkup(createElement(EvidenceQuote, { quote: '짧은 원문', location: 'fields.srngMthdCn' }));
    expect(short).toContain('「선정 방법」');
    expect(short).toContain('짧은 원문');
    expect(short).not.toContain('원문 전체 보기');
  });
});

describe('S5: 조합 밖 목록에서 온 정책', () => {
  it('결과의 어느 목록에 있었는지 찾는다', () => {
    const result = JSON.parse(emptyText) as RecommendationResponse;
    const view = result.results['1y'].pv_off;
    view.closed_now = [{ policy_id: 'p:closed', last_period: null, next_expected: null }];
    view.not_eligible = [{ policy_id: 'p:ne', failed: ['selection_region_code'] }];
    view.not_yet_reviewed = [{ policy_id: 'p:nr', eligibility_summary: 'needs_review' }];
    expect(policyStatusInResult(result, ONE_YEAR, 'p:closed')).toEqual({ kind: 'closed' });
    expect(policyStatusInResult(result, ONE_YEAR, 'p:ne')).toEqual({ kind: 'notEligible', failed: ['selection_region_code'] });
    expect(policyStatusInResult(result, ONE_YEAR, 'p:nr')).toEqual({ kind: 'notReviewed', summary: 'needs_review' });
    expect(policyStatusInResult(result, ONE_YEAR, 'p:none')).toBeNull();
  });

  it('조합 없이 하나씩 보여 준 지원금은 fallback으로 알려 준다', () => {
    const result = JSON.parse(resultText) as RecommendationResponse;
    const view = result.results['1y'].pv_off;
    view.fallback_list = [{ policy_id: 'p:fb', amount_in_period_won: 3_600_000, reason: '개별 표시' }];
    expect(policyStatusInResult(result, ONE_YEAR, 'p:fb')).toEqual({ kind: 'fallback' });
  });
});

describe('S3 펼침·스크롤 기억', () => {
  it('뒤로 돌아온 경우에만 펼친 목록과 위치를 되살리고, 다른 결과면 새로 시작한다', () => {
    resetResultViewMemory();
    startResultView('r1', false);
    rememberFold('r1', 'closed', true);
    rememberScroll('r1', 0, 820);
    const back = startResultView('r1', true);
    expect([...back.open]).toEqual(['closed']);
    expect(back.screenTop).toBe(820);

    const fresh = startResultView('r1', false);
    expect(fresh.open.size).toBe(0);
    expect(startResultView('r2', true).screenTop).toBe(0);
    expect(foldElementId('notReviewed')).toBe('s3-fold-notReviewed');
  });
});

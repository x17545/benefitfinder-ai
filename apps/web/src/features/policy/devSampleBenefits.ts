// 개발용 가상 정책 상세. 정책 상세 API(GET /api/v1/benefits/{id})가 생기기 전까지 개발 서버에서만 쓴다.
// 이름·금액·문장은 모두 가상이며, 백엔드 1차 MVP의 P1·P3·P6 조건 이름에 맞췄다.
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';

const SOURCE = {
  url: 'https://policy.example/youth/sample-housing',
  checked_at: '2026-10-26T09:00:00+09:00',
  document_version: 'fictional-v1',
};

function evidence(quote: string, location: string): NormalizedBenefit['eligibility_conditions'][number]['evidence'] {
  return [{ quote: `[가상] ${quote}`, location, source: SOURCE }];
}

const SAMPLES: NormalizedBenefit[] = [
  {
    id: 'youth:example:P1',
    domain: 'youth',
    title: '[가상] 청년 월세 지원',
    summary: '[가상] 월세로 사는 무주택 청년에게 월세 일부를 지원해요.',
    provider: '[가상] 국토교통부',
    categories: ['housing'],
    application: { status: 'open', starts_on: '2026-10-01', ends_on: '2026-12-31', url: null },
    eligibility_conditions: [
      { id: 'age', description: '[가상] 만 19 ~ 34세', evidence: evidence('신청일 기준 만 19세 이상 34세 이하인 청년', '지원 대상') },
      { id: 'home_ownership', description: '[가상] 무주택', evidence: evidence('본인 명의의 주택을 소유하지 않은 사람', '지원 대상') },
      { id: 'monthly_rent', description: '[가상] 월세 계약', evidence: evidence('월세 임대차 계약을 맺고 거주 중인 사람', '지원 대상') },
      { id: 'income', description: '[가상] 연 소득 3,000만 원 이하', evidence: evidence('본인 연 소득이 3,000만 원 이하인 사람', '소득 기준') },
      { id: 'deposit', description: '[가상] 보증금 5천만 원 이하', evidence: evidence('임차 보증금 5천만 원 이하 주택', '주택 기준') },
      { id: 'parent_income', description: '[가상] 원가구 소득 기준', evidence: evidence('부모님을 포함한 원가구 소득이 기준 중위소득 100% 이하', '소득 기준') },
    ],
    benefit_amount: {
      type: 'monthly', amount_won: 200_000, period_months: 12, max_total_won: 2_400_000,
      pay_timing: 'monthly_from_start', amount_text: '[가상] 월 최대 20만 원 · 12개월', amount_status: 'verified',
    },
    reviewed: true,
    conflicts: [{ policy_id: 'youth:example:P6', evidence: evidence('청년 전세대출 이자 지원과 함께 받을 수 없어요', '중복 지원 제한') }],
    lifetime_once: true,
    source: SOURCE,
    domain_data: {},
  },
  {
    id: 'youth:example:P3',
    domain: 'youth',
    title: '[가상] 구직활동 지원금',
    summary: '[가상] 일자리를 찾는 청년에게 구직활동 비용을 지원해요.',
    provider: '[가상] 고용노동부',
    categories: ['employment'],
    application: { status: 'open', starts_on: null, ends_on: null, url: null },
    eligibility_conditions: [
      { id: 'age', description: '[가상] 만 19 ~ 34세', evidence: evidence('신청일 기준 만 19세 이상 34세 이하인 청년', '지원 대상') },
      { id: 'employment', description: '[가상] 구직 중', evidence: evidence('현재 취업하지 않고 구직활동 중인 사람', '지원 대상') },
    ],
    benefit_amount: {
      type: 'monthly', amount_won: 500_000, period_months: 6, max_total_won: 3_000_000,
      pay_timing: 'monthly_from_start', amount_text: '[가상] 월 50만 원 · 6개월', amount_status: 'verified',
    },
    reviewed: true,
    conflicts: [],
    lifetime_once: true,
    source: SOURCE,
    domain_data: {},
  },
  {
    id: 'youth:example:P6',
    domain: 'youth',
    title: '[가상] 청년 전세대출 이자 지원',
    summary: '[가상] 전세대출을 받은 무주택 청년의 이자 일부를 지원해요.',
    provider: '[가상] 주택도시기금',
    categories: ['housing'],
    application: { status: 'closed', starts_on: '2026-07-01', ends_on: '2026-08-31', url: null },
    eligibility_conditions: [
      { id: 'age', description: '[가상] 만 19 ~ 39세', evidence: evidence('신청일 기준 만 19세 이상 39세 이하인 청년', '지원 대상') },
      { id: 'home_ownership', description: '[가상] 무주택', evidence: evidence('세대원 모두 주택을 소유하지 않은 사람', '지원 대상') },
      { id: 'income', description: '[가상] 연 소득 5,000만 원 이하', evidence: evidence('본인 연 소득이 5,000만 원 이하인 사람', '소득 기준') },
    ],
    benefit_amount: {
      type: 'interest', amount_won: 1_500_000, period_months: 24, max_total_won: 3_000_000,
      pay_timing: 'yearly', amount_text: '[가상] 연 최대 150만 원 · 2년', amount_status: 'verified',
    },
    reviewed: true,
    conflicts: [{ policy_id: 'youth:example:P1', evidence: evidence('청년 월세 지원과 함께 받을 수 없어요', '중복 지원 제한') }],
    lifetime_once: false,
    source: SOURCE,
    domain_data: {},
  },
];

/** 개발 서버에서만 가상 정책 상세를 돌려준다. 배포 화면에서는 늘 null */
export function devSampleBenefit(id: string): NormalizedBenefit | null {
  if (!import.meta.env.DEV) {
    return null;
  }
  return SAMPLES.find((sample) => sample.id === id) ?? null;
}

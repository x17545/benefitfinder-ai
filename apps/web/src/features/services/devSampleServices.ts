// 개발용 가상 서비스 목록 (S6). 추천 결과에 services가 생기기 전까지 개발 서버에서만 쓴다.
// 이름·단가·기준은 모두 가상이며, 기획서 S6 시안의 예시를 따랐다.
import type { ServiceEntry } from './serviceModel.ts';

const SOURCE = {
  url: 'https://policy.example/youth/sample-service',
  checked_at: '2026-11-01T09:00:00+09:00',
  document_version: 'fictional-v1',
};

function met(conditionId: string, quote: string): ServiceEntry['eligibility'][number] {
  return {
    condition_id: conditionId,
    status: 'met',
    reason: '',
    evidence: [{ quote: `[가상] ${quote}`, location: '지원 대상', source: SOURCE }],
  };
}

function review(conditionId: string, reason: string): ServiceEntry['eligibility'][number] {
  return { condition_id: conditionId, status: 'needs_review', reason, evidence: [] };
}

const SAMPLES: ServiceEntry[] = [
  {
    policy_id: 'youth:example:S1',
    service: {
      item: '1톤 이사 차량과 인력 1회', unit: '회', quantity: 1, unit_value_won: 250_000,
      value_basis: '[가상] 이사 업체 3곳 견적 평균', value_source_url: SOURCE.url, value_checked_at: '2026-11-01',
      value_status: 'verified',
    },
    eligibility: [
      met('age', '신청일 기준 만 19세 이상 34세 이하인 청년'),
      review('home_ownership', '전입 신고 여부를 확인해야 해요'),
    ],
  },
  {
    policy_id: 'youth:example:S2',
    service: {
      item: '면접 정장 한 벌 대여 (3박 4일)', unit: '회', quantity: 3, unit_value_won: 40_000,
      value_basis: '[가상] 시중 대여점 3곳 가격', value_source_url: SOURCE.url, value_checked_at: '2026-11-01',
      value_status: 'verified',
    },
    eligibility: [
      met('age', '신청일 기준 만 19세 이상 34세 이하인 청년'),
      met('employment', '현재 취업하지 않고 구직활동 중인 사람'),
    ],
  },
  {
    policy_id: 'youth:example:S3',
    service: {
      item: '증명사진 촬영과 보정 1회', unit: '회', quantity: 1, unit_value_won: 30_000,
      value_basis: '[가상] 사진관 3곳 가격', value_source_url: SOURCE.url, value_checked_at: '2026-11-01',
      value_status: 'verified',
    },
    eligibility: [met('age', '신청일 기준 만 19세 이상 39세 이하인 청년')],
  },
  {
    policy_id: 'youth:example:S4',
    service: {
      item: '전문 상담사 1:1 상담 최대 5회', unit: '회', quantity: 5, unit_value_won: null,
      value_basis: '', value_source_url: null, value_checked_at: null, value_status: 'unknown',
    },
    eligibility: [
      met('age', '신청일 기준 만 19세 이상 34세 이하인 청년'),
      met('employment', '현재 취업하지 않고 구직활동 중인 사람'),
    ],
  },
];

/** 개발 서버에서만 가상 목록을 돌려준다. 배포 화면에서는 늘 null */
export function devSampleServices(): ServiceEntry[] | null {
  return import.meta.env.DEV ? SAMPLES : null;
}

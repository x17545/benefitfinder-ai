// S6 돈 말고 받을 수 있는 도움 (F14). 기획서 6장의 v3.0 초안 모양을 프론트 안에서만 임시로 쓴다.
// 공용 계약(contracts/v1)에는 아직 없으므로, 서버가 services를 보내지 않으면 배포 화면에서는 아무것도 보여 주지 않는다.
import type { RecommendationResponse } from '../../shared/api/recommendations.ts';
import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import { arrayOf, isEvidence, isHttpUrl, isNonEmptyString, isRecord } from '../../shared/api/responseValidation.ts';
import type { ConditionResult } from '../policy/policyDetailModel.ts';
import { devSampleServices } from './devSampleServices.ts';

/** 서비스 내용과 환산 기준 (기획서 6장 `service`) */
export interface ServiceValue {
  /** 예: 면접 정장 한 벌 대여 (3박 4일) */
  item: string;
  /** 예: 회 */
  unit: string;
  quantity: number;
  /** 1회 시중 가격. 근거가 없으면 null */
  unit_value_won: number | null;
  /** 예: [가상] 시중 대여점 3곳 가격 */
  value_basis: string;
  value_source_url: string | null;
  /** YYYY-MM-DD */
  value_checked_at: string | null;
  value_status: 'verified' | 'unknown';
}

/** S6 서비스 행 하나 (기획서 6장 `services`의 항목) */
export interface ServiceEntry {
  policy_id: string;
  service: ServiceValue;
  eligibility: ConditionResult[];
}

export interface ServiceList {
  entries: ServiceEntry[];
  /** 개발용 가상 예시로 본 것 */
  isSample: boolean;
  virtualData: boolean;
  available: boolean;
}

function isNullableNonNegative(value: unknown): boolean {
  return value === null || (typeof value === 'number' && Number.isSafeInteger(value) && value >= 0);
}

function isDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function isServiceValue(value: unknown): boolean {
  return (
    isRecord(value) &&
    isNonEmptyString(value.item) && isNonEmptyString(value.unit) &&
    typeof value.quantity === 'number' && Number.isSafeInteger(value.quantity) && value.quantity >= 1 &&
    isNullableNonNegative(value.unit_value_won) && typeof value.value_basis === 'string' &&
    (value.value_source_url === null || isNonEmptyString(value.value_source_url)) &&
    (value.value_checked_at === null || isNonEmptyString(value.value_checked_at)) &&
    (value.value_status === 'verified' || value.value_status === 'unknown')
  );
}

function isCondition(value: unknown): boolean {
  return (
    isRecord(value) && isNonEmptyString(value.condition_id) &&
    (value.status === 'met' || value.status === 'not_met' || value.status === 'needs_review') &&
    typeof value.reason === 'string' &&
    arrayOf(value.evidence, isEvidence, value.status === 'needs_review' ? 0 : 1)
  );
}

export function isServiceEntry(value: unknown): value is ServiceEntry {
  return (
    isRecord(value) && typeof value.policy_id === 'string' &&
    /^youth:[a-zA-Z0-9_:-]+$/.test(value.policy_id) && isServiceValue(value.service) &&
    arrayOf(value.eligibility, isCondition)
  );
}

/**
 * 추천 결과에서 서비스 목록을 꺼낸다. 모양이 틀린 항목은 버린다.
 * 서버가 services를 아예 보내지 않으면 개발 서버에서만 가상 예시를 쓴다.
 */
export function readServices(
  result: (RecommendationResponse & { services?: unknown }) | null, sampleAnalysis = false,
): ServiceList {
  if (result === null) {
    return { entries: [], isSample: false, virtualData: false, available: false };
  }
  const virtualData = sampleAnalysis || result.virtual_data === true;
  if ('services' in result) {
    if (!Array.isArray(result.services)) {
      return { entries: [], isSample: false, virtualData, available: false };
    }
    const valid = result.services.filter(isServiceEntry);
    const ids = new Set<string>();
    const entries = valid.filter((entry) => {
      if (ids.has(entry.policy_id)) return false;
      ids.add(entry.policy_id);
      return !entry.eligibility.some((condition) => condition.status === 'not_met');
    });
    return { entries, isSample: false, virtualData, available: result.services.length === 0 || valid.length > 0 };
  }
  const sample = devSampleServices();
  return sample !== null
    ? { entries: sample, isSample: true, virtualData: true, available: true }
    : { entries: [], isSample: false, virtualData, available: false };
}

/** 환산 금액 = 단가 × 횟수. 근거가 확인되지 않으면 null (기획서 F14-3) */
export function serviceValueWon(service: ServiceValue): number | null {
  if (service.value_status !== 'verified' || service.unit_value_won === null ||
    !isNullableNonNegative(service.unit_value_won) || !Number.isSafeInteger(service.quantity) || service.quantity < 1 ||
    service.value_basis.trim() === '' || !isHttpUrl(service.value_source_url) ||
    service.value_checked_at === null || !isDate(service.value_checked_at)) {
    return null;
  }
  const total = service.unit_value_won * service.quantity;
  return Number.isSafeInteger(total) ? total : null;
}

/** 근거 있는 금액만 합산한다. 환산 가능한 항목이 없거나 합계가 범위를 넘으면 미상이다. */
export function servicesTotalWon(entries: readonly ServiceEntry[]): number | null {
  let total: number | null = null;
  for (const entry of entries) {
    const value = serviceValueWon(entry.service);
    if (value === null) continue;
    total = (total ?? 0) + value;
    if (!Number.isSafeInteger(total)) return null;
  }
  return total;
}

/** 순서: 환산 금액 큰 순. 같으면 모집 중인 것 먼저 (기획서 4.7-1) */
export function sortServices(
  entries: readonly ServiceEntry[],
  isOpen: (policyId: string) => boolean,
): ServiceEntry[] {
  return [...entries].sort((a, b) => {
    const diff = (serviceValueWon(b.service) ?? -1) - (serviceValueWon(a.service) ?? -1);
    if (diff !== 0) {
      return diff;
    }
    return Number(isOpen(b.policy_id)) - Number(isOpen(a.policy_id));
  });
}

/** 문구 S6-BASE: "시중 가격 1회 약 40,000원 × 3회 · 기준 {출처} · 11/01 확인" */
export function valueBasisText(service: ServiceValue): string {
  const parts: string[] = [];
  if (service.unit_value_won !== null) {
    parts.push(
      `시중 가격 1${service.unit} 약 ${service.unit_value_won.toLocaleString('ko-KR')}원 × ${String(service.quantity)}${service.unit}`,
    );
  }
  if (service.value_basis !== '') {
    parts.push(`기준 ${service.value_basis}`);
  }
  if (service.value_checked_at !== null) {
    const [, month, day] = service.value_checked_at.split('-');
    if (month !== undefined && day !== undefined) {
      parts.push(`${month}/${day} 확인`);
    }
  }
  return parts.join(' · ');
}

/** 종료·예정 정책은 받을 수 있는 목록과 합계에서 제외한다. */
export function isCurrentService(benefit: NormalizedBenefit | undefined): boolean {
  return benefit?.application.status !== 'closed' && benefit?.application.status !== 'upcoming';
}

/** 임시 서비스 행에 별도 출처가 없으므로 진단 근거로 상세와 같은 문서인지 확인한다. */
export function findServiceForDetail(
  list: ServiceList, benefit: NormalizedBenefit, detailIsSample: boolean,
): ServiceEntry | null {
  if (list.isSample !== detailIsSample || list.virtualData !== (detailIsSample || benefit.virtual_data === true)) {
    return null;
  }
  const entry = list.entries.find((row) => row.policy_id === benefit.id);
  if (entry === undefined) return null;
  const evidence = entry.eligibility.flatMap((condition) => condition.evidence);
  return evidence.length > 0 && evidence.every(({ source }) =>
    source.url === benefit.source.url && source.document_version === benefit.source.document_version)
    ? entry : null;
}

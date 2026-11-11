import type { NormalizedBenefit } from '../../shared/api/benefits.ts';
import type { RecommendationResponse, RecommendedService } from '../../shared/api/generated/recommendation-response.ts';
import type { InKindService } from '../../shared/api/generated/service.ts';

/** 지난 분석을 복원해도 마감된 행과 그 합계를 다시 보여 주지 않는다. */
export function hasExpiredServices(entries: readonly RecommendedService[] | undefined, now = new Date()): boolean {
  const today = new Date(now.getTime() + 9 * 3_600_000).toISOString().slice(0, 10);
  return entries?.some(({ application }) => application.ends_on !== null && application.ends_on < today) ?? false;
}

/** 개인별 진단은 현재 분석과 공개 상세의 정책·문서 버전이 같을 때만 연결한다. */
export function findServiceForDetail(
  result: RecommendationResponse | null, benefit: NormalizedBenefit, sampleAnalysis: boolean,
): RecommendedService | null {
  if (result === null || benefit.service === undefined ||
    benefit.application.status === 'closed' || benefit.application.status === 'upcoming' ||
    (sampleAnalysis || result.virtual_data === true) !== (benefit.virtual_data === true)) return null;
  const { url, document_version } = benefit.source;
  return result.services?.find((entry) => entry.policy_id === benefit.id &&
    !hasExpiredServices([entry]) &&
    entry.source.url === url && entry.source.document_version === document_version &&
    entry.eligibility.every((condition) => condition.evidence.every(({ source }) =>
      source.url === url && source.document_version === document_version))) ?? null;
}

/** 문구 S6-BASE. 검증된 서버 환산의 근거만 보여 준다. */
export function valueBasisText(service: InKindService): string {
  if (service.estimated_value_won === null || service.unit_value_won === null ||
    service.value_basis === null || service.value_checked_at === null) return '';
  const [, month, day] = service.value_checked_at.split('-');
  return `시중 가격 1${service.unit} 약 ${service.unit_value_won.toLocaleString('ko-KR')}원 × ${String(service.quantity)}${service.unit} · 기준 ${service.value_basis} · ${month}/${day} 확인`;
}

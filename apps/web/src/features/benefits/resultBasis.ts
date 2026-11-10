import type { Period } from './format.ts';

export interface ResultBasis {
  period: Period;
  present_value: boolean;
}

/** 화면 표시 옵션만 주소에 담는다. 사용자 조건은 보내지 않는다. */
export function withResultBasis(path: string, basis: ResultBasis): string {
  const query = new URLSearchParams();
  if (basis.period !== '1y') query.set('period', basis.period);
  if (basis.present_value) query.set('pv', '1');
  return query.size === 0 ? path : `${path}?${query.toString()}`;
}

export function readResultBasis(search: string): ResultBasis {
  const query = new URLSearchParams(search);
  const period = query.get('period');
  return { period: period === '2y' || period === 'all' ? period : '1y', present_value: query.get('pv') === '1' };
}

import { useState } from 'react';
import type { JSX } from 'react';
import { Button } from '../../../shared/ui/Button.tsx';
import {
  clearCashFacts,
  getCashFactsForRegion,
  setCashFactsForRegion,
} from '../cashFactsMemory.ts';
import type { CashFacts } from '../cashFactsMemory.ts';

const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

const SAMPLE_JSON = [
  '{',
  '  "birth_date": "2001-03-15",',
  '  "exam": { "exam_date": "2026-09-01", "fee_won": 50000, "same_exam_awards": 0, "prior_awards_won": 0 },',
  '  "rent": { "deposit_won": 10000000, "contract_rent_won": 500000 },',
  '  "program_periods": []',
  '}',
].join('\n');

function toJsonText(saved: CashFacts): string {
  const rest: Record<string, unknown> = JSON.parse(JSON.stringify(saved)) as Record<string, unknown>;
  delete rest.birth_date;
  return Object.keys(rest).length === 0 ? '' : JSON.stringify(rest, null, 2);
}

/**
 * DEV-only panel for exact applicant facts. Step 13 only, local flag on,
 * region 41280 or 41820. Module memory only: nothing here touches storage,
 * saved profiles, or URLs. Shape checks stay shallow; the backend validates.
 */
export function CashFactsPanel({ regionCode }: { regionCode: string }): JSX.Element {
  const [birthDate, setBirthDate] = useState<string>(
    () => getCashFactsForRegion(regionCode)?.birth_date ?? '',
  );
  const [jsonText, setJsonText] = useState<string>(() => {
    const saved = getCashFactsForRegion(regionCode);
    return saved === null ? '' : toJsonText(saved);
  });
  const [error, setError] = useState<string | null>(null);
  const [hasFacts, setHasFacts] = useState<boolean>(() => getCashFactsForRegion(regionCode) !== null);

  function invalidate(message: string): void {
    setCashFactsForRegion(regionCode, null);
    setHasFacts(false);
    setError(message);
  }

  function apply(nextBirth: string, nextJson: string): void {
    const birth = nextBirth.trim();
    if (birth !== '' && !DATE_PATTERN.test(birth)) {
      invalidate('생년월일은 YYYY-MM-DD 형식으로 적어 주세요. 예: 2001-03-15');
      return;
    }
    const trimmed = nextJson.trim();
    let parsed: CashFacts = {};
    if (trimmed !== '') {
      let value: unknown;
      try {
        value = JSON.parse(trimmed) as unknown;
      } catch {
        invalidate('JSON을 읽지 못했어요. 쉼표와 따옴표를 확인해 주세요.');
        return;
      }
      if (typeof value !== 'object' || value === null || Array.isArray(value)) {
        invalidate('JSON 객체 { ... } 로 적어 주세요. 자세한 모양 검사는 서버가 해요.');
        return;
      }
      parsed = value as CashFacts;
    }
    const combined: CashFacts = { ...parsed };
    if (birth !== '') {
      combined.birth_date = birth;
    }
    setError(null);
    const facts = Object.keys(combined).length === 0 ? null : combined;
    setCashFactsForRegion(regionCode, facts);
    setHasFacts(facts !== null);
  }

  function handleClear(): void {
    setBirthDate('');
    setJsonText('');
    setError(null);
    clearCashFacts();
    setHasFacts(false);
  }

  const birthId = 'cashfacts-birth-date';
  const jsonId = 'cashfacts-json';

  return (
    <section className="s2-sub s2-cashfacts" aria-label="개발용 추가 사실 입력">
      <p className="s2-dev-note">개발용: 이 입력은 기기 메모리에만 두고 저장·기억하기에 넣지 않아요.</p>
      <h2 className="s2-sub__title">추가 사실 (선택)</h2>
      <p className="s2-sub__desc">
        정확한 생년월일과 시험·월세·다른 사업 참여 사실을 적으면 요청에만 포함돼요. program_periods를 빈 배열 [] 로 두면
        다른 사업에 없다는 뜻이에요. 비워 두면 기존 13단계 흐름 그대로 가요.
      </p>
      <label className="s2-field" htmlFor={birthId}>
        <span className="s2-field__label">생년월일 (YYYY-MM-DD)</span>
        <input
          id={birthId}
          className="s2-select"
          inputMode="numeric"
          placeholder="2001-03-15"
          autoComplete="off"
          value={birthDate}
          onChange={(event) => {
            const next = event.target.value;
            setBirthDate(next);
            apply(next, jsonText);
          }}
        />
      </label>
      <label className="s2-field" htmlFor={jsonId}>
        <span className="s2-field__label">시험·월세·참여 기간 (JSON)</span>
        <textarea
          id={jsonId}
          className="s2-textarea"
          rows={8}
          spellCheck={false}
          autoComplete="off"
          placeholder={SAMPLE_JSON}
          value={jsonText}
          onChange={(event) => {
            const next = event.target.value;
            setJsonText(next);
            apply(birthDate, next);
          }}
        />
      </label>
      {error !== null ? (
        <p className="s2-cashfacts__error" role="alert">
          {error}
        </p>
      ) : (
        <p className="s2-sub__desc" role="status">
          {hasFacts ? '입력됨: 다음 [내 조합 찾기] 요청에만 포함돼요.' : '비어 있음: 요청에 아무것도 붙지 않아요.'}
        </p>
      )}
      <div>
        <Button variant="secondary" onClick={handleClear}>
          추가 사실 지우기
        </Button>
      </div>
    </section>
  );
}

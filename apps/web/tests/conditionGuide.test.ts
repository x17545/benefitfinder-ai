import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { HOUSEHOLD_HEAD_CHOICES } from '../src/domains/youth/conditionOptions.ts';
import { TERM_HELP } from '../src/domains/youth/termHelp.ts';
import { ConditionStepLayout } from '../src/features/profile/ConditionStepLayout.tsx';
import {
  firstUnanswered,
  questionElementId,
  remainingQuestionsHint,
} from '../src/features/profile/stepQuestions.ts';
import { ChipQuestion } from '../src/features/profile/steps/ChipQuestion.tsx';

const HOUSING = [
  { id: 'living', answered: true },
  { id: 'homeOwnership', answered: false },
  { id: 'householdHead', answered: false },
] as const;

describe('남은 질문 안내', () => {
  it('[다음]이 꺼져 있고 답하지 않은 질문이 있을 때만 개수를 알려 준다', () => {
    expect(remainingQuestionsHint(HOUSING, false)).toBe('아직 고르지 않은 질문이 2개 있어요');
    expect(remainingQuestionsHint(HOUSING, true)).toBeNull();
    expect(remainingQuestionsHint([{ id: 'age', answered: false }], false)).toBeNull();
    expect(remainingQuestionsHint(HOUSING.map((question) => ({ ...question, answered: true })), false)).toBeNull();
    expect(firstUnanswered(HOUSING)?.id).toBe('homeOwnership');
    expect(questionElementId('homeOwnership')).toBe('s2-q-homeOwnership');
  });

  it('하단 [다음] 위에 안내 버튼을 보이고, 질문이 하나뿐이면 보이지 않는다', () => {
    const render = (questions: readonly { id: string; answered: boolean }[]) => renderToStaticMarkup(
      createElement(ConditionStepLayout, { step: 6, title: '어떻게 살고 있나요?', canNext: false, questions, children: null }),
    );
    const html = render(HOUSING);
    expect(html).toContain('class="s2-remaining"');
    expect(html).toContain('아직 고르지 않은 질문이 2개 있어요');
    expect(html.indexOf('s2-remaining')).toBeGreaterThan(html.indexOf('s2-bottom'));
    expect(render([])).not.toContain('s2-remaining');
  });
});

describe('어려운 용어 설명', () => {
  it('질문 아래에 펼침 설명을 붙이고 이동용 id를 단다', () => {
    const html = renderToStaticMarkup(createElement(ChipQuestion<string>, {
      title: '주민등록상 세대주인가요?',
      questionId: 'householdHead',
      help: TERM_HELP.householdHead,
      choices: HOUSEHOLD_HEAD_CHOICES,
      value: null,
      onChange: () => undefined,
    }));
    expect(html).toContain('id="s2-q-householdHead"');
    expect(html).toContain('<details class="s2-term">');
    // 한 줄 뜻은 summary 안에 있어 펼치지 않아도 보이고, 자세한 설명은 펼침 안에 있다
    const summary = html.match(/<summary[\s\S]*?<\/summary>/)?.[0] ?? '';
    expect(summary).toContain('<b>세대주</b>');
    expect(summary).toContain(TERM_HELP.householdHead.short);
    expect(summary).toContain('자세히');
    expect(html).toContain('정부24에서 등본을 떼 보면');
  });

  it('정책마다 다른 기준(무주택·재산·가구원 수)은 단정하지 않고 정책별 확인을 안내한다', () => {
    for (const key of ['homeOwnership', 'asset', 'householdSize'] as const) {
      expect(TERM_HELP[key].answer).toContain('정책마다 달라');
    }
    expect(TERM_HELP.homeOwnership.answer).toContain('세대원');
    expect(TERM_HELP.homeOwnership.answer).toContain('분양권·입주권');
    expect(TERM_HELP.asset.answer).toContain('빚');
  });

  it('설명은 모두 짧은 한 줄 뜻과 한두 문장의 자세한 설명을 가진다', () => {
    for (const { term, short, answer } of Object.values(TERM_HELP)) {
      expect(term.length).toBeGreaterThan(1);
      expect(short.length < 60).toBe(true);
      expect(answer.length).toBeGreaterThan(20);
      expect(answer.length < 140).toBe(true);
    }
  });

  it('사용자 테스트에서 어렵다고 한 부모님 재산과 특수 조건에도 설명이 있다', () => {
    expect(TERM_HELP.parentAsset.short).toContain('몰라도');
    expect(TERM_HELP.parentAsset.answer).toContain('확인 필요');
    expect(TERM_HELP.special.term).toContain('차상위');
  });
});

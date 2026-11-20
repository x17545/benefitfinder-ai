// 한 화면에 질문이 여러 개인 단계(1·6·7·8)에서, 아래쪽 질문을 못 보고 [다음]이 왜 꺼졌는지
// 모르는 일이 없도록 남은 질문 수를 알려 주고 눌러서 그 질문으로 이동하게 한다.

export interface StepQuestion {
  /** 화면 안의 질문 구분 이름. DOM id는 questionElementId(id) */
  id: string;
  answered: boolean;
}

/** 질문 묶음의 DOM id */
export function questionElementId(id: string): string {
  return `s2-q-${id}`;
}

/**
 * 하단 안내 문구. [다음]이 꺼져 있고, 질문이 2개 이상이며, 답하지 않은 질문이 있을 때만 보인다.
 * 선택 질문만 있는 단계(12)는 [다음]이 늘 켜져 있으므로 보이지 않는다.
 */
export function remainingQuestionsHint(questions: readonly StepQuestion[], canNext: boolean): string | null {
  if (canNext || questions.length < 2) {
    return null;
  }
  const left = questions.filter((question) => !question.answered).length;
  return left > 0 ? `아직 고르지 않은 질문이 ${String(left)}개 있어요` : null;
}

/** 처음으로 답하지 않은 질문 */
export function firstUnanswered(questions: readonly StepQuestion[]): StepQuestion | undefined {
  return questions.find((question) => !question.answered);
}

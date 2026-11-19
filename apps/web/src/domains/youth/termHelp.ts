// S2 조건 입력의 어려운 용어 설명 (펼침). 정책마다 기준이 다른 것은 단정하지 않고 그렇다고 알린다.

export interface TermHelpContent {
  /** 펼치기 전 보이는 질문. 예: 세대주가 뭐예요? */
  question: string;
  /** 펼치면 보이는 한두 줄 설명 */
  answer: string;
}

export const TERM_HELP = {
  homeOwnership: {
    question: '무주택이 뭐예요?',
    answer:
      '본인(또는 배우자) 이름으로 된 집이 없으면 보통 무주택으로 봐요. 여기서는 분양권·입주권도 집으로 쳐 주세요. 세대원까지 보는지, 분양권·입주권을 집으로 치는지는 정책마다 달라요.',
  },
  householdHead: {
    question: '세대주가 뭐예요?',
    answer:
      "주민등록등본 맨 위에 '세대주'로 적힌 사람이에요. 혼자 전입신고를 했다면 보통 본인이 세대주예요. 정부24에서 등본을 떼 보면 알 수 있어요.",
  },
  income: {
    question: '세전 연 소득은 어떻게 봐요?',
    answer: '세금과 4대 보험료를 떼기 전, 1년 동안 번 돈이에요. 아르바이트·프리랜서 소득도 더해요.',
  },
  asset: {
    question: '재산은 어디까지 세요?',
    answer:
      '보통 예금·주식·전월세 보증금·자동차·집과 땅을 더하고 대출 같은 빚을 빼요. 어떤 재산과 빚을 넣고 빼는지는 정책마다 달라 공고에서 확인해 주세요.',
  },
  householdSize: {
    question: '가구원 수는 누구까지 세요?',
    answer:
      '가구원을 어디까지 세는지는 정책마다 달라요. 보통 주민등록상 함께 올라 있는 가족을 세고, 부모님 가구(원가구)를 기준으로 보는 정책도 있어요. 정확한 기준은 공고에서 확인해 주세요.',
  },
  parentIncome: {
    question: '부모님 소득은 왜 봐요?',
    answer:
      '따로 살아도 부모님 가구(원가구)의 소득을 함께 보는 정책이 있어요. 모르면 모름을 골라도 돼요. 그 조건은 확인 필요로 표시돼요.',
  },
} as const satisfies Record<string, TermHelpContent>;

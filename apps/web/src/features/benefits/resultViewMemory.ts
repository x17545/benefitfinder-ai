// S3에서 정책 상세로 갔다가 [뒤로]로 돌아오면, 펼쳐 둔 목록과 스크롤 위치를 되살린다.
// 이 탭의 메모리에만 두고 저장소에는 쓰지 않는다. 다른 분석 결과(request_id)면 새로 시작한다.

export type FoldId = 'excluded' | 'closed' | 'notEligible' | 'notReviewed';

interface ResultViewMemory {
  key: string;
  open: Set<FoldId>;
  windowY: number;
  screenTop: number;
}

let memory: ResultViewMemory = { key: '', open: new Set(), windowY: 0, screenTop: 0 };

function forKey(key: string): ResultViewMemory {
  if (memory.key !== key) {
    memory = { key, open: new Set(), windowY: 0, screenTop: 0 };
  }
  return memory;
}

/** 결과 화면이 열릴 때. restore가 false면(새로 들어온 경우) 기억을 지운다 */
export function startResultView(key: string, restore: boolean): {
  open: ReadonlySet<FoldId>; windowY: number; screenTop: number;
} {
  const current = forKey(key);
  if (!restore) {
    current.open = new Set();
    current.windowY = 0;
    current.screenTop = 0;
  }
  return { open: new Set(current.open), windowY: current.windowY, screenTop: current.screenTop };
}

export function rememberFold(key: string, id: FoldId, open: boolean): void {
  const current = forKey(key);
  if (open) {
    current.open.add(id);
  } else {
    current.open.delete(id);
  }
}

export function rememberScroll(key: string, windowY: number, screenTop: number): void {
  const current = forKey(key);
  current.windowY = windowY;
  current.screenTop = screenTop;
}

/** 테스트용: 기억을 비운다 */
export function resetResultViewMemory(): void {
  memory = { key: '', open: new Set(), windowY: 0, screenTop: 0 };
}

/** 접힌 목록의 DOM id */
export function foldElementId(id: FoldId): string {
  return `s3-fold-${id}`;
}

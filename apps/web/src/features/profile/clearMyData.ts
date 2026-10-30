import { showToast } from '../../shared/ui/toastStore.ts';
import { clearFlowState } from './flowStore.ts';
import { removeSavedConditions } from './savedConditions.ts';

/** 하단 [내 정보 지우기]: 저장한 조건과 마지막 결과를 바로 지운다 (F09-5, 토스트 T-CLEAR) */
export function clearMyData(): void {
  removeSavedConditions();
  clearFlowState();
  showToast('이 기기에 저장된 내 정보를 지웠어요');
}

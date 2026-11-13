import { describe, expect, it } from 'vitest';
import { FAILURE_COPY, failureCopy, failureReason } from '../src/features/benefits/analysisFailure.ts';
import { postRecommendations, RecommendationError } from '../src/shared/api/recommendations.ts';
import type { RecommendationRequest } from '../src/shared/api/recommendations.ts';

const REQUEST = { domain: 'youth', profile: {} } as unknown as RecommendationRequest;

function reply(body: string, status: number): typeof fetch {
  return async () => new Response(body, { status, headers: { 'Content-Type': 'application/json' } });
}

describe('분석 실패 안내 (L-E1)', () => {
  it('503 정책 자료 없음, 연결 실패, 하루 상한, 그 밖의 실패를 구분한다', () => {
    expect(failureReason(new RecommendationError('dependency_unavailable'), false)).toBe('unavailable');
    expect(failureReason(new RecommendationError('network_error'), false)).toBe('network');
    expect(failureReason(new RecommendationError('daily_limit_exceeded'), false)).toBe('limit');
    expect(failureReason(new RecommendationError('internal_error'), false)).toBe('error');
    expect(failureReason(new Error('boom'), false)).toBe('error');
    // 시간 초과는 원인을 단정하지 않는다
    expect(failureReason(new RecommendationError('network_error'), true)).toBe('error');
  });

  it('본문 없는 502·503·504도 정책 자료를 쓸 수 없는 상태로 본다', async () => {
    for (const status of [502, 503, 504]) {
      await expect(postRecommendations(REQUEST, { fetchFn: reply('<html>down</html>', status) })).rejects.toMatchObject({
        code: 'dependency_unavailable',
      });
    }
    await expect(postRecommendations(REQUEST, { fetchFn: reply('', 500) })).rejects.toMatchObject({ code: 'http_error' });
  });

  it('서버가 보낸 503 dependency_unavailable 본문을 그대로 쓴다', async () => {
    const body = JSON.stringify({ request_id: 'r', code: 'dependency_unavailable', message: '정책 자료를 조회할 수 없습니다.' });
    await expect(postRecommendations(REQUEST, { fetchFn: reply(body, 503) })).rejects.toMatchObject({
      code: 'dependency_unavailable',
    });
  });

  it('503 안내는 입력 조건 문제가 아니라고 알리고, 모든 안내는 조건이 남아 있다고 알린다', () => {
    expect(FAILURE_COPY.unavailable.title).toBe('지금은 정책 자료를 불러올 수 없어요');
    expect(FAILURE_COPY.unavailable.description).toContain('입력한 조건 문제는 아니에요');
    expect(failureCopy('limit')).toEqual(FAILURE_COPY.error);
    for (const copy of Object.values(FAILURE_COPY)) {
      expect(copy.description).toContain('고른 조건은 그대로 남아 있어요.');
    }
  });
});

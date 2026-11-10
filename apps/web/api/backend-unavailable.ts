import type { PublicErrorResponse } from '../src/shared/api/generated/error.ts';

/** An unconnected preview must neither call production nor claim API readiness. */
export default {
  fetch(): Response {
    const error: PublicErrorResponse = {
      request_id: crypto.randomUUID(),
      code: 'dependency_unavailable',
      message: '이 미리보기는 서버에 연결되지 않았어요. 나중에 다시 시도해 주세요.',
    };
    return Response.json(error, {
      status: 503,
      headers: { 'Cache-Control': 'no-store' },
    });
  },
};

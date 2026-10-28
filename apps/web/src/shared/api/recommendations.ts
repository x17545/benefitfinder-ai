import type { PublicErrorResponse } from './generated/error.ts';
import type { RecommendationRequest } from './generated/recommendation-request.ts';
import type { RecommendationResponse } from './generated/recommendation-response.ts';
import type { SSEProgressEventData } from './generated/sse-progress.ts';
import { RECOMMENDATIONS_PATH } from '../config/api.ts';

export type { RecommendationRequest, RecommendationResponse, SSEProgressEventData };

/** 서버 오류 코드 + 화면에서 구분할 두 가지 (연결 실패, 약속과 다른 응답) */
export type RecommendationErrorCode = PublicErrorResponse['code'] | 'network_error' | 'invalid_response';

const SERVER_ERROR_CODES: readonly string[] = [
  'http_error',
  'invalid_request',
  'unsupported_domain',
  'not_found',
  'method_not_allowed',
  'dependency_unavailable',
  'internal_error',
  'daily_limit_exceeded',
  'calculation_limit_exceeded',
];

export class RecommendationError extends Error {
  readonly code: RecommendationErrorCode;

  constructor(code: RecommendationErrorCode) {
    super(`recommendation failed: ${code}`);
    this.name = 'RecommendationError';
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isResultView(value: unknown): boolean {
  return isRecord(value) && Array.isArray(value.portfolios) && typeof value.calculation_status === 'string';
}

function isPeriodResult(value: unknown): boolean {
  return isRecord(value) && isResultView(value.pv_off) && isResultView(value.pv_on);
}

/** 추천 결과가 약속(contracts/v1)의 큰 틀과 맞는지 확인한다 */
export function isRecommendationResponse(value: unknown): value is RecommendationResponse {
  if (!isRecord(value) || !isRecord(value.results)) {
    return false;
  }
  const { results } = value;
  return (
    typeof value.request_id === 'string' &&
    (value.view === 'portfolio' || value.view === 'fallback_list' || value.view === 'empty') &&
    isPeriodResult(results['1y']) &&
    isPeriodResult(results['2y']) &&
    isPeriodResult(results.all)
  );
}

function isProgress(value: unknown): value is SSEProgressEventData {
  return (
    isRecord(value) &&
    typeof value.step === 'string' &&
    typeof value.label === 'string' &&
    (value.state === 'running' || value.state === 'done' || value.state === 'failed')
  );
}

function errorCodeFrom(body: unknown, status: number): RecommendationErrorCode {
  if (isRecord(body) && typeof body.code === 'string' && SERVER_ERROR_CODES.includes(body.code)) {
    return body.code as PublicErrorResponse['code'];
  }
  return status === 429 ? 'daily_limit_exceeded' : 'http_error';
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

export interface SseEvent {
  event: string;
  data: string;
}

/** SSE 글자를 받는 대로 넣으면 끝난 이벤트만 돌려준다 (빈 줄이 이벤트 끝) */
export function createSseParser(): { push: (text: string) => SseEvent[] } {
  let buffer = '';
  return {
    push(text: string): SseEvent[] {
      buffer += text.replace(/\r\n?/g, '\n');
      const events: SseEvent[] = [];
      let end = buffer.indexOf('\n\n');
      while (end !== -1) {
        const block = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        let event = 'message';
        const data: string[] = [];
        for (const line of block.split('\n')) {
          if (line === '' || line.startsWith(':')) {
            continue;
          }
          const colon = line.indexOf(':');
          const field = colon === -1 ? line : line.slice(0, colon);
          let value = colon === -1 ? '' : line.slice(colon + 1);
          if (value.startsWith(' ')) {
            value = value.slice(1);
          }
          if (field === 'event') {
            event = value;
          } else if (field === 'data') {
            data.push(value);
          }
        }
        if (data.length > 0) {
          events.push({ event, data: data.join('\n') });
        }
        end = buffer.indexOf('\n\n');
      }
      return events;
    },
  };
}

async function readEventStream(
  response: Response,
  onProgress?: (progress: SSEProgressEventData) => void,
): Promise<RecommendationResponse> {
  if (response.body === null) {
    throw new RecommendationError('invalid_response');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = createSseParser();

  for (;;) {
    const { done, value } = await reader.read();
    const text = done ? decoder.decode() + '\n\n' : decoder.decode(value, { stream: true });
    for (const event of parser.push(text)) {
      const data = parseJson(event.data);
      if (event.event === 'progress' && isProgress(data)) {
        onProgress?.(data);
      } else if (event.event === 'result') {
        if (!isRecommendationResponse(data)) {
          throw new RecommendationError('invalid_response');
        }
        await reader.cancel();
        return data;
      } else if (event.event === 'error') {
        throw new RecommendationError(errorCodeFrom(data, 500));
      }
    }
    if (done) {
      throw new RecommendationError('invalid_response');
    }
  }
}

export interface PostRecommendationsOptions {
  signal?: AbortSignal;
  /** SSE 진행 이벤트를 받을 때마다 */
  onProgress?: (progress: SSEProgressEventData) => void;
  /** 테스트에서 바꿔 끼우는 fetch */
  fetchFn?: typeof fetch;
}

/**
 * POST /api/v1/recommendations.
 * 서버가 SSE(text/event-stream)로 답하면 진행 이벤트를 읽고, JSON으로 답하면 결과를 바로 읽는다.
 */
export async function postRecommendations(
  request: RecommendationRequest,
  options: PostRecommendationsOptions = {},
): Promise<RecommendationResponse> {
  const { signal, onProgress, fetchFn = fetch } = options;

  let response: Response;
  try {
    response = await fetchFn(RECOMMENDATIONS_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream, application/json' },
      body: JSON.stringify(request),
      signal,
    });
  } catch (error) {
    if (signal?.aborted === true) {
      throw error;
    }
    throw new RecommendationError('network_error');
  }

  if (!response.ok) {
    const body = parseJson(await response.text());
    throw new RecommendationError(errorCodeFrom(body, response.status));
  }

  if ((response.headers.get('Content-Type') ?? '').includes('text/event-stream')) {
    return readEventStream(response, onProgress);
  }

  const body = parseJson(await response.text());
  if (!isRecommendationResponse(body)) {
    throw new RecommendationError('invalid_response');
  }
  return body;
}

import { describe, expect, it } from 'vitest';
import {
  createSseParser,
  isRecommendationResponse,
  postRecommendations,
  RecommendationError,
} from '../src/shared/api/recommendations.ts';
import type { RecommendationRequest } from '../src/shared/api/recommendations.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';
import emptyText from '../../../contracts/fixtures/empty-response.json?raw';
import fallbackText from '../../../contracts/fixtures/fallback-response.json?raw';
import unavailableText from '../../../contracts/fixtures/pv-unknown-response.json?raw';

const FIXTURE: unknown = JSON.parse(fixtureText);

const REQUEST: RecommendationRequest = { domain: 'youth', profile: { age: 25 } };

function textResponse(body: string, status: number, type: string): Response {
  return new Response(body, { status, headers: { 'Content-Type': type } });
}

/** 가상 응답의 특정 필드만 손상시킨다. 실제 오류 본문·프로필은 사용하지 않는다. */
function replaceField(path: readonly (string | number)[], value: unknown): unknown {
  const result: unknown = JSON.parse(fixtureText);
  let target = result;
  for (const key of path.slice(0, -1)) {
    if (typeof target !== 'object' || target === null) { throw new Error('missing fixture path'); }
    target = Reflect.get(target, key);
  }
  if (typeof target !== 'object' || target === null) { throw new Error('missing fixture target'); }
  Reflect.set(target, path[path.length - 1], value);
  return result;
}

describe('isRecommendationResponse', () => {
  it('accepts the contract example', () => {
    expect(isRecommendationResponse(FIXTURE)).toBe(true);
  });

  it('rejects broken shapes', () => {
    expect(isRecommendationResponse(null)).toBe(false);
    expect(isRecommendationResponse({ request_id: 'x', view: 'portfolio', results: {} })).toBe(false);
  });

  it.each(['extra', '3y', 'summary'])('rejects an unvalidated results.%s key', (key) => {
    expect(isRecommendationResponse(replaceField(['results', key], {}))).toBe(false);
  });

  it.each([emptyText, fallbackText, unavailableText])('accepts empty, nullable fallback and unavailable fixtures', (text) => {
    const result: unknown = JSON.parse(text);
    expect(isRecommendationResponse(result)).toBe(true);
  });

  const view = ['results', '1y', 'pv_off'];
  it.each<{ name: string; path: (string | number)[]; value: unknown }>([
    { name: 'portfolio without policies', path: [...view, 'portfolios'], value: [{}] },
    { name: 'null portfolio', path: [...view, 'portfolios'], value: [null] },
    { name: 'null policy', path: [...view, 'portfolios', 0, 'policies'], value: [null] },
    { name: 'missing eligibility', path: [...view, 'portfolios', 0, 'policies', 0, 'eligibility'], value: undefined },
    { name: 'unsupported eligibility status', path: [...view, 'portfolios', 0, 'policies', 0, 'eligibility', 0, 'status'], value: 'need_check' },
    { name: 'confirmed condition without evidence', path: [...view, 'portfolios', 0, 'policies', 1, 'eligibility', 0, 'evidence'], value: [] },
    { name: 'null policy source', path: [...view, 'portfolios', 0, 'policies', 0, 'source'], value: null },
    { name: 'non-HTTP source URL', path: [...view, 'portfolios', 0, 'policies', 0, 'source', 'url'], value: 'javascript:alert(1)' },
    { name: 'null given-up item', path: [...view, 'portfolios', 0, 'given_up'], value: [null] },
    { name: 'broken unverified pair', path: [...view, 'portfolios', 0, 'unverified_pairs'], value: [{ a: 'A' }] },
    { name: 'broken exclusion evidence', path: [...view, 'excluded', 0, 'evidence'], value: [{}] },
    { name: 'broken failed-conditions list', path: [...view, 'not_eligible', 0, 'failed'], value: null },
    { name: 'invalid review status', path: [...view, 'not_yet_reviewed', 0, 'eligibility_summary'], value: 'unknown' },
    { name: 'null closed item', path: [...view, 'closed_now'], value: [null] },
    { name: 'broken fallback item', path: [...view, 'fallback_list'], value: [{}] },
    { name: 'non-string deciding policy', path: [...view, 'why', 'deciding_conflict'], value: [{}] },
    { name: 'missing calculation reason', path: [...view, 'unavailable_reason'], value: undefined },
    { name: 'invalid later PV branch', path: ['results', 'all', 'pv_on', 'portfolios'], value: [{}] },
    { name: 'missing AI metadata', path: ['ai_generated'], value: undefined },
    { name: 'non-numeric discount rate', path: ['discount_rate'], value: 'unknown' },
  ])('rejects $name before it reaches rendering', ({ path, value }) => {
    expect(isRecommendationResponse(replaceField(path, value))).toBe(false);
  });
});

describe('createSseParser', () => {
  it('returns events only when they end, even across chunks', () => {
    const parser = createSseParser();
    expect(parser.push('event: progress\ndata: {"a"')).toEqual([]);
    expect(parser.push(':1}\n\nevent: result\r\ndata: {}\r\n\r\n')).toEqual([
      { event: 'progress', data: '{"a":1}' },
      { event: 'result', data: '{}' },
    ]);
  });

  it('keeps CRLF together at every possible chunk boundary', () => {
    const stream = 'event: progress\r\ndata: {"step":"search"}\r\n\r\nevent: result\r\ndata: {}\r\n\r\n';
    for (let split = 0; split <= stream.length; split += 1) {
      const parser = createSseParser();
      expect([...parser.push(stream.slice(0, split)), ...parser.push(stream.slice(split))]).toEqual([
        { event: 'progress', data: '{"step":"search"}' },
        { event: 'result', data: '{}' },
      ]);
    }
  });

  it('accepts one-character chunks, comments, CR-only and multiline data', () => {
    const parser = createSseParser();
    const stream = ': heartbeat\r\nevent: result\rdata: first\r\ndata: second\r\n\r\n';
    expect([...stream].flatMap((char) => parser.push(char))).toEqual([
      { event: 'result', data: 'first\nsecond' },
    ]);
  });
});

describe('postRecommendations', () => {
  it.each(['application/json', 'text/event-stream'])('rejects extra result branches from %s', async (type) => {
    const json = JSON.stringify(replaceField(['results', 'extra'], {}));
    const body = type === 'application/json' ? json : `event: result\ndata: ${json}\n\n`;
    const fetchFn: typeof fetch = async () => textResponse(body, 200, type);
    await expect(postRecommendations(REQUEST, { fetchFn })).rejects.toMatchObject({ code: 'invalid_response' });
  });

  it.each(['application/json', 'text/event-stream'])('rejects a malformed nested result from %s', async (type) => {
    const broken = replaceField(['results', '1y', 'pv_off', 'portfolios'], [{}]);
    const json = JSON.stringify(broken);
    const body = type === 'application/json' ? json : `event: result\ndata: ${json}\n\n`;
    const fetchFn: typeof fetch = async () => textResponse(body, 200, type);
    await expect(postRecommendations(REQUEST, { fetchFn })).rejects.toMatchObject({ code: 'invalid_response' });
  });

  it('reads a JSON response', async () => {
    const fetchFn: typeof fetch = async () => textResponse(JSON.stringify(FIXTURE), 200, 'application/json');
    await expect(postRecommendations(REQUEST, { fetchFn })).resolves.toEqual(FIXTURE);
  });

  it('reads SSE progress and result events', async () => {
    const stream =
      'event: progress\ndata: {"step":"search_policies","state":"done","label":"정책 찾기","detail":"3개"}\n\n' +
      `event: result\ndata: ${JSON.stringify(FIXTURE)}\n\n`;
    const fetchFn: typeof fetch = async () => textResponse(stream, 200, 'text/event-stream');
    const steps: string[] = [];
    await expect(
      postRecommendations(REQUEST, {
        fetchFn,
        onProgress: (progress) => {
          steps.push(progress.step);
        },
      }),
    ).resolves.toEqual(FIXTURE);
    expect(steps).toEqual(['search_policies']);
  });

  it('reads a real byte stream split inside CRLF and Korean UTF-8 characters', async () => {
    const text =
      'event: progress\r\ndata: {"step":"search_policies","state":"done","label":"정책 찾기"}\r\n\r\n' +
      `event: result\r\ndata: ${JSON.stringify(FIXTURE)}\r\n\r\n`;
    const bytes = new TextEncoder().encode(text);
    const fetchFn: typeof fetch = async () => new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        for (const byte of bytes) {
          controller.enqueue(Uint8Array.of(byte));
        }
        controller.close();
      },
    }), { headers: { 'Content-Type': 'text/event-stream' } });
    const labels: string[] = [];
    await expect(postRecommendations(REQUEST, {
      fetchFn,
      onProgress: (progress) => { labels.push(progress.label); },
    })).resolves.toEqual(FIXTURE);
    expect(labels).toEqual(['정책 찾기']);
  });

  it('keeps an SSE error terminal even when a result follows in the same chunk', async () => {
    const error = { request_id: 'r', code: 'daily_limit_exceeded', message: 'limit' };
    const text = `event: error\r\ndata: ${JSON.stringify(error)}\r\n\r\nevent: result\r\ndata: ${JSON.stringify(FIXTURE)}\r\n\r\n`;
    const fetchFn: typeof fetch = async () => textResponse(text, 200, 'text/event-stream');
    await expect(postRecommendations(REQUEST, { fetchFn })).rejects.toMatchObject({ code: 'daily_limit_exceeded' });
  });

  it('turns HTTP 429 into the daily limit code', async () => {
    const fetchFn: typeof fetch = async () => textResponse('', 429, 'text/plain');
    await expect(postRecommendations(REQUEST, { fetchFn })).rejects.toMatchObject({
      code: 'daily_limit_exceeded',
    });
  });

  it('uses the server error code and fails on a broken body', async () => {
    const error = JSON.stringify({ request_id: 'r', code: 'calculation_limit_exceeded', message: 'x' });
    const failing: typeof fetch = async () => textResponse(error, 503, 'application/json');
    await expect(postRecommendations(REQUEST, { fetchFn: failing })).rejects.toBeInstanceOf(RecommendationError);
    await expect(postRecommendations(REQUEST, { fetchFn: failing })).rejects.toMatchObject({
      code: 'calculation_limit_exceeded',
    });

    const broken: typeof fetch = async () => textResponse('{"view":"portfolio"}', 200, 'application/json');
    await expect(postRecommendations(REQUEST, { fetchFn: broken })).rejects.toMatchObject({
      code: 'invalid_response',
    });
  });
});

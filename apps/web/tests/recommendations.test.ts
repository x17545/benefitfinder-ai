import { describe, expect, it } from 'vitest';
import {
  createSseParser,
  isRecommendationResponse,
  postRecommendations,
  RecommendationError,
} from '../src/shared/api/recommendations.ts';
import type { RecommendationRequest } from '../src/shared/api/recommendations.ts';
import fixtureText from '../../../contracts/fixtures/recommendation-response.json?raw';

const FIXTURE: unknown = JSON.parse(fixtureText);

const REQUEST: RecommendationRequest = { domain: 'youth', profile: { age: 25 } };

function textResponse(body: string, status: number, type: string): Response {
  return new Response(body, { status, headers: { 'Content-Type': type } });
}

describe('isRecommendationResponse', () => {
  it('accepts the contract example', () => {
    expect(isRecommendationResponse(FIXTURE)).toBe(true);
  });

  it('rejects broken shapes', () => {
    expect(isRecommendationResponse(null)).toBe(false);
    expect(isRecommendationResponse({ request_id: 'x', view: 'portfolio', results: {} })).toBe(false);
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

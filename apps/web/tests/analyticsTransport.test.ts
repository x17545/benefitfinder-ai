import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AnalyticsEvent } from '../src/shared/api/generated/analytics-event.ts';

const EVENTS: AnalyticsEvent[] = [
  { name: 'consent_check' }, { name: 'remember_on' }, { name: 'step_view', step: 1 },
  { name: 'step_complete', step: 13 }, { name: 'step_skip' }, { name: 'analyze_start' },
  { name: 'analyze_success', duration_ms: 1200, portfolio_count: 3 },
  { name: 'analyze_fail', code: 'daily_limit_exceeded' },
  { name: 'period_change', period: 'all' }, { name: 'pv_toggle', on: true },
  { name: 'combo_select', rank: 2 }, { name: 'detail_view', policy_id: 'fictional:policy-1' },
  { name: 'apply_click', policy_id: 'fictional:policy-1', from: 'result', rank: 1 },
  { name: 'whatif_open' }, { name: 'whatif_select', scenario: 'employed' },
  { name: 'image_save' }, { name: 'clear_mine' },
];
const CONFIG_URL = '/api/v1/analytics/config';
const EVENT_URL = '/api/v1/analytics/events';
const UUID = '00000000-0000-4000-8000-000000000001';
const configured = (): Response => new Response(JSON.stringify({ enabled: true }));

beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal('window', {});
  vi.stubGlobal('navigator', { doNotTrack: '0' });
  vi.stubGlobal('crypto', { randomUUID: vi.fn(() => UUID) });
});
afterEach(() => vi.unstubAllGlobals());

describe('F11 통계 전송 경계', () => {
  it('서버가 사용 중이라고 확인하기 전에는 요청이나 식별자를 만들지 않는다', async () => {
    const fetcher = vi.fn<typeof fetch>();
    vi.stubGlobal('fetch', fetcher);
    const { trackEvent } = await import('../src/shared/analytics/events.ts');
    trackEvent({ name: 'step_view', step: 1 });
    expect(fetcher).not.toHaveBeenCalled();
    expect(crypto.randomUUID).not.toHaveBeenCalled();
  });

  it('17개 이벤트를 정확한 필드로만 같은 출처에 보내며 쿠키·referrer를 전달하지 않는다', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    fetcher.mockResolvedValueOnce(configured());
    vi.stubGlobal('fetch', fetcher);
    const { initializeAnalytics, trackEvent } = await import('../src/shared/analytics/events.ts');
    await initializeAnalytics();
    EVENTS.forEach(trackEvent);
    expect(fetcher).toHaveBeenCalledTimes(18);
    expect(fetcher.mock.calls[0]?.[0]).toBe(CONFIG_URL);
    for (const [index, event] of EVENTS.entries()) {
      const [url, options] = fetcher.mock.calls[index + 1];
      expect(url).toBe(EVENT_URL);
      expect(options).toMatchObject({
        method: 'POST', credentials: 'omit', referrerPolicy: 'no-referrer', keepalive: true,
      });
      expect(options?.signal).toBeInstanceOf(AbortSignal);
      const body: unknown = JSON.parse(String(options?.body));
      expect(body).toEqual({ client_id: UUID, session_id: expect.any(Number), event });
    }
    expect(crypto.randomUUID).toHaveBeenCalledTimes(1);
  });

  it('선택값·프로필·URL·오류 문장·알 수 없는 필드와 잘못된 자료형을 거부한다', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(configured());
    vi.stubGlobal('fetch', fetcher);
    const { initializeAnalytics, trackEvent, analyticsErrorCode } = await import('../src/shared/analytics/events.ts');
    await initializeAnalytics();
    const invalid: unknown[] = [
      { name: 'step_view', step: 0 }, { name: 'step_view', step: 14 },
      { name: 'step_view', step: 1.5 }, { name: 'step_view', step: '1' },
      { name: 'consent_check', profile: { age: 25, income: 100 } },
      { name: 'step_view', step: 9, special_conditions: ['fictional-sensitive'] },
      { name: 'detail_view', policy_id: 'https://example.invalid/?income=100' },
      { name: 'analyze_fail', code: 'fictional sensitive exception text' },
      { name: 'analyze_success', duration_ms: -1, portfolio_count: 3 },
      { name: 'analyze_success', duration_ms: 1, portfolio_count: 4 },
      { name: 'pv_toggle', on: 'true' }, { name: 'combo_select', rank: 4 },
      { name: 'apply_click', policy_id: 'V-1', from: 'detail' },
      { name: 'whatif_select', scenario: 'income=100' },
      { name: 'period_change', period: '5y' }, { name: 'constructor' }, null,
    ];
    invalid.forEach((value) => trackEvent(value as AnalyticsEvent));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(analyticsErrorCode('fictional sensitive exception text')).toBe('unknown');
    expect(analyticsErrorCode('timeout')).toBe('timeout');
  });

  it('설정 조회 중에는 복사한 안전한 이벤트만 최대 32개 보관한다', async () => {
    let respond: (response: Response) => void = () => { throw new Error('not initialized'); };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    fetcher.mockReturnValueOnce(new Promise<Response>((resolve) => { respond = resolve; }));
    vi.stubGlobal('fetch', fetcher);
    const { initializeAnalytics, trackEvent } = await import('../src/shared/analytics/events.ts');
    const first = initializeAnalytics();
    expect(initializeAnalytics()).toBe(first);
    const event: AnalyticsEvent = { name: 'step_view', step: 1 };
    trackEvent(event);
    event.step = 13;
    for (let index = 0; index < 35; index += 1) trackEvent({ name: 'remember_on' });
    respond(configured());
    await first;
    expect(fetcher).toHaveBeenCalledTimes(33);
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body)).event).toEqual({ name: 'step_view', step: 1 });
  });

  it.each([false, 'true', null, { enabled: true, secret: 'fictional' }])(
    '잘못된/꺼진 설정 %j는 대기 이벤트를 버리고 수집하지 않는다', async (config) => {
      const payload = config === false ? { enabled: false } : config;
      const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify(payload)));
      vi.stubGlobal('fetch', fetcher);
      const { initializeAnalytics, trackEvent } = await import('../src/shared/analytics/events.ts');
      const loading = initializeAnalytics();
      trackEvent({ name: 'step_view', step: 1 });
      await loading;
      trackEvent({ name: 'analyze_start' });
      expect(fetcher).toHaveBeenCalledTimes(1);
      expect(crypto.randomUUID).not.toHaveBeenCalled();
    },
  );

  it('설정·전송 오류를 기록하거나 다시 시도하지 않는다', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error('fictional private error'));
    vi.stubGlobal('fetch', fetcher);
    const { initializeAnalytics, trackEvent } = await import('../src/shared/analytics/events.ts');
    await expect(initializeAnalytics()).resolves.toBeUndefined();
    expect(() => trackEvent({ name: 'analyze_start' })).not.toThrow();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('사용 중인 통계 전송이 실패해도 호출자를 실패시키지 않는다', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error('fictional private error'));
    fetcher.mockResolvedValueOnce(configured());
    vi.stubGlobal('fetch', fetcher);
    const { initializeAnalytics, trackEvent } = await import('../src/shared/analytics/events.ts');
    await initializeAnalytics();
    expect(() => trackEvent({ name: 'analyze_start' })).not.toThrow();
    await Promise.resolve();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it.each([{ doNotTrack: '1' }, { globalPrivacyControl: true }])(
    '브라우저 추적 거부 %j를 존중한다', async (settings) => {
      vi.stubGlobal('navigator', settings);
      const fetcher = vi.fn<typeof fetch>();
      vi.stubGlobal('fetch', fetcher);
      const { initializeAnalytics, trackEvent } = await import('../src/shared/analytics/events.ts');
      await initializeAnalytics();
      trackEvent({ name: 'analyze_start' });
      expect(fetcher).not.toHaveBeenCalled();
      expect(crypto.randomUUID).not.toHaveBeenCalled();
    },
  );

  it('정보 삭제 후 이전 ID를 재사용하지 않는다', async () => {
    const nextId = '00000000-0000-4000-8000-000000000002';
    vi.stubGlobal('crypto', { randomUUID: vi.fn().mockReturnValueOnce(UUID).mockReturnValueOnce(nextId) });
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    fetcher.mockResolvedValueOnce(configured());
    vi.stubGlobal('fetch', fetcher);
    const { initializeAnalytics, trackEvent, resetAnalyticsSession } = await import('../src/shared/analytics/events.ts');
    await initializeAnalytics();
    trackEvent({ name: 'clear_mine' });
    resetAnalyticsSession();
    trackEvent({ name: 'analyze_start' });
    expect(crypto.randomUUID).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body)).client_id).toBe(UUID);
    expect(JSON.parse(String(fetcher.mock.calls[2]?.[1]?.body)).client_id).toBe(nextId);
  });

  it('설정 조회 중 정보를 지우면 삭제 전의 대기 이벤트를 보내지 않는다', async () => {
    let respond: (response: Response) => void = () => { throw new Error('not initialized'); };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    fetcher.mockReturnValueOnce(new Promise<Response>((resolve) => { respond = resolve; }));
    vi.stubGlobal('fetch', fetcher);
    const { initializeAnalytics, trackEvent, resetAnalyticsSession } = await import('../src/shared/analytics/events.ts');
    const loading = initializeAnalytics();
    trackEvent({ name: 'step_view', step: 1 });
    resetAnalyticsSession();
    trackEvent({ name: 'analyze_start' });
    respond(configured());
    await loading;
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body)).event).toEqual({ name: 'analyze_start' });
  });
});

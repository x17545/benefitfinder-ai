import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import fixtureText from '../../../contracts/fixtures/youth-benefit.json?raw';
import { ApplicationGuide } from '../src/features/policy/ApplicationGuide.tsx';
import { fetchBenefit, isNormalizedBenefit } from '../src/shared/api/benefits.ts';
import type { NormalizedBenefit } from '../src/shared/api/benefits.ts';

const benefit = JSON.parse(fixtureText) as NormalizedBenefit;
const guide = benefit.application_guide;
if (!guide?.method || !guide.office || !guide.documents?.[0]) throw new Error('missing guide fixture');
const method = guide.method;
const office = guide.office;
const document = guide.documents[0];

function render(value: NormalizedBenefit, virtualData = false): string {
  return renderToStaticMarkup(createElement(ApplicationGuide, { benefit: value, virtualData }));
}

describe('source-bound application instructions', () => {
  it('renders reviewed method, distinct intake office, exact documents and locations', () => {
    const html = render(benefit);
    expect(html).toContain(method.quote);
    expect(html).toContain(office.quote);
    expect(html).toContain(document.quote);
    expect(html).toContain(method.location);
    expect(html).not.toContain('공식 자료에 서류 안내가 없어요');
    expect(html).not.toContain(benefit.provider);
  });

  it('keeps unchecked documents separate from confirmed no guidance', () => {
    for (const value of [undefined, null, { method: null, office: null, documents: null }]) {
      const html = render({ ...benefit, application_guide: value });
      expect(html).toContain('준비 서류 안내를 아직 확인하지 못했어요');
      expect(html).not.toContain('공식 자료에 서류 안내가 없어요');
      expect(html).toContain('접수 기관은 공식 페이지에서 확인해 주세요');
      expect(html).not.toContain(benefit.provider);
    }
    const html = render({ ...benefit, application_guide: { method: null, office: null, documents: [] } });
    expect(html).toContain('공식 자료에 서류 안내가 없어요');
  });

  it('shows virtual notices and escapes source HTML as text', () => {
    expect(render({ ...benefit, application_guide: undefined }, true)).toContain('가상 정책에는 실제 제출 서류가 없어요');
    const html = render({ ...benefit, application_guide: {
      method: { ...method, quote: '<script>synthetic()</script>\n가상 온라인 신청' }, office: null, documents: [],
    } });
    expect(html).toContain('&lt;script&gt;synthetic()&lt;/script&gt;\n가상 온라인 신청');
    expect(html).not.toContain('<script>');
  });

  it('accepts legacy and nullable instructions', () => {
    expect(isNormalizedBenefit(benefit)).toBe(true);
    expect(isNormalizedBenefit({ ...benefit, application_guide: undefined })).toBe(true);
    expect(isNormalizedBenefit({ ...benefit, application_guide: null })).toBe(true);
    expect(isNormalizedBenefit({ ...benefit, application_guide: { method: null, office: null, documents: [] } })).toBe(true);
  });

  it.each([
    [], {}, { method: null, office: null },
    { method: 'online', office: null, documents: [] },
    { method: { ...method, location: undefined }, office: null, documents: [] },
    { method: { ...method, quote: '  ' }, office: null, documents: [] },
    { method: { ...method, location: '\n' }, office: null, documents: [] },
    { method: { ...method, quote: 'x'.repeat(2501) }, office: null, documents: [] },
    { method: { ...method, source: { ...method.source, document_version: 'other' } }, office: null, documents: [] },
    { method: null, office: { ...office, source: { ...office.source, url: 'https://different.example/' } }, documents: [] },
    { method: null, office: { ...office, source: {} }, documents: [] },
    { method: null, office: null, documents: [null] },
    { method: null, office: null, documents: Array.from({ length: 101 }, () => document) },
  ])('rejects malformed instructions before UI and shared cache: %j', async (value) => {
    const body = { ...benefit, application_guide: value };
    expect(isNormalizedBenefit(body)).toBe(false);
    await expect(fetchBenefit(benefit.id, {
      fetchFn: () => Promise.resolve(new Response(JSON.stringify(body))),
    })).rejects.toThrow('unexpected response shape');
  });
});

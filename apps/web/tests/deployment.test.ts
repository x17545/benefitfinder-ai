import { describe, expect, it } from 'vitest';
import { createVercelConfig } from '../deploymentConfig.ts';

describe('Vercel API routing', () => {
  it('keeps preview API and health on the selected backend before the SPA fallback', () => {
    const { rewrites } = createVercelConfig('https://branch-backend.example/', 'preview');
    expect(rewrites).toEqual([
      { source: '/api/:path*', destination: 'https://branch-backend.example/api/:path*' },
      { source: '/health', destination: 'https://branch-backend.example/health' },
      { source: '/(.*)', destination: '/index.html' },
    ]);
  });

  it.each(['preview', 'development', undefined])(
    'keeps a preview without a backend isolated from production (%s)', (environment) => {
      for (const target of [undefined, '', '  ']) {
        expect(createVercelConfig(target, environment).rewrites).toEqual([
          { source: '/api/:path*', destination: '/api/backend-unavailable' },
          { source: '/health', destination: '/api/backend-unavailable' },
          { source: '/(.*)', destination: '/index.html' },
        ]);
      }
    },
  );

  it('preserves the production backend default', () => {
    expect(createVercelConfig(undefined, 'production').rewrites[0]?.destination)
      .toBe('https://benefitfinder-ai.onrender.com/api/:path*');
  });

  it.each(['http://backend.example', 'https://user:pass@backend.example',
    'https://backend.example/api', 'https://backend.example/?token=secret', 'invalid'])(
    'rejects credentials, insecure origins and path/query configuration: %s', (target) => {
      expect(() => createVercelConfig(target, 'preview')).toThrow();
    },
  );
});

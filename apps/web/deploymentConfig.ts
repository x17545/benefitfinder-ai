/** Pure routing configuration, shared by the deployment entrypoint and its tests. */
export function createVercelConfig(
  apiTarget: string | undefined,
  environment: string | undefined,
): { rewrites: { source: string; destination: string }[] } {
  if (environment !== 'production' && !apiTarget?.trim()) {
    return {
      rewrites: [
        { source: '/api/:path*', destination: '/api/backend-unavailable' },
        { source: '/health', destination: '/api/backend-unavailable' },
        { source: '/(.*)', destination: '/index.html' },
      ],
    };
  }
  let target: URL;
  try {
    target = new URL(apiTarget ?? 'https://benefitfinder-ai.onrender.com');
  } catch {
    throw new Error('invalid_API_PROXY_TARGET');
  }
  if (target.protocol !== 'https:' || target.username || target.password ||
    target.pathname !== '/' || target.search || target.hash) {
    throw new Error('API_PROXY_TARGET_must_be_https_origin');
  }
  return {
    rewrites: [
      { source: '/api/:path*', destination: `${target.origin}/api/:path*` },
      { source: '/health', destination: `${target.origin}/health` },
      { source: '/(.*)', destination: '/index.html' },
    ],
  };
}

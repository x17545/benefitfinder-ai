/** Server-side deployment entrypoint; never imported by the browser app. */
import { env } from 'node:process';
import { createVercelConfig } from './deploymentConfig.ts';

export const config = createVercelConfig(env.API_PROXY_TARGET, env.VERCEL_ENV);

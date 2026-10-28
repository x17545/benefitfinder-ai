/** Same-origin health path. The Vite dev proxy forwards it to FastAPI. */
export const HEALTH_PATH = '/health';

/**
 * Backend API docs are served by FastAPI directly.
 * Open them without going through the Vite proxy.
 */
export const BACKEND_DOCS_URL = 'http://127.0.0.1:8000/docs';

/** Default FastAPI origin used by the Vite dev proxy. */
export const BACKEND_DEFAULT_ORIGIN = 'http://127.0.0.1:8000';

/** Same-origin API prefix. The Vite dev proxy forwards it to FastAPI. */
export const API_PREFIX = '/api/v1';

/** GET 선택지 목록 (지역·구간). contracts/v1 options.schema.json */
export const OPTIONS_PATH = `${API_PREFIX}/options`;

import type { ApiClient } from './client';

/**
 * `VITE_API_URL`, if it is on the page's origin. The page's Content-Security-Policy (`connect-src 'self'`,
 * `img-src 'self'`: vite.config.ts and deploy/nginx/security-headers.conf) lets it talk to its own origin and
 * nobody else, so another origin would have every request and every crop blocked, with nothing on the page to say
 * why. That is a build mistake: it fails here, at start-up, with the reason.
 */
function sameOriginBase(baseUrl: string): string {
  if (!baseUrl || typeof location === 'undefined') return baseUrl;
  let origin: string;
  try {
    origin = new URL(baseUrl, location.href).origin;
  } catch {
    throw new Error(`VITE_API_URL=${baseUrl} is not a URL. Leave it empty to use the page's own origin.`);
  }
  if (origin !== location.origin) {
    throw new Error(
      `VITE_API_URL=${baseUrl} is on ${origin}, but this page is on ${location.origin}. The page's Content-Security-Policy ` +
        `(connect-src 'self', img-src 'self') allows requests to its own origin only, so every API request and every ` +
        `crop would be blocked. Serve the API under the page's origin (a reverse proxy), leave VITE_API_URL empty, or ` +
        `add the API's origin to the policy in web/vite.config.ts and deploy/nginx/security-headers.conf.`,
    );
  }
  return baseUrl;
}

/**
 * The backend the app talks to, chosen at build time by `VITE_API`:
 *
 * - `http`: the job API (`HttpApiClient`) on the page's origin. `VITE_API_URL` may add a path or the page's own
 *   origin in front of `/api`; an API on another origin is refused at start-up (see `sameOriginBase`).
 * - `mock` (and nothing at all, until the HTTP client is the default, TODO): the mock.
 *
 * Each is a dynamic import behind the flag, so a build for one carries none of the other's code (the mock
 * brings its fixtures with it). Any other value fails at start-up instead of quietly using the mock.
 */
export async function createApiClient(): Promise<ApiClient> {
  const backend = import.meta.env.VITE_API || 'mock';
  if (backend === 'http') {
    const baseUrl = sameOriginBase(import.meta.env.VITE_API_URL ?? '');
    const { HttpApiClient } = await import('./http');
    return new HttpApiClient({ baseUrl });
  }
  if (backend === 'mock') {
    const { createMockApiClient } = await import('../mocks');
    return createMockApiClient();
  }
  throw new Error(`VITE_API=${String(backend)}: expected "http" or "mock"`);
}

/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Which backend the app talks to: `http` (the job API) or `mock` (the default until P3-12 makes `http` the default). */
  readonly VITE_API?: 'mock' | 'http';
  /**
   * Put in front of the API's `/api` paths: empty (the default: the page's own origin), a path such as `/beta`,
   * or an absolute URL on the page's own origin. A URL on ANOTHER origin fails at start-up: the page's CSP
   * (`connect-src 'self'`, `img-src 'self'`) would block every request and crop. Serve the API under the page's
   * origin, or add the origin to the CSP first (web/vite.config.ts, deploy/nginx/security-headers.conf).
   */
  readonly VITE_API_URL?: string;
  /** Quiet time after the last canvas change before an edit is reported (ms). Tests lengthen it; unset in production. */
  readonly VITE_EDIT_DEBOUNCE_MS?: string;
}

/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Which backend the app talks to: `http` (the job API) or `mock` (the default when unset; the production image builds with `http`). */
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
  /**
   * Where the browser's error reports go (a self-hosted GlitchTip): `https://<public key>@<this site's host>/glitchtip/<project id>`.
   * Unset or empty (the default): no reports. The edge proxies `/glitchtip/api/<project id>/envelope/` to GlitchTip, so the
   * reports use the page's own origin and the CSP (`connect-src 'self'`) needs no entry. A DSN on another origin, or one
   * that is not a DSN, is refused at start-up with a console warning (src/observability/sentry.ts): the page runs without
   * reports. Build-time, so it is a build ARG of the web image (deploy/web.Dockerfile): a build without it contains no
   * reporting code at all, and one with it loads the SDK as a chunk of its own (src/main.tsx).
   */
  readonly VITE_SENTRY_DSN?: string;
  /** The release the reports are filed under (the git revision of the build). Optional. */
  readonly VITE_RELEASE?: string;
}

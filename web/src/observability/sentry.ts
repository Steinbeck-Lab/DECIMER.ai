/**
 * Crash reports from the browser: the Sentry SDK, pointed at a self-hosted GlitchTip, and nothing else.
 *
 * Off unless the build has a DSN (`VITE_SENTRY_DSN`, env.d.ts). The DSN names THIS site: the edge proxies
 * `/glitchtip/api/<project>/envelope/` to GlitchTip, so the page's CSP (`connect-src 'self'`) needs no entry and no report
 * goes to another origin. A DSN on another origin would be blocked by the CSP without a word, so it is refused here, with
 * the reason on the console, like `VITE_API_URL` is (src/api/index.ts). A bad DSN never stops the app from starting.
 *
 * The SDK is cut down to what a crash report needs: the global error and unhandled-rejection handlers, de-duplication and
 * linked errors. No breadcrumbs (they hold what the user did), no console, fetch, XHR, DOM or history hooks, no request
 * context, no sessions, no tracing, no replay, no feedback widget, no client reports. What is left is cleaned again by an
 * allowlist in `beforeSend` (scrub.ts), so a field a newer SDK adds does not leave by default.
 *
 * main.tsx imports this module only when the build has a DSN (and then as a chunk of its own), so the SDK is not part of
 * the main bundle.
 */
import type { BrowserOptions } from '@sentry/browser';
import { close, dedupeIntegration, globalHandlersIntegration, init, linkedErrorsIntegration } from '@sentry/browser';
import { scrubEvent } from './scrub';

export interface ErrorReportingOptions {
  /** The DSN. Production passes nothing: it is `VITE_SENTRY_DSN`. For tests. */
  dsn?: string;
  /** The page's location (only `origin` is read). Production passes nothing: it is `window.location`. For tests. */
  location?: { origin: string };
  /** The SDK's transport factory (it receives the endpoint URL). Production passes nothing: the SDK's `fetch` transport. */
  transport?: BrowserOptions['transport'];
}

let started = false;

/** What is wrong with the DSN for a page on `pageOrigin`, or `undefined` if it is usable. */
function dsnProblem(dsn: string, pageOrigin: string | undefined): string | undefined {
  let url: URL;
  try {
    url = new URL(dsn);
  } catch {
    return 'it is not a URL (expected https://<public key>@<this site>/glitchtip/<project id>).';
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') return `its scheme is ${url.protocol}, not https.`;
  if (!url.username) return 'it has no public key in front of the host.';
  if (!/\/\d+$/.test(url.pathname)) return 'it does not end in a numeric project id.';
  if (pageOrigin === undefined) return 'the page has no origin to compare it with.';
  if (url.origin !== pageOrigin) {
    return (
      `it is on ${url.origin}, but this page is on ${pageOrigin}. The page's Content-Security-Policy (connect-src 'self') ` +
      `allows requests to its own origin only, so every report would be blocked. Serve GlitchTip under the page's origin ` +
      `(the edge's /glitchtip/ location) and put that in the DSN.`
    );
  }
  return undefined;
}

/** Starts the error reports once. Returns whether it did: false when off (no DSN), refused (a bad DSN) or already started. */
export function startErrorReporting(options: ErrorReportingOptions = {}): boolean {
  const dsn = (options.dsn ?? import.meta.env.VITE_SENTRY_DSN ?? '').trim();
  if (!dsn || started) return false;
  const origin = (options.location ?? (typeof location === 'undefined' ? undefined : location))?.origin;
  const problem = dsnProblem(dsn, origin);
  if (problem) {
    console.warn(`Error reports are off: VITE_SENTRY_DSN is not usable: ${problem}`);
    return false;
  }
  const release = import.meta.env.VITE_RELEASE;
  try {
    init({
      dsn,
      ...(options.transport ? { transport: options.transport } : {}),
      environment: import.meta.env.MODE,
      ...(release ? { release } : {}),
      defaultIntegrations: false,
      integrations: [globalHandlersIntegration(), dedupeIntegration(), linkedErrorsIntegration()],
      // The 11.x SDK has no `sendDefaultPii`: `dataCollection` replaces it, and its defaults collect (user info, cookies,
      // headers). Everything off, so the SDK does not even put an IP-inference hint into the report.
      dataCollection: {
        userInfo: false,
        cookies: false,
        httpHeaders: false,
        httpBodies: [],
        urlQueryParams: false,
        stackFrameVariables: false,
        frameContextLines: 0,
        graphQL: { document: false, variables: false },
        genAI: { inputs: false, outputs: false },
        databaseQueryData: false,
        queues: false,
      },
      attachStacktrace: true,
      maxBreadcrumbs: 0,
      beforeBreadcrumb: () => null,
      sendClientReports: false,
      enhanceFetchErrorMessages: false, // the app's own error text stays as it is
      // The report is sent without the site's cookies (the visit counter's, for one): GlitchTip has no use for them.
      transportOptions: { fetchOptions: { credentials: 'omit' } },
      beforeSend: scrubEvent,
    });
  } catch (error) {
    console.warn('Error reports are off: the SDK did not start.', error);
    return false;
  }
  started = true;
  return true;
}

/** For tests: shut the SDK down and forget that it was started. */
export async function resetErrorReportingForTests(): Promise<void> {
  started = false;
  await close();
}

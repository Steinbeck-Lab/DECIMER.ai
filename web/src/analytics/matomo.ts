/**
 * The visit counter: Matomo of NFDI4Chem, with the code of the earlier Laravel version of decimer.ai (resources/views/layouts/default.blade.php):
 * the same tracker address (matomo.nfdi4chem.de) and site id (2), the same calls in the same order (`trackPageView`,
 * `enableLinkTracking`, `setTrackerUrl`, `setSiteId`), and no `disableCookies`: Matomo sets its own `_pk_*` cookies, and
 * the privacy text (strings.ts) says so. The one difference is that the address says `https` (the page's CSP names the
 * origin; the earlier code was protocol-relative) and that it lives in a module, because the CSP allows no inline script.
 *
 * The address is in the CSP too (`script-src`, `img-src` for the tracking image and `connect-src` for the beacon): keep
 * web/vite.config.ts and deploy/nginx/security-headers.conf in step (`python3 -I deploy/nginx/check-csp.py`).
 */
import type { Route } from '../shell/router';

export const MATOMO_URL = 'https://matomo.nfdi4chem.de/';
export const MATOMO_SITE_ID = '2';

type Call = (string | number)[];
type Win = Window & { _paq?: Call[] };

/** The hosts whose visits belong in decimer.ai's statistics. */
const TRACKED_HOSTS = new Set(['decimer.ai', 'www.decimer.ai']);

/**
 * Counts only the production build served as decimer.ai: not the dev server, not a page on this machine (the
 * local stack on 127.0.0.1, `vite preview` under the end-to-end tests, Lighthouse), and not a copy someone
 * hosts themselves, whose visitors are not decimer.ai's to count.
 */
export function trackingEnabled(hostname: string, production: boolean): boolean {
  return production && TRACKED_HOSTS.has(hostname);
}

let started = false;
let current: Route | undefined;

/** Loads Matomo once. Returns whether it did. */
export function startMatomo(
  win: Win = window,
  doc: Document = document,
  enabled: boolean = trackingEnabled(win.location.hostname, import.meta.env.PROD),
): boolean {
  if (!enabled || started) return false;
  started = true;
  const paq = (win._paq = win._paq || []);
  paq.push(['trackPageView']);
  paq.push(['enableLinkTracking']);
  paq.push(['setTrackerUrl', `${MATOMO_URL}matomo.php`]);
  paq.push(['setSiteId', MATOMO_SITE_ID]);
  const script = doc.createElement('script');
  script.async = true;
  script.src = `${MATOMO_URL}matomo.js`;
  doc.head.appendChild(script);
  return true;
}

/**
 * Counts a move to another page of the app. The first call only notes the page the visit began on (the code above
 * already counted it), and a call for the page that is already current counts nothing.
 */
export function trackRoute(route: Route, win: Win = window, doc: Document = document): void {
  const previous = current;
  current = route;
  if (!started || previous === undefined || previous === route) return;
  const paq = (win._paq = win._paq || []);
  paq.push(['setCustomUrl', win.location.href]);
  paq.push(['setDocumentTitle', doc.title]);
  paq.push(['trackPageView']);
}

/** For tests: forget that Matomo was started. */
export function resetMatomoForTests(): void {
  started = false;
  current = undefined;
}

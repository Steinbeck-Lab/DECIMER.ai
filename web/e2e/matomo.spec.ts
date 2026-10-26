import { expect } from '@playwright/test';
import { test } from './returning';
import { watchProblems } from './helpers';

// The visit counter (src/analytics/matomo.ts) starts only on a real host, never on this machine. The first describe
// opens the built app under a made-up host name that resolves to the local preview server, with the real CSP, and
// answers matomo.nfdi4chem.de itself, so that nothing reaches the live statistics.

const MATOMO = 'https://matomo.nfdi4chem.de';
/** A 1x1 GIF, what matomo.php answers an image request with. */
const PIXEL = Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7', 'base64');

type Paq = (string | number)[][];
const queue = (page: import('@playwright/test').Page) => page.evaluate(() => (window as unknown as { _paq?: Paq })._paq ?? null);

// Top level, because a browser option cannot be set for one group: it only adds a name for the whole file.
test.use({ launchOptions: { args: ['--host-resolver-rules=MAP decimer.test 127.0.0.1'] } });

test.describe('on a real host', () => {
  // The returning-tab fixture (./returning) keeps the 1.9 MB animation from playing on this host too.
  test.use({ baseURL: 'http://decimer.test:4173/' });

  test('the counter is loaded once, counts each page, and the CSP lets its script, beacon and image through', async ({ page }) => {
    const problems = watchProblems(page); // console errors, including every CSP violation
    const requests: string[] = [];
    await page.route(`${MATOMO}/**`, (route) => {
      const url = route.request().url();
      requests.push(url);
      if (url.endsWith('/matomo.js')) return route.fulfill({ contentType: 'application/javascript', body: 'window.__matomoLoaded = true;' });
      return route.fulfill({ contentType: 'image/gif', body: PIXEL });
    });

    await page.goto('./');
    await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();
    await expect.poll(() => page.evaluate(() => (window as unknown as { __matomoLoaded?: boolean }).__matomoLoaded)).toBe(true);
    expect(requests.filter((url) => url.endsWith('/matomo.js'))).toHaveLength(1);
    expect(await queue(page)).toEqual([
      ['trackPageView'],
      ['enableLinkTracking'],
      ['setTrackerUrl', `${MATOMO}/matomo.php`],
      ['setSiteId', '2'],
    ]);

    // A move to another page of the app is one more page view, with that page's address and title.
    await page.getByRole('contentinfo').getByRole('link', { name: 'Privacy' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Privacy' })).toBeVisible();
    const after = (await queue(page)) ?? [];
    expect(after.filter((call) => call[0] === 'trackPageView')).toHaveLength(2);
    expect(after.at(-3)).toEqual(['setCustomUrl', 'http://decimer.test:4173/#/privacy']);
    expect(after.at(-2)).toEqual(['setDocumentTitle', 'Privacy · DECIMER.ai']);
    expect(requests.filter((url) => url.endsWith('/matomo.js'))).toHaveLength(1); // not loaded again

    // The tracker's two ways of reporting: a beacon (connect-src) and a tracking image (img-src).
    expect(await page.evaluate(async (url) => (await fetch(url, { method: 'POST', mode: 'no-cors', body: 'x' })).type, `${MATOMO}/matomo.php`)).toBe('opaque');
    const image = await page.evaluate(
      (url) =>
        new Promise<string>((resolve) => {
          const img = new Image();
          img.onload = () => resolve('loaded');
          img.onerror = () => resolve('blocked');
          img.src = url;
        }),
      `${MATOMO}/matomo.php?idsite=2`,
    );
    expect(image).toBe('loaded');
    expect(problems).toEqual([]);
  });

  test('a script from any other host is still blocked', async ({ page }) => {
    const violations: string[] = [];
    await page.route('https://example.test/**', (route) => route.fulfill({ contentType: 'application/javascript', body: 'window.__other = true;' }));
    await page.goto('./');
    page.on('console', (m) => m.type() === 'error' && violations.push(m.text()));
    await page.evaluate(() => {
      document.addEventListener('securitypolicyviolation', (e) => console.error(`CSP ${e.violatedDirective}`));
      const script = document.createElement('script');
      script.src = 'https://example.test/other.js';
      document.head.appendChild(script);
    });
    await expect.poll(() => violations.some((v) => v.includes('script-src'))).toBe(true);
    expect(await page.evaluate(() => (window as unknown as { __other?: boolean }).__other)).toBeUndefined();
  });
});

test('on this machine nothing is counted and nothing is requested', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', (request) => request.url().startsWith(MATOMO) && requests.push(request.url()));
  await page.goto('./#/about');
  await page.locator('main h1').waitFor();
  await page.waitForTimeout(500);
  expect(requests).toEqual([]);
  expect(await queue(page)).toBeNull();
});

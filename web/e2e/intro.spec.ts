import { expect, test, type Page } from '@playwright/test';

// The logo animation: DECIMER.gif, 1.9 MB, once per browser tab (the flag is in the tab's session storage).
// These tests use the plain `test`, not ./returning.ts, so each
// one starts in a fresh tab with nothing stored, like a visitor who has just opened the site.

const GIF = /\/decimer-intro-[^/]*\.gif$/;

function watchGif(page: Page): string[] {
  const requests: string[] = [];
  page.on('request', (r) => {
    if (GIF.test(r.url())) requests.push(r.url());
  });
  return requests;
}

const visited = (page: Page) => page.evaluate(() => sessionStorage.getItem('decimer.visited'));
const visitedInBrowser = (page: Page) => page.evaluate(() => localStorage.getItem('decimer.visited'));

test('a new tab plays the animation and remembers it, for that tab, once it is on screen', async ({ page }) => {
  const gif = watchGif(page);
  await page.goto('./');
  const logo = page.locator('img.dc-intro');
  await expect(logo).toBeVisible();
  await expect(logo).toHaveAttribute('alt', 'DECIMER.ai');
  await expect.poll(() => visited(page), { timeout: 15_000 }).toBe('1');
  expect(await visitedInBrowser(page)).toBeNull(); // the tab remembers it, the browser does not
  expect(gif).toHaveLength(1);
  // The animation is the logo of this screen: the page is still one upload target, and the tagline is text.
  await expect(page.getByRole('heading', { level: 1, name: 'DECIMER.ai' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();
  // The GIF has the tagline in it, so the text is kept for assistive technology only; the whole first
  // screen, the upload button and the footer's coffee credit included, still fits a 1440 by 900 window.
  const tagline = page.getByText('Extract chemical structures from PDFs and images');
  await expect(tagline).toHaveClass(/dc-sr/);
  const choose = await page.getByRole('button', { name: 'Choose files' }).boundingBox();
  expect((choose?.y ?? 9999) + (choose?.height ?? 0)).toBeLessThan((page.viewportSize()?.height ?? 0));
});

test('the same tab gets the static wordmark on a reload and never downloads the animation again', async ({ page }) => {
  await page.goto('./');
  await expect.poll(() => visited(page), { timeout: 15_000 }).toBe('1');

  const gif = watchGif(page);
  await page.reload();
  await expect(page.locator('img.dc-wordmark--hero')).toBeVisible();
  await expect(page.locator('img.dc-intro')).toHaveCount(0);
  await expect(page.locator('.dc-tagline')).toBeVisible();
  await page.waitForTimeout(500);
  expect(gif).toEqual([]);
});

test('coming back to the home page within the same tab does not play it again', async ({ page }) => {
  await page.goto('./');
  await expect.poll(() => visited(page), { timeout: 15_000 }).toBe('1');

  const gif = watchGif(page);
  await page.goto('./#/about');
  await expect(page.getByRole('heading', { level: 1, name: 'About DECIMER.ai' })).toBeVisible();
  await page.goto('./#/');
  await expect(page.locator('img.dc-wordmark--hero')).toBeVisible();
  await expect(page.locator('img.dc-intro')).toHaveCount(0);
  await page.waitForTimeout(500);
  expect(gif).toEqual([]);
});

test('another tab of the same browser plays it again', async ({ page, context }) => {
  await page.goto('./');
  await expect.poll(() => visited(page), { timeout: 15_000 }).toBe('1');

  const second = await context.newPage(); // a new tab: its own session storage
  const gif = watchGif(second);
  await second.goto('./');
  await expect(second.locator('img.dc-intro')).toBeVisible();
  await expect.poll(() => visited(second), { timeout: 15_000 }).toBe('1');
  expect(gif).toHaveLength(1);
});

test('a tab that never gets the animation has not used it up', async ({ page }) => {
  await page.route(GIF, (route) => route.abort());
  await page.goto('./');
  await page.waitForTimeout(1500);
  expect(await visited(page)).toBeNull();
  // The wordmark is still there to be read (the image carries the name as its alt text).
  await expect(page.getByRole('heading', { level: 1, name: 'DECIMER.ai' })).toBeAttached();

  // The next load of the tab, with the file reachable, plays it and then remembers.
  await page.unroute(GIF);
  await page.reload();
  await expect.poll(() => visited(page), { timeout: 15_000 }).toBe('1');
});

test('with reduced motion there is no animation, no download and the tab keeps it for later', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const gif = watchGif(page);
  await page.goto('./');
  await expect(page.locator('img.dc-wordmark--hero')).toBeVisible();
  await page.waitForTimeout(800);
  expect(gif).toEqual([]);
  expect(await visited(page)).toBeNull();
});

test('nothing preloads the animation, and pages other than the empty screen never fetch it', async ({ page }) => {
  const gif = watchGif(page);
  await page.goto('./#/about');
  await page.waitForTimeout(800);
  await expect(page.locator('head link[rel=preload], head link[rel=prefetch], head link[rel=modulepreload][href*="gif"]')).toHaveCount(0);
  expect(gif).toEqual([]);
  expect(await visited(page)).toBeNull(); // not played, so not used up
});

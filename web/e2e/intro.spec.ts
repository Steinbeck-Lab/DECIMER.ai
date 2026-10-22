import { expect, test, type Page } from '@playwright/test';

// The first-visit logo animation (PRODUCT.md, Brand Commitments): DECIMER.gif, 1.9 MB, once per browser.
// These tests start from an empty storage, like a new visitor.
test.use({ storageState: { cookies: [], origins: [] } });

const GIF = /\/decimer-intro-[^/]*\.gif$/;

function watchGif(page: Page): string[] {
  const requests: string[] = [];
  page.on('request', (r) => {
    if (GIF.test(r.url())) requests.push(r.url());
  });
  return requests;
}

const visited = (page: Page) => page.evaluate(() => localStorage.getItem('decimer.visited'));

test('the first visit plays the animation and is remembered once it is on screen', async ({ page }) => {
  const gif = watchGif(page);
  await page.goto('./');
  const logo = page.locator('img.dc-intro');
  await expect(logo).toBeVisible();
  await expect(logo).toHaveAttribute('alt', 'DECIMER.ai');
  await expect.poll(() => visited(page), { timeout: 15_000 }).toBe('1');
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

test('a returning visitor gets the static wordmark and never downloads the animation', async ({ page }) => {
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

test('a first visit that never gets the animation is not used up', async ({ page }) => {
  await page.route(GIF, (route) => route.abort());
  await page.goto('./');
  await page.waitForTimeout(1500);
  expect(await visited(page)).toBeNull();
  // The wordmark is still there to be read (the image carries the name as its alt text).
  await expect(page.getByRole('heading', { level: 1, name: 'DECIMER.ai' })).toBeAttached();

  // The next visit, with the file reachable, plays it and then remembers.
  await page.unroute(GIF);
  await page.reload();
  await expect.poll(() => visited(page), { timeout: 15_000 }).toBe('1');
});

test('with reduced motion there is no animation, no download and the visit is kept for later', async ({ page }) => {
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

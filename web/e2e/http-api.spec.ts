import { expect } from '@playwright/test';
import { test } from './returning';
import { HTTP_API_PORT } from '../playwright.config';
import { apiError, fakeApi, JOB_ID, wireJob } from './fakeApi';
import { fixtureImage, watchProblems } from './helpers';

// The build with VITE_API=http (playwright.config.ts, port 4175): the app against the job API's endpoints,
// which the tests answer. It checks what unit tests cannot: a real multipart upload, the page's CSP,
// relative media URLs and polling in a browser.
test.use({ baseURL: `http://127.0.0.1:${HTTP_API_PORT}/` });

const JOB_PATH = `/api/jobs/${JOB_ID}`;

async function upload(page: import('@playwright/test').Page, name = 'figure2.png') {
  await page.locator('input[type=file]').setInputFiles(fixtureImage(name));
}

test('upload, queue, run, results: the real endpoints, a multipart body, crops from the media URLs', async ({ page }) => {
  test.setTimeout(60_000);
  const problems = watchProblems(page);
  let polls = 0;
  const seen = await fakeApi(page, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path === '/api/jobs') {
      await route.fulfill({ status: 202, contentType: 'application/json', headers: { Location: JOB_PATH }, body: JSON.stringify(wireJob('queued')) });
      return true;
    }
    if (request.method() === 'GET' && path === JOB_PATH) {
      const kind = ++polls === 1 ? 'queued' : polls === 2 ? 'running' : 'done';
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(wireJob(kind)) });
      return true;
    }
    return false;
  });

  await page.goto('./');
  await upload(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Waiting for a free worker' })).toBeVisible();
  await expect(page.getByText('You are number 2 in the queue.')).toBeVisible();
  await expect(page.getByRole('heading', { level: 1, name: 'Finding structures on each page' })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole('list', { name: 'Recognised structures' })).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('.dc-tile__number')).toHaveText(['1', '2', '3']);

  // The upload: POST /api/jobs, multipart, one `file` part named like the file; no JSON content type.
  const post = seen.requests.find((r) => r.method === 'POST');
  expect(post?.path).toBe('/api/jobs');
  expect(post?.contentType).toMatch(/^multipart\/form-data; boundary=/);
  expect(post?.body).toContain('name="file"; filename="figure2.png"');

  // The results came from GET /api/jobs/{id}, polled every ~2 s, and stopped when the job was done.
  const gets = seen.requests.filter((r) => r.method === 'GET' && r.path === JOB_PATH);
  expect(gets).toHaveLength(3);
  expect(gets[1]!.at - gets[0]!.at).toBeGreaterThan(1500);
  await page.waitForTimeout(2500);
  expect(seen.requests.filter((r) => r.method === 'GET' && r.path === JOB_PATH)).toHaveLength(3);

  // The crops loaded from the media URLs the job gave (same origin, so the CSP allows them).
  await expect.poll(() => page.locator('.dc-tile__crop').first().evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  expect(seen.requests.some((r) => r.path === `/api/jobs/${JOB_ID}/media/crop-0.png`)).toBe(true);
  // The name's `nameTier` and `nameVerified` came over the wire into the hover text.
  await page.getByRole('button', { name: /^Structure 3\b/ }).hover();
  await expect(page.getByRole('tooltip').locator('.dc-hover__line')).toHaveText([
    'Preferred IUPAC name',
    'OPSIN reads this name back to exactly this structure',
  ]);
  // A result with no `svg` from the API (it sends "" until the worker draws one) shows a placeholder, not a broken image.
  await expect(page.locator('.dc-tile__structure--none').first()).toHaveText('No depiction');
  expect(problems).toEqual([]);
});

test('refusals show the sentence for their code, with the server\'s message where it names the file', async ({ page }) => {
  let next = apiError(429, 'rate_limited', 'Too many active jobs.');
  await fakeApi(page, async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill(next);
      return true;
    }
    return false;
  });
  await page.goto('./');

  await upload(page);
  await expect(page.getByRole('alert')).toContainText('Too many jobs are running from your network');
  next = apiError(503, 'queue_full', 'The queue is full.', { 'Retry-After': '5' });
  await upload(page);
  await expect(page.getByRole('alert')).toContainText('The queue is full right now');
  next = apiError(400, 'corrupt_file', 'paper.pdf could not be read as a PDF document.');
  await upload(page);
  await expect(page.getByRole('alert')).toContainText('A file could not be read');
  await expect(page.getByRole('alert')).toContainText('paper.pdf could not be read as a PDF document.');
  next = apiError(400, 'encrypted_pdf', 'paper.pdf is password-protected.');
  await upload(page);
  await expect(page.getByRole('alert')).toContainText('password-protected');
});

test('no answer from the server: the upload failed, check your connection', async ({ page }) => {
  await page.route('**/api/**', (route) => route.abort('connectionrefused'));
  await page.goto('./');
  await upload(page);
  await expect(page.getByRole('alert')).toContainText('The upload failed. Check your connection');
});

test('a 503 with Retry-After while polling is waited out, not hammered', async ({ page }) => {
  test.setTimeout(60_000);
  let polls = 0;
  const seen = await fakeApi(page, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST') {
      await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify(wireJob('queued')) });
      return true;
    }
    if (path === JOB_PATH) {
      if (++polls === 2) {
        await route.fulfill(apiError(503, 'unavailable', 'The service is temporarily unavailable. Please retry.', { 'Retry-After': '5' }));
      } else {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(wireJob(polls === 1 ? 'queued' : 'done')) });
      }
      return true;
    }
    return false;
  });
  await page.goto('./');
  await upload(page);
  await expect(page.getByRole('list', { name: 'Recognised structures' })).toBeVisible({ timeout: 30_000 });
  const gets = seen.requests.filter((r) => r.method === 'GET' && r.path === JOB_PATH);
  expect(gets).toHaveLength(3);
  // The failed poll was the second; the third came after the 5 s the server asked for (not after the usual 2).
  expect(gets[2]!.at - gets[1]!.at).toBeGreaterThanOrEqual(4800);
});

const queuedThen = (answer: (poll: number) => Parameters<import('@playwright/test').Route['fulfill']>[0] | 'abort') =>
  async (route: import('@playwright/test').Route, poll: { n: number }) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST') {
      await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify(wireJob('queued')) });
      return true;
    }
    if (path !== JOB_PATH) return false;
    const next = answer(++poll.n);
    if (next === 'abort') await route.abort('connectionrefused');
    else await route.fulfill(next);
    return true;
  };

const ok = (kind: 'queued' | 'done') => ({ status: 200, contentType: 'application/json', body: JSON.stringify(wireJob(kind)) });

test('no answer while polling is retried with growing waits, past the three tries an unexpected answer gets', async ({ page }) => {
  test.setTimeout(90_000);
  const poll = { n: 0 };
  // Three polls get no answer in a row: for an unexpected answer that would be the end (see the next test).
  const seen = await fakeApi(page, (route) => queuedThen((n) => (n >= 2 && n <= 4 ? 'abort' : ok(n === 1 ? 'queued' : 'done')))(route, poll));
  await page.goto('./');
  await upload(page);
  await expect(page.getByRole('list', { name: 'Recognised structures' })).toBeVisible({ timeout: 60_000 });
  const gets = seen.requests.filter((r) => r.method === 'GET' && r.path === JOB_PATH);
  expect(gets).toHaveLength(5);
  // Failure 1 waits one poll interval (2 s), failure 2 twice that, failure 3 four times.
  expect(gets[2]!.at - gets[1]!.at).toBeGreaterThanOrEqual(1800);
  expect(gets[3]!.at - gets[2]!.at).toBeGreaterThanOrEqual(3800);
  expect(gets[4]!.at - gets[3]!.at).toBeGreaterThanOrEqual(7800);
});

test('after unexpected answers the page gives up and offers "Try again", which picks the same job up: no new upload', async ({ page }) => {
  test.setTimeout(60_000);
  const poll = { n: 0 };
  const seen = await fakeApi(page, (route) =>
    queuedThen((n) => (n >= 2 && n <= 4 ? apiError(500, 'unknown', 'Internal error.') : ok(n === 1 ? 'queued' : 'done')))(route, poll),
  );
  await page.goto('./');
  await upload(page);
  await expect(page.getByRole('heading', { level: 1, name: 'The job did not finish' })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('alert')).toContainText('The connection to the server was lost');
  await page.screenshot({ path: 'test-results/screens/connection-lost-desktop.png' }); // for the design review
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByRole('list', { name: 'Recognised structures' })).toBeVisible({ timeout: 15_000 });
  expect(seen.requests.filter((r) => r.method === 'POST')).toHaveLength(1);
  // Every poll, before and after, was for the same job.
  const gets = seen.requests.filter((r) => r.method === 'GET' && r.path.startsWith('/api/jobs/') && !r.path.includes('/media/'));
  expect(new Set(gets.map((r) => r.path))).toEqual(new Set([JOB_PATH]));
  expect(gets).toHaveLength(5);
});

test('a job that expired (404) is reported as lost, not polled for ever', async ({ page }) => {
  const seen = await fakeApi(page, async (route) => {
    if (route.request().method() === 'POST') {
      await route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify(wireJob('queued')) });
      return true;
    }
    return false; // GET /api/jobs/{id}: the default 404 not_found
  });
  await page.goto('./');
  await upload(page);
  await expect(page.getByRole('heading', { level: 1, name: 'The job did not finish' })).toBeVisible({ timeout: 10_000 });
  // Trying the same job again would end the same way: only a fresh upload is offered.
  await expect(page.getByRole('alert')).toContainText('The server no longer has this job');
  await expect(page.getByRole('button', { name: 'Try again' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Start over' })).toBeVisible();
  await page.waitForTimeout(3000);
  expect(seen.requests.filter((r) => r.method === 'GET' && r.path === JOB_PATH)).toHaveLength(1);
});

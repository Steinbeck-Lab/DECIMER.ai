import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, type Page, type Response } from '@playwright/test';
import { editorPanel, downloadButton, layoutOnCanvas, waitForEditor, watchProblems } from '../helpers';
import { readZip } from '../live/zip';
import { test } from '../returning';
import { CROP, dir, Facts, fileFrom, pasteFile, shot } from './shared';

// ROADMAP §6 item 17, the Safari warning: "re-test with Ketcher 3.18 and drop it if it is no longer needed".
// Legacy shows "We have some problems with Safari! ... the loading icons do not move while your data is processed"
// to every Safari user agent: its progress icons are animated GIFs, and Safari stops them while the page navigates
// (every stage of a job is a full page load). The new app is one page: its progress is CSS, and nothing navigates.
// Playwright's WebKit is the Safari engine, so this run answers three questions in it: is any Safari warning shown,
// do the animations of the working view keep running, and do Ketcher 3.18, the edit and the exports work.
// Run: PARITY_NEW_URL=http://127.0.0.1:8090 npx playwright test --config e2e/parity/parity.config.ts --project webkit

const facts = new Facts('webkit');
const isApi = (response: Response, method: string, pathEnd: string) =>
  response.request().method() === method && new URL(response.url()).pathname.endsWith(pathEnd);

/** CSS and SMIL animations that are running now, with where each one is (to see it move between two samples). */
async function animationClock(page: Page): Promise<{ name: string; playState: string; time: number }[]> {
  return page.evaluate(() =>
    document.getAnimations().map((a) => ({
      name: (a as CSSAnimation).animationName ?? a.constructor.name,
      playState: a.playState,
      time: Number(a.currentTime ?? 0),
    })),
  );
}

test('WebKit: no Safari warning, the working view animates, Ketcher 3.18 loads, an edit and a ZIP export work', async ({ page, browser, browserName }) => {
  test.setTimeout(420_000);
  const problems = watchProblems(page);
  const version = browser.version();
  facts.set('browser', { name: browserName, version, userAgent: await page.evaluate(() => navigator.userAgent) });
  let jobId = '';
  page.on('response', async (r) => {
    if (isApi(r, 'POST', '/api/jobs') && r.status() === 202) jobId = ((await r.json().catch(() => ({}))) as { id?: string }).id ?? '';
  });

  await page.goto('./');
  await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();
  const bodyText = await page.locator('body').innerText();
  facts.set('17.any Safari warning on the empty screen (new stack, WebKit)', {
    mentionsSafari: /safari/i.test(bodyText),
    mentionsFrozen: /frozen|do not move/i.test(bodyText),
    alerts: await page.getByRole('alert').count(),
  });
  await shot(page, 'webkit', 'w01-empty-webkit');

  // The working view: paste the image, and sample the animations twice, a moment apart.
  const created = page.waitForResponse((r) => isApi(r, 'POST', '/api/jobs'), { timeout: 60_000 });
  expect(await pasteFile(page, fileFrom(CROP, 'image.png'))).toBe(true);
  expect((await created).status()).toBe(202);
  await expect(page.getByRole('progressbar', { name: 'Progress' })).toBeVisible({ timeout: 30_000 });
  await shot(page, 'webkit', 'w02-working-webkit');
  // Sample the running animations every 100 ms for as long as the working view is up (a job of this size is over in
  // seconds, and the animation that is running changes as the stages move on): is any one of them advancing?
  const samples: { name: string; playState: string; time: number }[][] = [];
  while ((await page.getByRole('progressbar', { name: 'Progress' }).count()) > 0 && samples.length < 600) {
    samples.push(await animationClock(page));
    await page.waitForTimeout(100);
  }
  const advancing = new Set<string>();
  for (let i = 1; i < samples.length; i++) {
    for (const now of samples[i] ?? []) {
      if (now.playState === 'running' && (samples[i - 1] ?? []).some((before) => before.name === now.name && now.time > before.time)) advancing.add(now.name);
    }
  }
  facts.set('17.animations of the working view in WebKit (sampled every 100 ms while it was on screen)', {
    samples: samples.length,
    runningAndAdvancing: [...advancing],
    elapsedClockText: await page.getByText(/^Elapsed/).first().innerText().catch(() => null),
  });

  await expect(page.getByRole('list', { name: 'Recognised structures' })).toBeVisible({ timeout: 240_000 });
  await page.locator('.dc-grid[data-arriving="false"]').waitFor();
  await shot(page, 'webkit', 'w03-results-webkit');

  // Ketcher 3.18, the edit, the exports.
  await page.getByRole('button', { name: /^Structure 1\b/ }).click();
  await waitForEditor(page, 2500);
  await expect(editorPanel(page).getByRole('heading', { name: 'Editing 1' })).toBeVisible();
  await expect(page.locator('.Ketcher-root')).toHaveCount(1);
  const validated = page.waitForResponse((r) => isApi(r, 'POST', `/api/jobs/${jobId}/results/0/validate`), { timeout: 60_000 });
  await layoutOnCanvas(page);
  expect((await validated).status()).toBe(200);
  await expect(page.getByRole('button', { name: /^Structure 1\b/ })).toContainText('Edited');
  await shot(page, 'webkit', 'w04-editor-webkit');

  const exported = page.waitForResponse((r) => isApi(r, 'POST', `/api/jobs/${jobId}/export`), { timeout: 120_000 });
  await downloadButton(page).click();
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 120_000 }),
    page.getByRole('menuitem', { name: /^ZIP/ }).click({ noWaitAfter: true }),
  ]);
  expect((await exported).status()).toBe(200);
  const path = resolve(dir('webkit'), 'webkit-download.zip');
  await download.saveAs(path);
  const files = readZip(readFileSync(path));
  facts.set('17.Ketcher 3.18, an edit and a ZIP export in WebKit', {
    ketcherRoots: await page.locator('.Ketcher-root').count(),
    tile: (await page.getByRole('button', { name: /^Structure 1\b/ }).innerText()).replace(/\s+/g, ' ').trim().slice(-40),
    zipEntries: [...files.keys()],
    editedMolNotRDKit: !(files.get('structure-01.mol')?.toString('utf8') ?? 'RDKit').includes('RDKit'),
  });

  // Clean up: the job goes (a start-over with an edit asks first).
  const deleted = page.waitForResponse((r) => isApi(r, 'DELETE', `/api/jobs/${jobId}`), { timeout: 30_000 });
  await page.getByRole('banner').getByRole('button', { name: 'New upload' }).click();
  await page.getByRole('dialog', { name: 'Start a new upload?' }).getByRole('button', { name: 'Start new upload' }).click();
  expect((await deleted).status()).toBe(204);
  facts.set('console.errors, page errors and CSP violations in WebKit', problems);
  facts.flush();
  expect(problems).toEqual([]);
});

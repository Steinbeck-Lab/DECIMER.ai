import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { fixtureImage, layoutOnCanvas, waitForEditor } from './helpers';

// WCAG 2.x A and AA rules (the Lighthouse accessibility score is built on the same axe-core rules).
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

/** `exclude`: a selector for third-party markup (Ketcher's own toolbar, EPAM's) that is not ours to fix. */
async function audit(page: Page, context: string, exclude?: string) {
  const builder = new AxeBuilder({ page }).withTags(TAGS);
  if (exclude) builder.exclude(exclude);
  const { violations } = await builder.analyze();
  const summary = violations.map((v) => `${context}: ${v.id} (${v.impact}) ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
  expect(summary).toEqual([]);
}

async function results(page: Page) {
  await page.goto('./?mockStepMs=150');
  await page.locator('input[type=file]').setInputFiles(fixtureImage());
  await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 30_000 });
  // The tiles settle in over about 2 s; a contrast reading in the middle of that is not a reading of the design.
  await page.locator('.dc-grid[data-arriving="false"]').waitFor({ timeout: 15_000 });
}

test('empty screen, with an upload problem showing', async ({ page }) => {
  await page.goto('./');
  await audit(page, 'empty');
  await page.locator('input[type=file]').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('x') });
  await page.getByRole('alert').waitFor();
  await audit(page, 'empty with problem');
});

test('working screen', async ({ page }) => {
  await page.goto('./?mockStepMs=3000');
  await page.locator('input[type=file]').setInputFiles(fixtureImage());
  await page.getByRole('heading', { level: 1, name: 'Waiting for a free worker' }).waitFor();
  await audit(page, 'working');
});

test('results screen, idle and with a structure open (editor not yet loaded and loaded)', async ({ page }) => {
  test.setTimeout(120_000);
  await results(page);
  await audit(page, 'results idle');
  await page.getByRole('button', { name: /^Structure 6\b/ }).click();
  await page.getByRole('heading', { name: 'Editing 6' }).waitFor();
  // Ketcher may already be mounting by now (its code is fetched when the pointer reaches the results): its own
  // markup is EPAM's, so it is left out here too. The loading placeholder and the panel around it are ours.
  await audit(page, 'results with panel (editor loading)', '.Ketcher-root');
  await page.waitForSelector('[data-testid=top-toolbar]', { state: 'attached', timeout: 90_000 });
  await page.waitForTimeout(2000);
  // Ketcher's own toolbar is third-party markup (EPAM); the page around it is ours.
  const { violations } = await new AxeBuilder({ page }).withTags(TAGS).exclude('.dc-editor__canvas').analyze();
  expect(violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
});

test('static pages', async ({ page }) => {
  for (const hash of ['#/about', '#/privacy', '#/impressum']) {
    await page.goto(`./${hash}`);
    await page.locator('main h1').waitFor();
    await audit(page, hash);
  }
});

test('phone: results and the sheet', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await results(page);
  await audit(page, 'phone results');
  await page.getByRole('button', { name: /^Structure 6\b/ }).click();
  await page.getByRole('heading', { name: 'Editing 6' }).waitFor();
  await page.waitForTimeout(600);
  const { violations } = await new AxeBuilder({ page }).withTags(TAGS).exclude('.dc-editor__canvas').analyze();
  expect(violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
});

test('the stale tile of an edit the server has not answered yet still meets the contrast floor', async ({ page }) => {
  test.setTimeout(120_000);
  // A slow server keeps the result stale for seconds: the name and depiction from before the edit are marked.
  await page.goto('./?mockStepMs=150&mockLatencyMs=3500');
  await page.locator('input[type=file]').setInputFiles(fixtureImage());
  await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 40_000 });
  await page.locator('.dc-grid[data-arriving="false"]').waitFor({ timeout: 15_000 });
  await page.getByRole('button', { name: /^Structure 4\b/ }).click();
  await waitForEditor(page);
  await layoutOnCanvas(page);
  const stale = page.locator('[data-result-tile="2"][data-stale="true"]');
  await stale.waitFor({ timeout: 15_000 });

  // The tile is selected (sky fill) and an active button: its text needs 4.5:1 like any other.
  const { violations } = await new AxeBuilder({ page }).withTags(TAGS).exclude('.dc-editor__canvas').analyze();
  expect(violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  await expect(stale).toHaveAttribute('data-stale', 'true'); // still stale when the audit ran

  // The name says what is going on instead of showing the old one dimmed.
  await expect(stale.locator('.dc-tile__name')).toHaveText('Updating…');
});

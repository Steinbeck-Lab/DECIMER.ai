import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium, expect, test, type Page } from '@playwright/test';
import { startFlow } from 'lighthouse';
import puppeteer from 'puppeteer-core';
import { waitForEditor, watchProblems } from '../helpers';
import { CROP, Facts, shot } from './shared';

// TODO: Lighthouse accessibility of 90 or more, measured on the RUNNING stack (the production build under the
// edge's CSP, the real API and workers), where e2e/lighthouse.spec.ts measures `vite preview` with the mock API.
// Same method as that spec (Lighthouse's snapshot mode on Playwright's own Chromium, reached through its debugging
// port; the failing audits of a state are attributed to Ketcher's root or to us), against PARITY_NEW_URL, with one
// real job of the small image for the results states. The job is deleted at the end. With PARITY_LEGACY_URL set, the
// legacy home page is measured too (one page load, analytics blocked), for comparison.
// Run: PARITY_NEW_URL=http://127.0.0.1:8090 npx playwright test --config e2e/parity/parity.config.ts --project lighthouse

const facts = new Facts('lighthouse');
const newUrl = new URL(process.env.PARITY_NEW_URL ?? 'http://127.0.0.1:8090');
const legacyUrl = process.env.PARITY_LEGACY_URL ? new URL(process.env.PARITY_LEGACY_URL) : null;

interface Failing {
  id: string;
  selectors: string[];
}

async function measure(page: Page, port: number, name: string) {
  const pup = await puppeteer.connect({ browserURL: `http://127.0.0.1:${port}`, defaultViewport: null });
  try {
    const target = (await pup.pages()).find((p) => p.url() === page.url());
    if (!target) throw new Error(`Lighthouse cannot find the page ${page.url()}`);
    const flow = await startFlow(target as never, {
      config: {
        extends: 'lighthouse:default',
        settings: { onlyCategories: ['accessibility'], formFactor: 'desktop', screenEmulation: { disabled: true } },
      },
    });
    await flow.snapshot({ name });
    const lhr = (await flow.createFlowResult()).steps[0]!.lhr;
    const score = lhr.categories.accessibility?.score ?? 0;
    const failing: Failing[] = Object.values(lhr.audits)
      .filter((a) => a.score !== null && a.score < 1 && a.scoreDisplayMode === 'binary')
      .map((a) => {
        const items = (a.details as { items?: { node?: { selector?: string } }[] } | undefined)?.items ?? [];
        return { id: a.id, selectors: items.map((i) => i.node?.selector ?? '').filter(Boolean) };
      });
    return { score, failing };
  } finally {
    await pup.disconnect();
  }
}

/**
 * Where the failing nodes are. `ours`: outside Ketcher's root. `unattributed`: failing audits that name no node,
 * and selectors that match nothing (or are not selectors): nobody can say whose they are. Both must be empty.
 */
async function attribute(page: Page, failing: Failing[]) {
  return page.evaluate((groups) => {
    const ours: string[] = [];
    const unattributed: string[] = [];
    for (const { id, selectors } of groups) {
      if (selectors.length === 0) unattributed.push(`${id}: no node in the report`);
      for (const selector of selectors) {
        let found: Element[];
        try {
          found = [...document.querySelectorAll(selector)];
        } catch {
          unattributed.push(`${id}: not a selector: ${selector}`);
          continue;
        }
        if (found.length === 0) unattributed.push(`${id}: matches nothing: ${selector}`);
        for (const el of found) {
          if (!el.closest('.Ketcher-root')) ours.push(`${id}: ${selector}`);
        }
      }
    }
    return { ours, unattributed };
  }, failing);
}

/**
 * Chromium picks the debugging port itself (`--remote-debugging-port=0`, so parallel runs cannot collide) and
 * writes it to `DevToolsActivePort` in its profile directory, first line.
 */
async function debuggingPort(userDataDir: string): Promise<number> {
  const file = join(userDataDir, 'DevToolsActivePort');
  for (let attempt = 0; attempt < 100; attempt++) {
    const port = Number.parseInt((await readFile(file, 'utf8').catch(() => '')).split('\n')[0] ?? '', 10);
    if (port > 0) return port;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Chromium did not report its debugging port in ${file}`);
}


test('Lighthouse accessibility on the running stack: empty, results, results with Ketcher loaded, About', async () => {
  test.setTimeout(420_000);
  const userDataDir = await mkdtemp(join(tmpdir(), 'decimer-parity-lighthouse-'));
  const context = await chromium.launchPersistentContext(userDataDir, {
    args: ['--remote-debugging-port=0'],
    viewport: { width: 1440, height: 900 },
    baseURL: `${newUrl.origin}/`,
  });
  let jobId = '';
  try {
    // A returning visitor (a persistent context takes no storageState): no first-visit animation.
    await context.addInitScript(() => localStorage.setItem('decimer.visited', '1'));
    const port = await debuggingPort(userDataDir);
    const page = await context.newPage();
    const problems = watchProblems(page);
    page.on('response', async (r) => {
      if (r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/jobs' && r.status() === 202) {
        jobId = ((await r.json().catch(() => ({}))) as { id?: string }).id ?? '';
      }
    });

    await page.goto('./');
    const empty = await measure(page, port, 'empty');
    await shot(page, 'lighthouse', 'lh01-empty');

    await page.locator('input[type=file]').setInputFiles(CROP);
    await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 240_000 });
    await page.locator('.dc-grid[data-arriving="false"]').waitFor();
    const results = await measure(page, port, 'results, editor not opened');
    await shot(page, 'lighthouse', 'lh02-results');

    await page.getByRole('button', { name: /^Structure 1\b/ }).click();
    await waitForEditor(page, 2500);
    await expect(page.locator('.Ketcher-root')).toHaveCount(1);
    const withKetcher = await measure(page, port, 'results, Ketcher loaded');
    const whose = await attribute(page, withKetcher.failing);
    await shot(page, 'lighthouse', 'lh03-results-ketcher');

    // The job goes before the other pages are measured (they need no job).
    await page.getByRole('button', { name: 'Close' }).click();
    const deleted = page.waitForResponse((r) => r.request().method() === 'DELETE' && r.url().includes(`/api/jobs/${jobId}`), { timeout: 30_000 });
    await page.getByRole('banner').getByRole('button', { name: 'New upload' }).click();
    expect((await deleted).status()).toBe(204);
    jobId = '';

    await page.goto('./#/about');
    await page.getByRole('heading', { level: 1, name: 'About DECIMER.ai' }).waitFor();
    const about = await measure(page, port, 'about');
    await page.goto('./#/privacy');
    await page.getByRole('heading', { level: 1, name: 'Privacy' }).waitFor();
    const privacy = await measure(page, port, 'privacy');
    await page.goto('./#/impressum');
    await page.getByRole('heading', { level: 1, name: 'Impressum' }).waitFor();
    const impressum = await measure(page, port, 'impressum');

    const line = (name: string, r: { score: number; failing: Failing[] }) =>
      `${name}: ${Math.round(r.score * 100)}` + (r.failing.length ? ` (failing: ${r.failing.map((f) => `${f.id} x${f.selectors.length}`).join(', ')})` : '');
    const summary = [line('empty', empty), line('results', results), line('results with Ketcher loaded', withKetcher), line('about', about), line('privacy', privacy), line('impressum', impressum)];
    console.log('LIGHTHOUSE_ACCESSIBILITY', summary.join(' | '));
    facts.set('lighthouse accessibility, new stack (score x 100; failing audits and how many nodes)', summary);
    facts.set('lighthouse: failing nodes with Ketcher loaded, and whose they are', { failing: withKetcher.failing, ...whose });
    facts.set('lighthouse: base URL, lighthouse version, mode', { baseUrl: newUrl.origin, mode: 'snapshot (DOM as it is), desktop form factor, accessibility category only', chromium: context.browser()?.version() });
    facts.set('console.errors during the lighthouse run', problems);

    if (legacyUrl) {
      // The legacy home page, one load, analytics blocked: for comparison only.
      await context.route(/googletagmanager\.com|google-analytics\.com|matomo\.nfdi4chem\.de/, (route) => route.abort());
      const old = await context.newPage();
      await old.goto(`${legacyUrl.origin}/`);
      await old.waitForLoadState('load');
      const legacy = await measure(old, port, 'legacy home');
      facts.set('lighthouse accessibility, legacy home page (one load, snapshot)', line('legacy home', legacy));
      console.log('LIGHTHOUSE_LEGACY', line('legacy home', legacy));
    }

    // P4-13: 90 or more, with Ketcher loaded too; nothing failing outside Ketcher's own root.
    expect(whose).toEqual({ ours: [], unattributed: [] });
    expect(Math.min(empty.score, results.score, withKetcher.score, about.score, privacy.score, impressum.score)).toBeGreaterThanOrEqual(0.9);
    expect(problems).toEqual([]);
  } finally {
    if (jobId) await context.request.delete(`/api/jobs/${jobId}`).catch(() => null);
    facts.flush();
    await context.close();
    await rm(userDataDir, { recursive: true, force: true });
  }
});

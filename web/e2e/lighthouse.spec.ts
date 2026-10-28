import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium, expect, test, type Page } from '@playwright/test';
import { startFlow } from 'lighthouse';
import puppeteer from 'puppeteer-core';
import { fixtureImage, waitForEditor } from './helpers';

// Lighthouse's accessibility category on the live page (snapshot mode: the DOM as it is now, so
// the page can be in the state that matters: results, with Ketcher loaded). Lighthouse runs on Playwright's own
// Chromium, reached through its remote-debugging port. The score is printed so it can be recorded.
//
// The target is 90 or more, also with Ketcher loaded. Ketcher's own markup has unlabelled buttons and a
// textarea (src/editor/labels.ts gives them names and is kept in place); whatever else it gets wrong must be inside
// `.Ketcher-root`: nothing of ours may fail. A failing audit that does not say which nodes, or a node that is no
// longer on the page, cannot be attributed to anyone, so it fails the test too.

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

test('attribution: an audit that names no node, a selector that matches nothing and one that is not a selector belong to nobody', async ({ page }) => {
  await page.setContent('<div class="Ketcher-root"><button class="k">k</button></div><button class="ours">o</button>');
  const whose = await attribute(page, [
    { id: 'no-nodes', selectors: [] },
    { id: 'gone', selectors: ['button.vanished'] },
    { id: 'broken', selectors: ['button[[['] },
    { id: 'ketchers', selectors: ['button.k'] },
    { id: 'ours', selectors: ['button.ours'] },
  ]);
  expect(whose.unattributed).toEqual(['no-nodes: no node in the report', 'gone: matches nothing: button.vanished', expect.stringMatching(/^broken: not a selector/)]);
  expect(whose.ours).toEqual(['ours: button.ours']);
});

test('Lighthouse accessibility: the results page, with and without Ketcher loaded', async () => {
  test.setTimeout(240_000);
  const userDataDir = await mkdtemp(join(tmpdir(), 'decimer-lighthouse-'));
  const context = await chromium.launchPersistentContext(userDataDir, {
    args: ['--remote-debugging-port=0'],
    viewport: { width: 1440, height: 900 },
    baseURL: 'http://127.0.0.1:4173/',
  });
  try {
    // A tab that has shown the animation already (the flag is in session storage, which a persistent context takes no
    // storageState for): no first-visit animation.
    await context.addInitScript(() => sessionStorage.setItem('decimer.visited', '1'));
    const port = await debuggingPort(userDataDir);
    const page = await context.newPage();

    await page.goto('./');
    const empty = await measure(page, port, 'empty');

    await page.locator('input[type=file]').setInputFiles(fixtureImage());
    await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 40_000 });
    await page.locator('.dc-grid[data-arriving="false"]').waitFor();
    const results = await measure(page, port, 'results, editor not opened');

    await page.getByRole('button', { name: /^Structure 2\b/ }).click();
    await waitForEditor(page, 1500);
    await expect(page.locator('.Ketcher-root')).toHaveCount(1);
    const withKetcher = await measure(page, port, 'results, Ketcher loaded');
    // Whose are the failures? Asked now, while the page is as Lighthouse saw it.
    const whose = await attribute(page, withKetcher.failing);

    await page.goto('./#/about');
    await page.getByRole('heading', { level: 1, name: 'About DECIMER.ai' }).waitFor();
    const about = await measure(page, port, 'about');

    const line = (name: string, r: { score: number; failing: Failing[] }) =>
      `${name}: ${Math.round(r.score * 100)}` + (r.failing.length ? ` (failing: ${r.failing.map((f) => `${f.id} x${f.selectors.length}`).join(', ')})` : '');
    console.log('LIGHTHOUSE_ACCESSIBILITY', [line('empty', empty), line('results', results), line('results with Ketcher loaded', withKetcher), line('about', about)].join(' | '));
    if (withKetcher.failing.length) console.log('LIGHTHOUSE_KETCHER_FAILING', JSON.stringify(withKetcher.failing));

    // Our pages: no failing audit, so the score is the maximum.
    expect(empty.failing).toEqual([]);
    expect(results.failing).toEqual([]);
    expect(about.failing).toEqual([]);
    expect(Math.min(empty.score, results.score, about.score)).toBeGreaterThanOrEqual(0.9);

    // With Ketcher loaded: 90 or more, and whatever still fails is Ketcher's, from nodes that can be found.
    expect(whose).toEqual({ ours: [], unattributed: [] });
    expect(withKetcher.score).toBeGreaterThanOrEqual(0.9);
  } finally {
    await context.close();
    await rm(userDataDir, { recursive: true, force: true });
  }
});

import { expect, test } from '@playwright/test';

// TODO: one Ketcher editor reused across results, under the production CSP.
test('one editor swaps between results without cross-talk', async ({ page }) => {
  const problems: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') problems.push(`console: ${m.text()}`);
  });
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  await page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (e) =>
      console.error(`CSP violation: ${e.violatedDirective} ${e.blockedURI}`),
    );
  });

  await page.goto('spike/ketcher.html');
  await page.getByTestId('edit-0').click();
  const crashed = page.waitForEvent('pageerror', { timeout: 90_000 }).then((e) => {
    throw new Error(`editor crashed: ${e.message}`);
  });
  await Promise.race([
    page.waitForFunction(() => window.__spike.ready, null, { timeout: 90_000 }),
    crashed,
  ]);
  crashed.catch(() => undefined);

  const smiles = () => page.evaluate(() => window.__spike.ketcher!.getSmiles());

  // A: load, then edit (add a methanol fragment), so A's stored state differs from its original.
  await page.evaluate(() => window.__spike.select(0));
  expect(await smiles()).toContain('N');
  await page.evaluate(() => window.__spike.ketcher!.addFragment('CO'));
  const editedA = await smiles();
  expect(editedA).toContain('.');

  // C once, to record how Ketcher writes it.
  await page.evaluate(() => window.__spike.select(2));
  const c = await smiles();

  // B in the same editor: none of A's edit may leak in.
  await page.evaluate(() => window.__spike.select(1));
  const b = await smiles();
  expect(b).not.toContain('.');
  expect(b).not.toContain('N');

  // Back to A: the edit must come back.
  await page.evaluate(() => window.__spike.select(0));
  expect(await smiles()).toBe(editedA);

  // Rapid switching without awaiting: the last call wins and each result keeps its own state.
  await page.evaluate(() => {
    for (let i = 0; i < 10; i++) void window.__spike.select(i % 3);
    return window.__spike.select(2);
  });
  expect(await page.evaluate(() => window.__spike.current())).toBe(2);
  expect(await smiles()).toBe(c);
  await page.evaluate(() => window.__spike.select(0));
  expect(await smiles()).toBe(editedA);

  // Toolbars render at a usable size inside Mantine (no CSS collapse).
  const topBar = page.getByTestId('top-toolbar').locator('visible=true');
  expect((await topBar.boundingBox())?.width ?? 0).toBeGreaterThan(500);
  expect((await page.getByTestId('left-toolbar').locator('visible=true').boundingBox())?.height ?? 0)
    .toBeGreaterThan(300);

  // Recognize (EPAM Imago) and the 3D viewer are hidden; Undo proves the title lookup works.
  await expect(page.locator('[title^="Undo"]:visible')).not.toHaveCount(0);
  await expect(page.locator('[title^="Recognize Molecule"]:visible')).toHaveCount(0);
  await expect(page.locator('[title^="3D Viewer"]:visible')).toHaveCount(0);

  const metrics = await page.evaluate(() => {
    const res = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
    return {
      editorReadyMs: Math.round(window.__spike.editorReadyMs ?? -1),
      // Main-thread resources only: the Indigo WASM is fetched by the worker and not counted here.
      transferredKB: Math.round(res.reduce((s, r) => s + r.transferSize, 0) / 1024),
    };
  });
  console.log('SPIKE_METRICS', JSON.stringify(metrics));
  await page.screenshot({ path: 'test-results/ketcher-spike.png', fullPage: true });

  expect(problems).toEqual([]);
});

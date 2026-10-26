import { expect } from '@playwright/test';
import { test } from './returning';
import { editorPanel, fixtureImage, layoutOnCanvas, tile, waitForCanvas, waitForEditor, watchProblems } from './helpers';

// Indigo (11.8 MB of WASM, and its worker) can take long to arrive on a slow link. Ketcher keeps the promise of its
// first call to Indigo, `info()`, as the `server` that every later action starts from; if the editor's own bookkeeping
// of Indigo's calls treated that promise as stale, Layout, Clean Up and the like would fail for the rest of the page.

test('Layout still works after Indigo took longer to start than the editor waits for a swap', async ({ page, context }) => {
  test.setTimeout(240_000);
  const problems = watchProblems(page);
  const delayed: string[] = [];
  await context.route(/\.wasm|indigoWorker/, async (route) => {
    delayed.push(route.request().url().split('/').pop() ?? '');
    await new Promise((resolve) => setTimeout(resolve, 9000)); // more than the 5 s a swap waits for a call of Ketcher
    await route.continue();
  });

  await page.goto('./?mockStepMs=150');
  await page.locator('input[type=file]').setInputFiles(fixtureImage('figure2.png'));
  await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 30_000 });
  await tile(page, 2).click();
  await expect(editorPanel(page).getByRole('heading', { name: 'Editing 2' })).toBeVisible();
  await waitForEditor(page, 200);

  // The first structure is on the canvas once Indigo is up (the load itself needs it). Ketcher offers its server
  // actions (Layout, Aromatize ...) from then on: Aromatize is the one that has a button of its own.
  await waitForCanvas(page, 90_000);
  await expect(page.locator('[title^="Aromatize"]:visible').first()).toBeEnabled({ timeout: 60_000 });
  expect(delayed.length).toBeGreaterThan(0); // the test did slow the start

  await layoutOnCanvas(page);
  await expect(tile(page, 2)).toContainText('Edited', { timeout: 15_000 });
  expect(problems).toEqual([]); // Ketcher reports a refused call to the console
});

import { expect, test, type Page } from '@playwright/test';
import { clearCanvas, editorPanel, fixtureImage, fixturePdf, headerDownload, waitForEditor } from './helpers';

// Review screenshots (impeccable finish review). They land in test-results/screens/ and the
// orchestrator copies them to .impeccable/review/. They double as a check that every state renders.
const OUT = 'test-results/screens';
const PDF = fixturePdf('zou2024_penicillium_shentong.pdf');

async function results(page: Page, file: Parameters<typeof fixtureImage>[0] | 'pdf' | 'truncated.pdf' = 'pdf') {
  const upload = file === 'pdf' ? PDF : file === 'truncated.pdf' ? fixturePdf(file) : fixtureImage(file);
  await page.locator('input[type=file]').setInputFiles(upload);
  await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 60_000 });
  await page.waitForTimeout(1800); // arrival animation
}

const tile = (page: Page, n: number) => page.getByRole('button', { name: new RegExp(`^Structure ${n}\\b`) });

async function editInKetcher(page: Page) {
  await page.waitForSelector('[data-testid=top-toolbar]', { state: 'attached', timeout: 90_000 });
  await page.waitForTimeout(2500);
  const box = await page.locator('.dc-editor__canvas[data-active=true]').boundingBox();
  if (!box) throw new Error('no canvas');
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await page.keyboard.press('Control+a');
  await page.keyboard.press('Control+l'); // Ketcher layout: the coordinates change, so the structure is edited
  await page.keyboard.press('Control+Shift+a'); // deselect all
  await expect(page.getByRole('list', { name: 'Status of the structures' })).toContainText('1 edited');
}

test.describe('screenshots', () => {
  test('empty and working, desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('./?mockStepMs=1500');
    await page.getByRole('button', { name: 'Choose files' }).waitFor();
    await page.screenshot({ path: `${OUT}/empty-desktop.png` });

    await page.locator('input[type=file]').setInputFiles(PDF);
    await page.getByRole('heading', { level: 1, name: 'Reading the pages' }).waitFor({ timeout: 30_000 });
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${OUT}/working-desktop.png` });
  });

  test('queued and failed, desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('./?mockStepMs=3000');
    await page.locator('input[type=file]').setInputFiles(fixtureImage('figure2.png'));
    await page.getByRole('heading', { level: 1, name: 'Waiting for a free worker' }).waitFor();
    await page.screenshot({ path: `${OUT}/queued-desktop.png` });
  });

  test('results with the editor, desktop', async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('./?mockStepMs=150');
    await results(page);
    await page.screenshot({ path: `${OUT}/results-idle-desktop.png`, fullPage: true });

    await tile(page, 1).click();
    await editorPanel(page).getByRole('button', { name: 'Mark as checked' }).click();
    await tile(page, 2).click();
    await editInKetcher(page);
    // Right after the edit the server has not answered: what is on screen from before it is marked, not shown as current.
    await page.locator('[data-result-tile="1"][data-stale="true"]').waitFor();
    await page.screenshot({ path: `${OUT}/stale-desktop.png` });
    await page.locator('[data-result-tile="1"]:not([data-stale="true"])').waitFor({ timeout: 20_000 });
    await page.screenshot({ path: `${OUT}/desktop.png`, fullPage: true });
    await page.screenshot({ path: `${OUT}/desktop-viewport.png` });
  });

  test('menu, tooltip and confirmation, desktop', async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('./?mockStepMs=150');
    await results(page);
    await tile(page, 4).hover();
    await page.getByRole('tooltip').waitFor();
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${OUT}/tooltip-desktop.png`, clip: { x: 0, y: 150, width: 960, height: 420 } });

    await headerDownload(page).click();
    await page.getByRole('menuitem', { name: /^ZIP/ }).waitFor();
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${OUT}/menu-desktop.png`, clip: { x: 800, y: 0, width: 640, height: 240 } });
    await page.keyboard.press('Escape');

    await tile(page, 2).click();
    await editInKetcher(page);
    await page.getByRole('button', { name: 'New upload' }).click();
    await page.getByRole('dialog').waitFor();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/modal-desktop.png` });
  });

  test('twenty structures, desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('./?mockStepMs=150');
    await results(page, 'truncated.pdf');
    await page.screenshot({ path: `${OUT}/twenty-desktop.png`, fullPage: true });
  });

  test('phone: empty, working, results, sheet', async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('./?mockStepMs=1500');
    await page.getByRole('button', { name: 'Choose files' }).waitFor();
    await page.screenshot({ path: `${OUT}/empty-mobile.png`, fullPage: true });

    await page.locator('input[type=file]').setInputFiles(PDF);
    await page.getByRole('heading', { level: 1, name: 'Reading the pages' }).waitFor({ timeout: 30_000 });
    await page.screenshot({ path: `${OUT}/working-mobile.png`, fullPage: true });

    await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 60_000 });
    await page.waitForTimeout(1800);
    await page.screenshot({ path: `${OUT}/mobile.png`, fullPage: true });

    await tile(page, 6).click();
    await page.waitForSelector('[data-testid=top-toolbar]', { state: 'attached', timeout: 90_000 });
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${OUT}/mobile-sheet.png` });
    await editorPanel(page).evaluate((el) => el.scrollTo(0, 99999));
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${OUT}/mobile-sheet-bottom.png` });
  });

  test('an invalid structure and one the editor cannot load, desktop', async ({ page }) => {
    test.setTimeout(240_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('./?mockStepMs=150');
    await results(page, 'badmolfile.png');
    await tile(page, 2).click();
    await waitForEditor(page);
    await clearCanvas(page);
    await page.getByRole('button', { name: /^Structure 2\b/ }).filter({ hasText: 'Invalid SMILES' }).waitFor({ timeout: 20_000 });
    await editorPanel(page).getByRole('button', { name: 'Mark as checked' }).click();
    await page.screenshot({ path: `${OUT}/invalid-desktop.png` });

    await tile(page, 1).click();
    await editorPanel(page).getByRole('alert').waitFor();
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${OUT}/load-failed-desktop.png` });
  });

  test('the classifier was unavailable, desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('./?mockStepMs=150');
    await results(page, 'nocls.png');
    await page.screenshot({ path: `${OUT}/no-classifier-desktop.png` });
  });

  test('static page, desktop and phone', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('./#/about');
    await page.getByRole('heading', { level: 1, name: 'About DECIMER.ai' }).waitFor();
    await page.screenshot({ path: `${OUT}/about-desktop.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `${OUT}/about-mobile.png`, fullPage: true });
  });
});

test.describe('first visit', () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test('the animated logo, desktop and phone', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('./');
    await page.locator('img.dc-intro').waitFor();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${OUT}/first-visit-start-desktop.png` });
    await page.waitForTimeout(8500); // the GIF plays once, about 8 s, and ends on the logo
    await page.screenshot({ path: `${OUT}/first-visit-desktop.png` });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: `${OUT}/first-visit-mobile.png`, fullPage: true });
  });
});

import { resolve } from 'node:path';
import { expect, type Page } from '@playwright/test';
import { test } from './returning';
import {
  clearCanvas,
  editorPanel,
  fixtureImage,
  layoutOnCanvas,
  tile,
  waitForEditor,
  watchProblems,
} from './helpers';

// Behaviour added or tightened in the review round. Each test says what it pins.

const counts = (page: Page) => page.getByRole('list', { name: 'Status of the structures' });

async function openResults(page: Page, file = 'figure2.png', query = '?mockStepMs=150') {
  await page.goto(`./${query}`);
  await page.locator('input[type=file]').setInputFiles(fixtureImage(file));
  await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 40_000 });
}

test('the working view announces stage and queue in one polite live region, and not the ticking clock', async ({ page }) => {
  // A step is long enough for the sampling below to see every stage, also on a busy machine.
  await page.goto('./?mockStepMs=1200');
  await page.locator('input[type=file]').setInputFiles(fixtureImage());

  const live = page.locator('#main [aria-live]');
  await expect(live).toHaveCount(1);
  await expect(live).toHaveAttribute('aria-live', 'polite');
  await expect(live).toHaveAttribute('role', 'status');
  await expect(live).toHaveAttribute('aria-atomic', 'true');

  // What a screen reader would be told over the whole run.
  const heard: string[] = [];
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline && (await page.locator('#main [aria-live]').count()) > 0) {
    const text = (await live.textContent({ timeout: 2000 }).catch(() => null)) ?? '';
    if (text && heard.at(-1) !== text) heard.push(text);
    await page.waitForTimeout(80);
  }
  const all = heard.join(' | ');
  for (const part of [
    'Waiting for a free worker',
    'You are number 2 in the queue',
    'You are next in line',
    'Reading the pages',
    'Step 2 of 5. Next: Finding structures on each page.',
    'Finding structures on each page',
    'Also running: reading the pages.', // the stages overlap: seg has the first page while the others render
    'Also running: finding structures on each page.',
    'Recognising each structure',
    'Checking the results',
    'This is the last step.',
  ]) {
    expect(all, part).toContain(part);
  }
  // Every announcement is a change of stage or queue position: no entry for a clock tick.
  expect(all).not.toMatch(/Elapsed/);
});

test('when the classifier was unavailable nothing says "not sure it is a structure", and the job warning is shown', async ({ page }) => {
  await openResults(page, 'nocls.png');
  // No peach anywhere: not in the counts, not on a tile, not in the filter.
  await expect(counts(page)).not.toContainText('not sure');
  await expect(counts(page)).toContainText('1 to review'); // number 6, low confidence, as before
  await expect(page.locator('.dc-grid [data-status="notsure"]')).toHaveCount(0); // no tile, no status word, no dot
  await expect(page.getByRole('group', { name: 'Show structures' }).getByRole('button', { name: /Needs review/ })).toContainText('1');
  await expect(page.getByRole('note')).toContainText('The classifier could not be reached');

  // Number 12 is the one the classifier doubts in the normal scenario. Here it is just unchecked.
  await expect(tile(page, 12)).toContainText('Unchecked');
  await tile(page, 12).click();
  const panel = editorPanel(page);
  await expect(panel.getByRole('heading', { name: 'Editing 12' })).toBeVisible();
  await expect(panel).not.toContainText('not sure this is a chemical structure');
  await expect(panel.getByRole('alert')).toHaveCount(0);

  // With the classifier's answers the same result is flagged, so the difference is the data and not the view.
  await page.getByRole('banner').getByRole('button', { name: 'New upload' }).click();
  await page.locator('input[type=file]').setInputFiles(fixtureImage('figure2.png'));
  await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 40_000 });
  await expect(tile(page, 12)).toContainText('Not sure it is a structure');
});

test('the elapsed clock ticks outside the live region', async ({ page }) => {
  await page.goto('./?mockStepMs=2000');
  await page.locator('input[type=file]').setInputFiles(fixtureImage());
  const clock = page.getByText(/^Elapsed \d+:\d\d$/);
  await expect(clock).toBeVisible();
  expect(await clock.evaluate((el) => el.closest('[aria-live]') === null)).toBe(true);
  const first = await clock.textContent();
  await expect(clock).not.toHaveText(first ?? '', { timeout: 5000 });
});

test('leaving the page asks for confirmation as soon as there is an edit or a check, and not before', async ({ page }) => {
  await openResults(page);
  const wouldAsk = () =>
    page.evaluate(() => {
      const event = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    });

  expect(await wouldAsk()).toBe(false);
  await tile(page, 6).click();
  await editorPanel(page).getByRole('button', { name: 'Mark as checked' }).click();
  expect(await wouldAsk()).toBe(true);
  await editorPanel(page).getByRole('button', { name: 'Undo check' }).click();
  expect(await wouldAsk()).toBe(false); // nothing left to lose
});

test('a file dropped outside a drop zone is cancelled on the results, text drags are not', async ({ page }) => {
  await openResults(page);
  const result = await page.evaluate(() => {
    const send = (type: string, make: (transfer: DataTransfer) => void) => {
      const transfer = new DataTransfer();
      make(transfer);
      const event = new DragEvent(type, { dataTransfer: transfer, cancelable: true, bubbles: true });
      document.body.dispatchEvent(event);
      return { prevented: event.defaultPrevented, effect: transfer.dropEffect };
    };
    const file = (t: DataTransfer) => t.items.add(new File(['x'], 'figure.png', { type: 'image/png' }));
    const text = (t: DataTransfer) => t.setData('text/plain', 'CCO');
    return {
      fileOver: send('dragover', file),
      fileDrop: send('drop', file),
      textOver: send('dragover', text),
      textDrop: send('drop', text),
    };
  });
  expect(result.fileOver.prevented).toBe(true);
  expect(result.fileDrop.prevented).toBe(true);
  expect(result.textOver.prevented).toBe(false);
  expect(result.textDrop.prevented).toBe(false);
  await expect(tile(page, 1)).toBeVisible(); // still the results
});

/**
 * A real drag of a file from the desktop, through the browser's own drag-and-drop (the DevTools protocol), not
 * a synthetic event: a synthetic DataTransfer ignores dropEffect, so it cannot tell a drop the page accepted
 * from one it refused. A drop whose last dragover said "none" is not delivered to the page at all.
 */
async function dragFile(page: Page, file: string, at: { x: number; y: number }, drop = true) {
  const cdp = await page.context().newCDPSession(page);
  const data = { items: [], files: [file], dragOperationsMask: 1 };
  await cdp.send('Input.dispatchDragEvent', { type: 'dragEnter', ...at, data });
  await cdp.send('Input.dispatchDragEvent', { type: 'dragOver', ...at, data });
  if (drop) await cdp.send('Input.dispatchDragEvent', { type: 'drop', ...at, data });
  await cdp.detach();
}

const FIXTURE_DIR = resolve(import.meta.dirname, '../src/mocks/fixtures');

test("a molfile dropped into Ketcher's Open dialog is accepted on the results screen, while a file dropped on the page is still cancelled", async ({ page }) => {
  test.setTimeout(240_000);
  await openResults(page);
  await tile(page, 2).click();
  await waitForEditor(page);
  await page.locator('[title^="Open"]:visible').first().click();
  const zone = page.getByTestId('open-from-file-button');
  await zone.waitFor();
  const box = await zone.boundingBox();
  if (!box) throw new Error('no drop zone');

  // A probe on the window, registered after the page's own listeners, sees each drag as they left it.
  await page.evaluate(() => {
    const seen: { type: string; prevented: boolean; effect: string }[] = [];
    (window as unknown as { __drags: typeof seen }).__drags = seen;
    for (const type of ['dragover', 'drop']) {
      window.addEventListener(type, (e) => {
        const event = e as DragEvent;
        seen.push({ type, prevented: event.defaultPrevented, effect: event.dataTransfer?.dropEffect ?? '' });
      });
    }
  });
  const drags = () => page.evaluate(() => (window as unknown as { __drags: { type: string; prevented: boolean; effect: string }[] }).__drags.splice(0));

  // Dropped into Ketcher's own drop zone: the window guard leaves the drag alone, so Ketcher gets the file.
  await dragFile(page, resolve(import.meta.dirname, 'fixtures/one-carbon.mol'), { x: box.x + box.width / 2, y: box.y + box.height / 2 });
  await expect(page.getByTestId('open-structure-textarea')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('open-structure-textarea')).toHaveValue(/V2000/);
  // Ketcher's drop zone said "copy" and the page's guard left it so.
  const inDialog = await drags();
  expect(inDialog.filter((d) => d.type === 'dragover').length).toBeGreaterThan(0);
  expect(inDialog.filter((d) => d.type === 'dragover').every((d) => d.prevented && d.effect === 'copy')).toBe(true);
  await page.getByTestId('close-window-button').click();

  // Dropped anywhere else on the page: refused. The browser would otherwise open the image in this tab
  // and the results (and every edit) would be gone.
  const url = page.url();
  const tileBox = await tile(page, 4).boundingBox();
  if (!tileBox) throw new Error('no tile');
  await dragFile(page, resolve(FIXTURE_DIR, 'crop5.png'), { x: tileBox.x + tileBox.width / 2, y: tileBox.y + tileBox.height / 2 });
  await page.waitForTimeout(1500);
  const onPage = await drags();
  expect(onPage.filter((d) => d.type === 'dragover').length).toBeGreaterThan(0);
  // Every dragover over the page was cancelled and told "none", so the drop is refused and nothing opens.
  expect(onPage.filter((d) => d.type === 'dragover').every((d) => d.prevented && d.effect === 'none')).toBe(true);
  expect(page.url()).toBe(url);
  await expect(tile(page, 4)).toBeVisible();
  await expect(page.getByRole('list', { name: 'Recognised structures' })).toBeVisible();
});

test('one export at a time: while it runs the Download button is off, and only one file arrives', async ({ page }) => {
  await openResults(page, 'figure2.png', '?mockStepMs=150&mockLatencyMs=2000');
  let downloads = 0;
  page.on('download', () => downloads++);
  // The one Download of the screen. By its class, not by name: while it works its name is "Preparing the download…".
  const button = page.locator('.dc-download');
  await expect(button).toHaveCount(1);

  await button.click();
  // `noWaitAfter`: a click that starts a download otherwise does not return until the download has finished.
  await page.getByRole('menuitem', { name: /^ZIP/ }).click({ noWaitAfter: true });
  await expect(button).toBeDisabled();
  await expect(button).toHaveAttribute('aria-busy', 'true');
  await expect(button).toContainText('Preparing the download');
  await button.click({ force: true }); // pressing it again does nothing

  await expect(page.getByRole('status').filter({ hasText: 'Your download has started.' })).toBeVisible({ timeout: 10_000 });
  await expect(button).toBeEnabled();
  await page.waitForTimeout(2500);
  expect(downloads).toBe(1);
});

test('a structure the model cut off is flagged on its tile, said in the green notices tile, and noted in the editor', async ({ page }) => {
  const problems = watchProblems(page);
  await openResults(page, 'incomplete.png');
  await page.locator('.dc-grid[data-arriving="false"]').waitFor({ timeout: 15_000 });
  // The mock cuts off its third structure, number 4: a flag in the family of "Invalid SMILES", in the tile's name.
  await expect(tile(page, 4)).toContainText('Incomplete');
  await expect(tile(page, 4)).toContainText('Review');
  await expect(tile(page, 1)).not.toContainText('Incomplete');
  // One sentence in the second half of the green box, above the grid.
  const note = page.getByRole('note');
  await expect(note).toHaveText("Structure 4 was cut off at the model's length limit; check it in the editor.");
  await expect(page.locator('.dc-top__left .dc-notices')).toBeVisible();
  // In the editor, right under the name (above the SMILES, which is the cut-off text).
  await tile(page, 4).click();
  const panel = editorPanel(page);
  const cutOff = panel.getByRole('note');
  await expect(cutOff).toHaveText('Incomplete: the model stopped at its length limit; the structure is cut off.');
  const below = await panel.evaluate((el) => {
    const facts = el.querySelector('.dc-facts')!.getBoundingClientRect();
    const smiles = el.querySelector('.dc-code')!.getBoundingClientRect();
    const noteBox = el.querySelector('.dc-incomplete')!.getBoundingClientRect();
    return noteBox.top >= facts.bottom - 1 && noteBox.bottom <= smiles.top + 1;
  });
  expect(below).toBe(true);
  expect(problems).toEqual([]);
});

test('New upload counts checked results as well as edited ones, and keeping on keeps them', async ({ page }) => {
  await openResults(page);
  await tile(page, 6).click();
  await editorPanel(page).getByRole('button', { name: 'Mark as checked' }).click();
  await tile(page, 7).click();
  await editorPanel(page).getByRole('button', { name: 'Mark as checked' }).click();

  await page.getByRole('banner').getByRole('button', { name: 'New upload' }).click();
  const dialog = page.getByRole('dialog', { name: 'Start a new upload?' });
  await expect(dialog).toContainText('You have checked 2 structures.');
  await dialog.getByRole('button', { name: 'Keep working' }).click();
  await expect(counts(page)).toContainText('2 checked');

  await page.getByRole('banner').getByRole('button', { name: 'New upload' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Start new upload' }).click();
  await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();
});

test('Previous and Next step through "Needs review" even after the open result has left it', async ({ page }) => {
  await openResults(page);
  await page.getByRole('group', { name: 'Show structures' }).getByRole('button', { name: /Needs review/ }).click();
  await expect(page.locator('.dc-tile')).toHaveCount(2); // 6 (low confidence) and 12 (classifier doubt)

  await tile(page, 6).click();
  const panel = editorPanel(page);
  await expect(panel.getByRole('heading', { name: 'Editing 6' })).toBeVisible();
  await expect(panel).toContainText('1 of 2');

  // Checking 6 takes it out of the filter. Next must go to the next flagged one (12), not to 7.
  await panel.getByRole('button', { name: 'Mark as checked' }).click();
  await expect(page.locator('.dc-tile')).toHaveCount(1);
  await expect(panel).toContainText('1 of 2');
  await panel.getByRole('button', { name: 'Next structure' }).click();
  await expect(panel.getByRole('heading', { name: 'Editing 12' })).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Next structure' })).toBeDisabled();
  // 6 is no longer flagged and no longer open, so there is nothing before 12 in this filter.
  await expect(panel.getByRole('button', { name: 'Previous structure' })).toBeDisabled();
});

test('the editor code is fetched when the pointer first reaches the results, or keyboard focus first lands on a tile, not before', async ({ browser }) => {
  for (const how of ['hover', 'focus'] as const) {
    const context = await browser.newContext();
    await context.addInitScript(() => window.sessionStorage.setItem('decimer.visited', '1')); // this tab has shown the animation
    const page = await context.newPage();
    const editorRequests: string[] = [];
    page.on('request', (r) => {
      if (/\/KetcherEditor-[^/]*\.js/.test(r.url())) editorRequests.push(r.url());
    });
    await openResults(page);
    await page.waitForTimeout(500);
    expect(editorRequests, `${how}: nothing yet`).toEqual([]);

    const request = page.waitForRequest(/\/KetcherEditor-[^/]*\.js/, { timeout: 10_000 });
    if (how === 'hover') await tile(page, 2).hover();
    else await tile(page, 2).focus();
    await request;
    // The WASM is not part of that: it waits for the editor to mount.
    expect(await page.evaluate(() => performance.getEntriesByType('resource').some((e) => e.name.endsWith('.wasm')))).toBe(false);
    await context.close();
  }
});

test('the editor placeholder is styled and centred in the canvas while the editor loads', async ({ page }) => {
  await page.route('**/assets/KetcherEditor-*.js', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    await route.continue();
  });
  await openResults(page);
  await tile(page, 2).click();
  const loading = page.locator('.dc-editor-loading');
  await expect(loading).toBeVisible();
  await expect(loading).toContainText('Loading the structure editor');
  const canvas = await page.locator('.dc-editor__canvas[data-active=true]').boundingBox();
  const text = await loading.getByText('Loading the structure editor').boundingBox();
  if (!canvas || !text) throw new Error('no boxes');
  expect(Math.abs(text.x + text.width / 2 - (canvas.x + canvas.width / 2))).toBeLessThan(40);
  expect(Math.abs(text.y + text.height / 2 - (canvas.y + canvas.height / 2))).toBeLessThan(80);
  const spinner = await loading.locator('.dc-spinner').evaluate((el) => {
    const style = getComputedStyle(el);
    return { width: el.getBoundingClientRect().width, animation: style.animationName };
  });
  expect(spinner.width).toBeGreaterThan(16);
  expect(spinner.animation).toBe('dc-spin');
  await waitForEditor(page, 500);
  await expect(loading).toHaveCount(0);
});

test('below 900 px the editor is a modal dialog and the page behind it is inert; above 900 px it is neither', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openResults(page);
  expect(await page.evaluate(() => document.querySelectorAll('[inert]').length)).toBe(0);
  await tile(page, 6).click();
  const dialog = page.getByRole('dialog', { name: 'Structure editor' });
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute('aria-modal', 'true');
  const state = await page.evaluate(() => ({
    inert: [...document.querySelectorAll('.dc-contents')].map((el) => el.hasAttribute('inert')),
    header: !!document.querySelector('header')?.closest('[inert]'),
    figure: !!document.querySelector('#main')?.closest('[inert]'),
    footer: !!document.querySelector('footer')?.closest('[inert]'),
    dialogInert: !!document.querySelector('.dc-editor')?.closest('[inert]'),
    notices: !!document.querySelector('.dc-notice-region')?.closest('[inert]'),
  }));
  expect(state).toEqual({ inert: [true, true, true], header: true, figure: true, footer: true, dialogInert: false, notices: false });

  // Closing gives the page back.
  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(dialog).toBeHidden();
  expect(await page.evaluate(() => document.querySelectorAll('[inert]').length)).toBe(0);

  await page.setViewportSize({ width: 1440, height: 900 });
  await tile(page, 6).click();
  await expect(page.getByRole('complementary', { name: 'Structure editor' })).toBeVisible();
  expect(await page.evaluate(() => document.querySelectorAll('[inert]').length)).toBe(0);
  expect(await page.evaluate(() => document.querySelector('.dc-editor')?.getAttribute('aria-modal') ?? null)).toBeNull();
});

test('on a phone, nothing of the cards behind the sheet shows through or below its sticky footer', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openResults(page);
  await tile(page, 6).click();
  const dialog = page.getByRole('dialog', { name: 'Structure editor' });
  await expect(dialog.getByRole('button', { name: 'Mark as checked' })).toBeVisible();
  await page.waitForTimeout(500);
  for (const scroll of [0, 150, 330, 99999]) {
    await dialog.evaluate((el, y) => el.scrollTo(0, y), scroll);
    await page.waitForTimeout(120);
    const strays = await page.evaluate(() => {
      const foot = document.querySelector('.dc-editor__foot');
      const bad: number[] = [];
      // The bottom 24 px of the screen all belong to the footer: no card, no canvas, nothing from behind.
      for (let y = window.innerHeight - 24; y < window.innerHeight; y++) {
        const hit = document.elementFromPoint(195, y);
        if (!hit || !foot?.contains(hit)) bad.push(y);
      }
      return bad;
    });
    expect(strays, `scrolled to ${scroll}`).toEqual([]);
  }
});

test('at 1440 by 900 PubChem, COCONUT and Report share one row, with the stereo switch below them', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openResults(page);
  await tile(page, 1).click(); // shentonin A has stereocentres, so the switch is there too
  const panel = editorPanel(page);
  await expect(panel.getByRole('switch')).toBeVisible();
  const box = async (locator: ReturnType<typeof panel.getByRole>) => (await locator.boundingBox()) ?? { x: 0, y: 0, width: 0, height: 0 };
  const pubchem = await box(panel.getByRole('link', { name: /^PubChem/ }));
  const coconut = await box(panel.getByRole('link', { name: /^COCONUT/ }));
  const report = await box(panel.getByRole('button', { name: 'Report a problem' }));
  const toggle = await box(panel.getByRole('switch'));
  const middle = (b: { y: number; height: number }) => b.y + b.height / 2;
  expect(Math.abs(middle(pubchem) - middle(coconut))).toBeLessThan(6);
  expect(Math.abs(middle(pubchem) - middle(report))).toBeLessThan(8);
  expect(toggle.y).toBeGreaterThan(pubchem.y + 10);
  // And all of it is on screen at this height, with the buttons above it.
  const mark = await box(panel.getByRole('button', { name: 'Mark as checked' }));
  expect(mark.y).toBeLessThan(pubchem.y);
  expect(report.y + report.height).toBeLessThanOrEqual(900);
});

test('a structure the server calls invalid stands out on its tile and in the panel, also once it is checked', async ({ page }) => {
  test.setTimeout(240_000);
  const problems = watchProblems(page);
  await openResults(page);
  await tile(page, 2).click();
  const panel = editorPanel(page);
  await waitForEditor(page);
  await clearCanvas(page); // an empty canvas is not a structure; the mock server says so

  await expect(tile(page, 2)).toContainText('Edited', { timeout: 15_000 });
  await expect(tile(page, 2)).toContainText('Invalid SMILES', { timeout: 15_000 });
  await expect(tile(page, 2)).toContainText('No depiction');
  // The pill is a flex row with a gap, so the icon does not touch the words.
  const pill = await tile(page, 2).locator('.dc-status--invalid').evaluate((el) => {
    const style = getComputedStyle(el);
    const icon = el.querySelector('svg')?.getBoundingClientRect();
    const range = document.createRange();
    const text = [...el.childNodes].find((n) => n.nodeType === Node.TEXT_NODE);
    if (text) range.selectNodeContents(text);
    const words = range.getBoundingClientRect();
    return { display: style.display, gap: (words.left ?? 0) - (icon?.right ?? 0) };
  });
  expect(pill.display).toMatch(/flex/); // a flex item is blockified, so inline-flex computes to flex
  expect(pill.gap).toBeGreaterThanOrEqual(3);
  await expect(panel).toContainText('This SMILES is not valid');
  await expect(panel.locator('.dc-code__text')).toHaveText('No valid SMILES');
  // No InChIKey, so no links to search for it.
  await expect(panel.getByRole('link', { name: /^PubChem/ })).toHaveCount(0);

  // Allowed (the user may mean it) but the warning stays.
  await panel.getByRole('button', { name: 'Mark as checked' }).click();
  await expect(tile(page, 2)).toContainText('Checked');
  await expect(tile(page, 2)).toContainText('Invalid SMILES');
  await expect(panel).toContainText('This SMILES is not valid');
  expect(problems).toEqual([]);
});

test('until the server has answered an edit, nothing from before it is shown as current', async ({ page }) => {
  test.setTimeout(240_000);
  await openResults(page, 'figure2.png', '?mockStepMs=150&mockLatencyMs=2500');
  await tile(page, 4).click(); // fusarindole B: it has a verified name, so there is a name to go stale
  const panel = editorPanel(page);
  await expect(panel.getByRole('link', { name: /^PubChem/ })).toBeVisible();
  await expect(panel.locator('.dc-code__text')).toContainText('OC[C@H](O)C(c1c[nH]c2ccccc12)c1c[nH]c2ccccc12');
  await waitForEditor(page);
  await layoutOnCanvas(page);

  // The edit is in the store and the question is out: the old values are marked, not shown.
  await expect(tile(page, 4)).toContainText('Edited', { timeout: 15_000 });
  await expect(tile(page, 4)).toHaveAttribute('data-stale', 'true');
  await expect(panel.locator('.dc-code__text')).toHaveText('Updating…');
  await expect(panel.getByRole('button', { name: 'Copy SMILES' })).toBeDisabled();
  await expect(panel.getByRole('link', { name: /^PubChem/ })).toHaveCount(0);
  await expect(panel.getByRole('link', { name: /^COCONUT/ })).toHaveCount(0);
  await expect(panel.locator('.dc-link--off')).toHaveCount(2);
  await expect(panel.getByText('Updating…').first()).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Copy name' })).toHaveCount(0);

  // Then the answer arrives and everything is current again (the mock's Orthonym declines the name of an edited structure).
  await expect(tile(page, 4)).not.toHaveAttribute('data-stale', 'true', { timeout: 20_000 });
  await expect(panel.locator('.dc-code__text')).toContainText('OC[C@H](O)C(c1c[nH]c2ccccc12)');
  await expect(panel.getByRole('link', { name: /^PubChem/ })).toBeVisible();
  await expect(tile(page, 4)).toContainText('No verified name');
});

test('a structure Ketcher cannot load is not shown under another number: the panel says so, nothing is attributed, the next one works', async ({ page }) => {
  test.setTimeout(240_000);
  const problems = watchProblems(page);
  await openResults(page, 'badmolfile.png'); // structure 1 has a molfile Ketcher cannot read
  const panel = editorPanel(page);

  await tile(page, 2).click(); // a good one first: the canvas shows structure 2
  await waitForEditor(page);
  await tile(page, 1).click();
  await expect(panel.getByRole('heading', { name: 'Structure 1, not loaded in the editor' })).toBeVisible();
  await expect(panel.getByText('Not loaded', { exact: true })).toBeVisible();
  const alert = panel.getByRole('alert');
  await expect(alert).toContainText('could not be loaded into the editor');
  await expect(alert.getByRole('button', { name: 'Try again' })).toBeVisible();
  // The header does not say "Editing", and it is still one row: the number, the label, the position and the buttons.
  await expect(panel.getByText('Editing', { exact: true })).toHaveCount(0);
  const row = async (locator: ReturnType<typeof panel.getByRole>) => {
    const b = await locator.boundingBox();
    return (b?.y ?? 0) + (b?.height ?? 0) / 2;
  };
  const heading = await row(panel.getByRole('heading', { name: /^Structure 1, not loaded/ }));
  for (const name of ['Previous structure', 'Next structure', 'Close']) {
    expect(Math.abs((await row(panel.getByRole('button', { name }))) - heading), name).toBeLessThan(14);
  }

  // Trying to edit does nothing and nothing is attributed to 1 or to 2.
  await page.keyboard.press('Control+a');
  await page.keyboard.press('Control+l');
  await page.waitForTimeout(1500);
  await expect(counts(page)).not.toContainText('edited');
  // The rest of the panel still works: the crop, the SMILES, the decision.
  await expect(panel.locator('.dc-code__text')).toContainText('C=CC(C)(C)');
  await panel.getByRole('button', { name: 'Mark as checked' }).click();
  await expect(tile(page, 1)).toContainText('Checked');
  await alert.getByRole('button', { name: 'Try again' }).click();
  await expect(alert).toBeVisible(); // still unreadable

  // The next structure loads and can be edited, and only it changes.
  await panel.getByRole('button', { name: 'Next structure' }).click();
  await expect(panel.getByRole('heading', { name: 'Editing 2' })).toBeVisible();
  await expect(panel.getByRole('alert')).toHaveCount(0);
  await page.waitForTimeout(800);
  await layoutOnCanvas(page);
  await expect(tile(page, 2)).toContainText('Edited', { timeout: 15_000 });
  await expect(tile(page, 1)).not.toContainText('Edited');
  await expect(counts(page)).toContainText('1 edited');
  expect(problems).toEqual([]);
});

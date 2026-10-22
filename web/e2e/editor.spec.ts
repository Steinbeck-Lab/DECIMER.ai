import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { atomCount, editorPanel, fixtureImage, headerDownload, layoutOnCanvas, waitForEditor, watchProblems } from './helpers';

// The real editor: one Ketcher instance, reused across results, under the production CSP.

const tile = (page: Page, n: number) => page.getByRole('button', { name: new RegExp(`^Structure ${n}\\b`) });
const panelOf = editorPanel;
const counts = (page: Page) => page.getByRole('list', { name: 'Status of the structures' });

async function openResults(page: Page, file = 'figure2.png') {
  await page.goto('./?mockStepMs=150');
  await page.locator('input[type=file]').setInputFiles(fixtureImage(file));
  await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 30_000 });
}

/** Waits for Ketcher, then edits the structure on the canvas the way a user does: select all, Layout (Ctrl+L). */
async function editOnCanvas(page: Page) {
  await waitForEditor(page);
  await layoutOnCanvas(page);
}

async function molfileText(page: Page) {
  const details = panelOf(page).locator('details.dc-molfile');
  if (!(await details.evaluate((el) => (el as HTMLDetailsElement).open))) await details.locator('summary').click();
  return (await details.locator('pre').textContent()) ?? '';
}

test('an edit marks the result edited, survives switching and never leaks into another result', async ({ page }) => {
  test.setTimeout(240_000);
  const problems = watchProblems(page);
  await openResults(page);
  const panel = panelOf(page);

  await tile(page, 2).click();
  await editOnCanvas(page);
  await expect(tile(page, 2)).toContainText('Edited');
  await expect(counts(page)).toContainText('1 edited');
  await expect(panel.getByText('Edited by you')).toBeVisible();
  const edited = await molfileText(page);
  expect(edited).not.toContain('RDKit'); // Ketcher wrote it, not the pipeline

  // Opening other structures in the same editor does not touch them, and they do not inherit the edit.
  await tile(page, 4).click();
  await expect(panel.getByRole('heading', { name: 'Editing 4' })).toBeVisible();
  await expect(panel.getByText('Same as recognised')).toBeVisible();
  await tile(page, 6).click();
  await expect(panel.getByRole('heading', { name: 'Editing 6' })).toBeVisible();
  await expect(counts(page)).toContainText('1 edited');
  await expect(counts(page)).not.toContainText('2 edited');

  // Ten quick switches (un-awaited by the UI) end on the last one with every state intact.
  const next = panel.getByRole('button', { name: 'Next structure' });
  const previous = panel.getByRole('button', { name: 'Previous structure' });
  for (let i = 0; i < 5; i++) {
    await next.click();
    await previous.click();
  }
  await expect(panel.getByRole('heading', { name: 'Editing 6' })).toBeVisible();

  // Back to the edited one: its edit is on the canvas and in the store.
  await tile(page, 2).click();
  await expect(panel.getByText('Edited by you')).toBeVisible();
  expect(await molfileText(page)).toBe(edited);
  await expect(counts(page)).toContainText('1 edited');

  // Undo cannot reach across a switch: the history of another structure is gone, so pressing it on 4 changes nothing.
  await tile(page, 4).click();
  await expect(panel.getByRole('heading', { name: 'Editing 4' })).toBeVisible();
  await page.waitForTimeout(800);
  const box = await page.locator('.dc-editor__canvas[data-active=true]').boundingBox();
  if (!box) throw new Error('canvas is not showing');
  await page.mouse.click(box.x + box.width / 2, box.y + box.height - 12);
  await page.keyboard.press('Control+z');
  await page.keyboard.press('Control+z');
  await page.waitForTimeout(800);
  await expect(panel.getByText('Same as recognised')).toBeVisible();
  await expect(counts(page)).toContainText('1 edited');
  await expect(counts(page)).not.toContainText('2 edited');

  expect(problems).toEqual([]);
});

test('undoing an edit in the editor brings the result back to "same as recognised"', async ({ page }) => {
  test.setTimeout(240_000);
  await openResults(page);
  await tile(page, 7).click();
  await editOnCanvas(page);
  await expect(tile(page, 7)).toContainText('Edited');
  // Ctrl+Z: Ketcher's own undo. The canvas is back at its first state, so the edit has cancelled out.
  await page.keyboard.press('Control+z');
  await expect(tile(page, 7)).toContainText('Unchecked');
  await expect(counts(page)).not.toContainText('edited');
  await expect(panelOf(page).getByText('Same as recognised')).toBeVisible();
});

test('Reset puts the recognised structure back on the canvas and in the store', async ({ page }) => {
  test.setTimeout(240_000);
  await openResults(page);
  const panel = panelOf(page);
  await tile(page, 5).click();
  await expect(panel.getByRole('button', { name: 'Reset' })).toBeDisabled();
  await editOnCanvas(page);
  await expect(tile(page, 5)).toContainText('Edited');
  await expect(panel.getByRole('button', { name: 'Reset' })).toBeEnabled();

  await panel.getByRole('button', { name: 'Reset' }).click();
  await expect(tile(page, 5)).toContainText('Unchecked');
  await expect(counts(page)).not.toContainText('edited');
  expect(await molfileText(page)).toContain('RDKit');
  await page.waitForTimeout(1200); // no late edit may come back after the reset
  await expect(counts(page)).not.toContainText('edited');
});

async function readDownload(page: Page, format: RegExp) {
  await headerDownload(page).click();
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('menuitem', { name: format }).click()]);
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(chunk as Buffer);
  return Buffer.concat(chunks).toString('utf8');
}

/** `[number, edited|original, whose molfile]` for each structure of the mock's ZIP, in file order. */
function zipSections(text: string) {
  return text
    .split(/\n== structure /)
    .slice(1)
    .map((s) => [Number(s.match(/^(\d+)/)?.[1]), s.match(/^\d+ \((\w+)\)/)?.[1], s.includes('RDKit') ? 'pipeline' : 'editor'] as const);
}

test('the download of twenty structures carries the edited ones, in result order, and the others as recognised', async ({ page }) => {
  test.setTimeout(240_000);
  await openResults(page, 'twenty.png');
  // Edit 14 first and 3 after it, the other way round to their order in the file.
  await tile(page, 14).click();
  await editOnCanvas(page);
  await expect(tile(page, 14)).toContainText('Edited', { timeout: 15_000 });
  await tile(page, 3).click();
  await expect(panelOf(page).getByRole('heading', { name: 'Editing 3' })).toBeVisible();
  await page.waitForTimeout(800);
  await layoutOnCanvas(page);
  await expect(tile(page, 3)).toContainText('Edited', { timeout: 15_000 });

  const sections = zipSections(await readDownload(page, /^ZIP/));
  expect(sections.map(([n]) => n)).toEqual(Array.from({ length: 20 }, (_, i) => i + 1));
  expect(sections.filter(([, state]) => state === 'edited').map(([n]) => n)).toEqual([3, 14]);
  for (const [n, state, who] of sections) {
    expect(who, `structure ${n}`).toBe(state === 'edited' ? 'editor' : 'pipeline');
  }
});

// The slow-debounce build (playwright.config.ts: VITE_EDIT_DEBOUNCE_MS=4000) keeps an edit in Ketcher for 4 s
// before the store hears of it, so the download can really be pressed inside that window.
test.describe('the download inside the edit debounce', () => {
  test.use({ baseURL: 'http://127.0.0.1:4174/' });

  test('an edit that the store has not heard of yet is in the file, because the export flushes the editor first', async ({ page }) => {
    test.setTimeout(240_000);
    await openResults(page, 'twenty.png');
    await tile(page, 14).click();
    await waitForEditor(page);
    await layoutOnCanvas(page);

    // The edit is on the canvas (Ketcher's Undo has come alive) but the store has not been told: nothing says "edited".
    await expect(page.locator('button[title^="Undo"]:visible').first()).toBeEnabled({ timeout: 10_000 });
    expect(await counts(page).textContent()).not.toContain('edited');
    await expect(tile(page, 14)).not.toContainText('Edited', { timeout: 0 });

    const text = await readDownload(page, /^ZIP/);
    const sections = zipSections(text);
    expect(sections.filter(([, state]) => state === 'edited').map(([n]) => n)).toEqual([14]);
    expect(sections.find(([n]) => n === 14)?.[2]).toBe('editor');
    expect(sections.map(([n]) => n)).toEqual(Array.from({ length: 20 }, (_, i) => i + 1));

    // And the store catches up on its own afterwards.
    await expect(tile(page, 14)).toContainText('Edited', { timeout: 15_000 });
  });
});

test('the editor is full size, hides Recognize and the 3D viewer, and opens in good time (ported from the Phase F spike)', async ({ page }) => {
  test.setTimeout(240_000);
  const problems = watchProblems(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  await openResults(page);
  const clicked = Date.now();
  await tile(page, 2).click();
  await waitForEditor(page, 0);
  const openedMs = Date.now() - clicked;

  const canvas = await page.locator('.dc-editor__canvas[data-active=true]').boundingBox();
  const topBar = await page.getByTestId('top-toolbar').locator('visible=true').boundingBox();
  const leftBar = await page.getByTestId('left-toolbar').locator('visible=true').boundingBox();
  // The toolbars render at a usable size inside Mantine and the panel (no CSS clash, no collapse).
  expect(topBar?.width ?? 0).toBeGreaterThan((canvas?.width ?? 0) * 0.8);
  expect(leftBar?.height ?? 0).toBeGreaterThan(250);
  // Recognize (EPAM Imago) and the 3D viewer are hidden; Undo proves the title lookup works.
  await expect(page.locator('[title^="Undo"]:visible')).not.toHaveCount(0);
  await expect(page.locator('[title^="Recognize Molecule"]:visible')).toHaveCount(0);
  await expect(page.locator('[title^="3D Viewer"]:visible')).toHaveCount(0);
  // Exactly one editor, ever.
  await expect(page.locator('.Ketcher-root')).toHaveCount(1);

  const kilobytes = await page.evaluate(() => {
    const res = performance.getEntriesByType('resource') as PerformanceResourceTiming[];
    return Math.round(res.reduce((sum, r) => sum + r.transferSize, 0) / 1024);
  });
  console.log('EDITOR_METRICS', JSON.stringify({ openedMs, mainThreadKB: kilobytes }));
  expect(openedMs).toBeLessThan(60_000);
  expect(problems).toEqual([]);
});

test('ten quick Next and Previous presses end on the right structure, with the canvas showing it', async ({ page }) => {
  test.setTimeout(240_000);
  const problems = watchProblems(page);
  await openResults(page);
  await tile(page, 1).click();
  await waitForEditor(page);
  const panel = panelOf(page);

  // Ten presses with Enter, one straight after the other, without waiting for Ketcher between them:
  // 7 times Next, 3 times Previous, so four steps on from the first.
  await panel.getByRole('button', { name: 'Next structure' }).focus();
  for (let i = 0; i < 7; i++) await page.keyboard.press('Enter');
  await panel.getByRole('button', { name: 'Previous structure' }).focus();
  for (let i = 0; i < 3; i++) await page.keyboard.press('Enter');
  // Result numbers are 1, 2, 4, 5, 6, 7, 11, 12: four steps on from the first is number 6.
  await expect(panel.getByRole('heading', { name: 'Editing 6' })).toBeVisible();
  await page.waitForTimeout(1500);

  // The canvas shows structure 6: an edit made there has its atoms, not those of a structure passed on the way.
  await layoutOnCanvas(page);
  await expect(tile(page, 6)).toContainText('Edited', { timeout: 15_000 });
  const fixture = JSON.parse(readFileSync(resolve(import.meta.dirname, '../src/mocks/fixtures/compounds.json'), 'utf8')) as {
    number: number;
    molfile: string;
  }[];
  const expectedAtoms = atomCount(fixture.find((c) => c.number === 6)?.molfile ?? '');
  expect(expectedAtoms).not.toBeNull();
  expect(atomCount(await molfileText(page))).toBe(expectedAtoms);
  // Nothing else was touched on the way.
  await expect(counts(page)).toContainText('1 edited');
  expect(problems).toEqual([]);
});

test('the Edited chip keeps counting a result that was checked after it was edited', async ({ page }) => {
  test.setTimeout(240_000);
  await openResults(page);
  await tile(page, 5).click();
  await editOnCanvas(page);
  await expect(tile(page, 5)).toContainText('Edited', { timeout: 15_000 });
  const filters = page.getByRole('group', { name: 'Show structures' });
  await expect(filters.getByRole('button', { name: /^Edited/ })).toContainText('1');

  await panelOf(page).getByRole('button', { name: 'Mark as checked' }).click();
  await expect(tile(page, 5)).toContainText('Checked');
  // Still one in "Edited", and the chip and the tiles it opens agree.
  await expect(filters.getByRole('button', { name: /^Edited/ })).toContainText('1');
  await filters.getByRole('button', { name: /^Edited/ }).click();
  await expect(page.locator('.dc-tile')).toHaveCount(1);
  await expect(tile(page, 5)).toBeVisible();
});

test('stepping through structures with the keyboard keeps focus on the button that was pressed', async ({ page }) => {
  test.setTimeout(240_000);
  await openResults(page);
  const panel = panelOf(page);
  await tile(page, 1).click();
  await page.waitForSelector('[data-testid=top-toolbar]', { state: 'attached', timeout: 90_000 });
  await page.waitForTimeout(1500);

  const next = panel.getByRole('button', { name: 'Next structure' });
  await next.focus();
  for (let i = 0; i < 3; i++) {
    await page.keyboard.press('Enter');
    await page.waitForTimeout(600); // Ketcher loads the molecule and may grab focus
    await expect(next).toBeFocused();
  }
  await expect(panel.getByRole('heading', { name: 'Editing 5' })).toBeVisible();
});

test('opening a structure by keyboard keeps focus in the panel while Ketcher starts', async ({ page }) => {
  test.setTimeout(240_000);
  await openResults(page);
  await tile(page, 4).focus();
  await page.keyboard.press('Enter');
  const panel = panelOf(page);
  await expect(panel.getByRole('heading', { name: 'Editing 4' })).toBeFocused();
  // Ketcher starts and, left alone, moves focus to its hidden clipboard field. The panel takes it back,
  // so Tab and Escape still work from where the user is.
  await page.waitForSelector('[data-testid=top-toolbar]', { state: 'attached', timeout: 90_000 });
  await page.waitForTimeout(2500);
  await expect(panel.getByRole('heading', { name: 'Editing 4' })).toBeFocused();
  await page.keyboard.press('Tab');
  expect(await panel.evaluate((el) => el.contains(document.activeElement))).toBe(true);
});

test('New upload asks before it drops edits; keeping on works, confirming clears everything', async ({ page }) => {
  test.setTimeout(240_000);
  await openResults(page);
  await tile(page, 1).click();
  await editOnCanvas(page);
  await expect(counts(page)).toContainText('1 edited');

  await page.getByRole('button', { name: 'New upload' }).click();
  const dialog = page.getByRole('dialog', { name: 'Start a new upload?' });
  await expect(dialog).toContainText('You have edited 1 structure.');
  await dialog.getByRole('button', { name: 'Keep working' }).click();
  await expect(dialog).toBeHidden();
  await expect(counts(page)).toContainText('1 edited');

  await page.getByRole('button', { name: 'New upload' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Start new upload' }).click();
  await expect(page.getByRole('button', { name: 'Choose files' })).toBeVisible();

  // The next job starts clean: the same editor, nothing of the old edits.
  await page.locator('input[type=file]').setInputFiles(fixtureImage());
  await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 30_000 });
  await expect(page.getByRole('list', { name: 'Status of the structures' })).not.toContainText('edited');
  await tile(page, 1).click();
  await expect(panelOf(page).getByText('Same as recognised')).toBeVisible();
  await expect(page.locator('.Ketcher-root')).toHaveCount(1);
});

// P4-14, the "one-press focus dip". Not ours: it is in Ketcher 3.18.0 (ketcher-react, ArrowScroll), and this test
// pins the diagnosis so a Ketcher upgrade that fixes it is noticed.
//
// A Ketcher toolbar that is taller than the panel has scroll arrows after its last button. The "forward" arrow
// (data-testid arrow-scroll-right-button, drawn as ▼ in a vertical toolbar) is only rendered while the end of the
// toolbar is not in view (`endInView`). Tab from the last button ("Add Image", "Extended Table") puts focus on that
// arrow; focusing it scrolls the toolbar until its end is in view; Ketcher then stops rendering the arrow, the
// focused element leaves the DOM, and focus falls to the body. The next Tab goes on from where the arrow was, so
// nothing is stuck. The panel's focus trap is not involved: the arrow is inside the panel, and the trap only acts
// at the panel's own first and last controls.
//
// When this test starts failing, Ketcher has fixed it: delete it, and the tolerance for a one-press dip in the
// "traps focus" test (app.spec.ts).
test('known upstream (Ketcher 3.18.0): Tab from the last toolbar button lands on a scroll arrow that Ketcher then removes, and focus falls to the body', async ({ page }) => {
  test.setTimeout(240_000);
  await openResults(page);
  await tile(page, 4).click();
  await waitForEditor(page, 1000);
  await page.evaluate(() => {
    const w = window as unknown as { __focused?: HTMLElement };
    document.addEventListener('focusin', (e) => (w.__focused = e.target as HTMLElement), true);
  });

  for (const last of ['Add Image', 'Extended Table']) {
    await page.locator(`[title="${last}"]:visible`).first().focus();
    await page.keyboard.press('Tab');
    await page.waitForTimeout(300);
    const after = await page.evaluate(() => {
      const focused = (window as unknown as { __focused?: HTMLElement }).__focused;
      return {
        landedOn: focused?.getAttribute('data-testid') ?? focused?.getAttribute('title') ?? focused?.tagName,
        stillInThePage: focused?.isConnected,
        activeIsBody: document.activeElement === document.body,
      };
    });
    expect(after, `after ${last}`).toEqual({ landedOn: 'arrow-scroll-right-button', stillInThePage: false, activeIsBody: true });
  }

  // One more Tab goes on from there, into the panel: nothing is trapped.
  await page.keyboard.press('Tab');
  expect(await panelOf(page).evaluate((el) => el.contains(document.activeElement))).toBe(true);
});

// P4-13: Lighthouse flags three controls of Ketcher 3.18.0 that have no name (button-name x2, label x1). The
// page names them (src/editor/labels.ts, text for EPAM in docs/ketcher-a11y-upstream.md). Ketcher mounts after the
// panel does, so this fails if the labels are only applied once, at panel mount. Whether they survive Ketcher
// replacing the nodes is shown by labels.test.ts: no interaction in the browser makes Ketcher replace them.
test('Ketcher\'s controls without a name are named once it has loaded, and stay named when another structure is opened', async ({ page }) => {
  test.setTimeout(240_000);
  await openResults(page);
  await tile(page, 2).click();
  await waitForEditor(page, 1000);

  const names = () =>
    page.evaluate(() => ({
      triangles: [...document.querySelectorAll('.Ketcher-root [data-testid="copy-button-dropdown-triangle"]')].map((el) => el.getAttribute('aria-label')),
      clipboard: [...document.querySelectorAll('.Ketcher-root textarea.cliparea')].map((el) => el.getAttribute('aria-label')),
    }));
  const expected = {
    triangles: ['More options for Copy', 'More options for Aromatize'],
    clipboard: ['Clipboard for the structure editor'],
  };
  expect(await names()).toEqual(expected);

  await tile(page, 4).click();
  await expect(panelOf(page).getByRole('heading', { name: 'Editing 4' })).toBeVisible();
  await page.waitForTimeout(1000);
  expect(await names()).toEqual(expected);
});

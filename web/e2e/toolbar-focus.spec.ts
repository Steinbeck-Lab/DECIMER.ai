import { expect, test, type Page } from '@playwright/test';
import { fixtureImage, waitForEditor } from './helpers';

// P4-14: keyboard focus inside Ketcher 3.18.0's toolbars (text for EPAM in docs/ketcher-a11y-upstream.md, items 4 and 5;
// the workarounds are in src/editor/labels.ts and src/editor/focusKeys.ts).
//
// 1. The "one-press focus dip". Ketcher puts a scroll arrow (a plain, tabbable <button>, data-testid
//    arrow-scroll-right-button or -left-button) after the last button of a toolbar that is longer than its space, and
//    stops rendering it as soon as the end of the toolbar is in view. Focusing the last button ("Add Image", "Extended
//    Table") scrolls the toolbar; a Tab pressed before the scroll has finished lands on the arrow; the scroll then ends,
//    Ketcher removes the arrow, and focus falls to <body> until the next press. The page takes the arrows out of the
//    tab order. The panel's focus trap is not involved: the arrows are inside the panel, and the trap only acts at the
//    panel's own first and last controls.
// 2. Shift+Tab is one of Ketcher's hotkeys for its selection tools: its handler cancels the key, so focus did not move
//    backwards from a toolbar button or the clipboard field. The page lets Shift+Tab through to the browser.
//
// Ketcher takes about 300 ms to remove an arrow, so every press is checked at 0, 400 and 800 ms: a landing on an
// element that disappears shows up at the second or third reading, not at the first.

const tile = (page: Page, n: number) => page.getByRole('button', { name: new RegExp(`^Structure ${n}\\b`) });

async function openEditor(page: Page) {
  await page.goto('./?mockStepMs=150');
  await page.locator('input[type=file]').setInputFiles(fixtureImage());
  await page.getByRole('list', { name: 'Recognised structures' }).waitFor({ timeout: 30_000 });
  await tile(page, 4).click();
  await waitForEditor(page, 1000);
}

const visible = (page: Page, title: string) => page.locator(`[title="${title}"]:visible`).first();

/** Where keyboard focus is, in terms the assertions can read. `moved`: it is not on the element it was on at the last `mark`. */
function focusNow(page: Page) {
  return page.evaluate(() => {
    const active = document.activeElement;
    const panel = document.querySelector('.dc-editor');
    return {
      id: active === document.body ? 'BODY' : (active?.getAttribute('data-testid') ?? active?.getAttribute('title') ?? active?.getAttribute('aria-label') ?? active?.tagName ?? 'nothing'),
      body: active === null || active === document.body,
      inPanel: !!active && !!panel?.contains(active),
      connected: !!active?.isConnected,
      arrow: !!active?.matches('[data-testid^="arrow-scroll-"]'),
      moved: active !== (window as unknown as { __marked?: Element | null }).__marked,
    };
  });
}

/** Remembers where focus is now, for `moved`. */
function mark(page: Page) {
  return page.evaluate(() => {
    (window as unknown as { __marked: Element | null }).__marked = document.activeElement;
  });
}

/**
 * Presses a key (the focus is marked just before) and checks where focus is, three times over 800 ms: on a control in the
 * panel that is still on the page, not on the body, not on a scroll arrow, and (unless `stays`) not where it was.
 */
async function press(page: Page, key: string, what: string, { stays = false } = {}) {
  await mark(page);
  await page.keyboard.press(key);
  for (const wait of [0, 400, 400]) {
    await page.waitForTimeout(wait);
    const focus = await focusNow(page);
    expect(focus, `${what}: focus is on ${focus.id}`).toMatchObject({ body: false, inPanel: true, connected: true, arrow: false, moved: !stays });
  }
}

/** The scroll arrows Ketcher draws after `button` (in document order, so those of the toolbars behind it too), with their tab index. */
async function arrowsAfter(page: Page, button: string) {
  return page.evaluate((title) => {
    const last = [...document.querySelectorAll(`[title="${title}"]`)].find((el) => el.getClientRects().length > 0);
    if (!last) throw new Error(`no visible ${title} button`);
    const arrows = [...document.querySelectorAll<HTMLElement>('[data-testid^="arrow-scroll-"]')].filter(
      (el) => el.getClientRects().length > 0 && last.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING,
    );
    return arrows.map((el) => ({ id: el.dataset.testid, tabIndex: el.tabIndex }));
  }, button);
}

test('Tab pressed at once after the last button of a toolbar never lands on a scroll arrow that Ketcher then removes', async ({ page }) => {
  test.setTimeout(240_000);
  await openEditor(page);

  for (const last of ['Add Image', 'Extended Table']) {
    await visible(page, last).focus();
    // Ketcher has not scrolled its toolbar yet: the arrow after this button is still drawn.
    const arrows = await arrowsAfter(page, last);

    await press(page, 'Tab', `Tab after ${last}`);

    // Why it holds, and that the test is not passing because the arrows are gone: they are drawn, and none is a tab stop.
    expect(arrows.length, `Ketcher no longer draws a scroll arrow after ${last}: the workaround in labels.ts can go`).toBeGreaterThan(0);
    expect(arrows.filter((a) => a.tabIndex >= 0), `scroll arrows in the tab order after ${last}`).toEqual([]);
  }
});

test('after "Add Image" and "Extended Table" have been opened and closed by keyboard, Tab and Shift+Tab never land on a scroll arrow or on nothing, and always move', async ({ page }) => {
  test.setTimeout(240_000);
  await openEditor(page);

  // "Add Image" selects the image tool (its file dialog opens on a click in the canvas); Escape selects the select tool again.
  await visible(page, 'Add Image').focus();
  await press(page, 'Enter', 'Add Image opened', { stays: true });
  await press(page, 'Escape', 'Add Image closed', { stays: true });
  for (let i = 1; i <= 3; i++) await press(page, 'Tab', `Tab ${i} after Add Image`);
  for (let i = 1; i <= 3; i++) await press(page, 'Shift+Tab', `Shift+Tab ${i} after Add Image`);

  // "Extended Table" opens a dialog. Escape does not close it in 3.18.0 (a document-level handler of Ketcher's
  // stops the key before its dialog sees it), so the dialog's own close button does, reached by Tab.
  const dialog = page.locator('dialog[data-testid="info-modal-window"]');
  await visible(page, 'Extended Table').focus();
  await page.keyboard.press('Enter');
  await expect(dialog).toBeVisible();
  await expect(dialog).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(dialog.getByTestId('close-window-button')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(dialog).toHaveCount(0);
  // Ketcher gives focus back in a timer, a few ms after the dialog has gone (to its hidden clipboard field): until then it is on nothing.
  await expect.poll(async () => (await focusNow(page)).body).toBe(false);
  expect(await focusNow(page), 'Extended Table closed').toMatchObject({ body: false, inPanel: true, connected: true, arrow: false });

  for (let i = 1; i <= 3; i++) await press(page, 'Tab', `Tab ${i} after Extended Table`);
  for (let i = 1; i <= 6; i++) await press(page, 'Shift+Tab', `Shift+Tab ${i} after Extended Table`);
});

test('the check above can tell: with the page\'s tabindex write blocked, Tab after Add Image lands on a scroll arrow', async ({ page }) => {
  test.setTimeout(240_000);
  await page.addInitScript(() => {
    const set = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function (this: Element, name: string, value: string) {
      if (name === 'tabindex' && this.matches('[data-testid^="arrow-scroll-"]')) return;
      return set.call(this, name, value);
    };
  });
  await openEditor(page);

  await visible(page, 'Add Image').focus();
  await mark(page);
  await page.keyboard.press('Tab');
  expect((await focusNow(page)).arrow, 'focus after Tab from Add Image, with the arrows left in the tab order').toBe(true);
});

/** Where focus is, as a string: what a person would call the control, and the nth control of that name (some are drawn twice). */
async function stop(page: Page) {
  return page.evaluate(() => {
    const active = document.activeElement as HTMLElement | null;
    if (!active || active === document.body) return 'BODY';
    const name = active.getAttribute('data-testid') ?? active.getAttribute('title') ?? active.getAttribute('aria-label') ?? active.textContent?.slice(0, 20) ?? active.tagName;
    const same = [...document.querySelectorAll(active.tagName)].filter((el) => (el.getAttribute('data-testid') ?? el.getAttribute('title') ?? el.getAttribute('aria-label') ?? el.textContent?.slice(0, 20) ?? el.tagName) === name);
    return `${name}#${same.indexOf(active)}`;
  });
}

test('Shift+Tab walks backwards through the whole panel, the toolbars and the clipboard field, in exactly the reverse of the Tab order, wrapping at the ends', async ({ page }) => {
  test.setTimeout(240_000);
  await openEditor(page);
  await page.getByRole('heading', { name: 'Editing 4' }).focus();

  // One lap forwards: Tab until the first stop comes round again (the panel's focus trap wraps it).
  const lap: string[] = [];
  for (let i = 0; i < 200; i++) {
    await page.keyboard.press('Tab');
    const here = await stop(page);
    expect(here, `Tab ${i + 1}`).not.toBe('BODY');
    if (here === lap[0]) break;
    lap.push(here);
  }
  expect(lap.length, 'a lap of the panel: header, toolbars, clipboard field, facts and links').toBeGreaterThan(60);
  expect(lap.some((s) => s.startsWith('select-rectangle#'))).toBe(true);
  expect(lap.some((s) => s.startsWith('H-button#'))).toBe(true);
  expect(lap.some((s) => s.startsWith('TEXTAREA') || s.includes('Clipboard'))).toBe(true);
  await page.waitForTimeout(500);

  // And one lap back from the first stop: the last stop, then each one before it, down to the first, then the last again.
  const expected = [...lap].reverse();
  expected.push(expected[0]!);
  const back: string[] = [];
  for (let i = 0; i < expected.length; i++) {
    await page.keyboard.press('Shift+Tab');
    back.push(await stop(page));
  }
  expect(back).toEqual(expected);
});

test('Rectangle Selection is still reachable: by its toolbar button from the keyboard, and by Escape in the canvas; Shift+Tab no longer cycles the selection tools', async ({ page }) => {
  test.setTimeout(240_000);
  await openEditor(page);
  const selected = (testId: string) =>
    page.evaluate((id) => {
      const button = [...document.querySelectorAll(`[data-testid="${id}"]`)].find((el) => el.getClientRects().length > 0);
      return !!button && /_selected/.test(button.className);
    }, testId);
  const tool = (testId: string) => page.locator(`[data-testid="${testId}"]:visible`).first();

  // Hand tool, then Rectangle Selection from its button with the keyboard.
  await tool('hand').click();
  expect(await selected('hand')).toBe(true);
  expect(await selected('select-rectangle')).toBe(false);
  await tool('select-rectangle').focus();
  await page.keyboard.press('Enter');
  await expect.poll(() => selected('select-rectangle')).toBe(true);

  // Hand tool again, a click in the canvas gives Ketcher the keyboard (its clipboard field has focus), and Escape is Ketcher's.
  await tool('hand').click();
  expect(await selected('hand')).toBe(true);
  const box = await page.locator('.dc-editor__canvas[data-active=true]').boundingBox();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  await page.keyboard.press('Escape');
  await expect.poll(() => selected('select-rectangle')).toBe(true);

  // Shift+Tab from the canvas's keyboard field moves focus on, and leaves the tool alone.
  await tool('hand').click();
  await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
  expect(await page.evaluate(() => document.activeElement?.matches('textarea.cliparea'))).toBe(true);
  await press(page, 'Shift+Tab', 'Shift+Tab from the clipboard field');
  expect(await selected('hand')).toBe(true);
});

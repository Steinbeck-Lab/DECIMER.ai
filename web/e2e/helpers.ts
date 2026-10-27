import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { expect, type Locator, type Page } from '@playwright/test';

/** Collects console errors, page errors and CSP violations; a test ends with `expect(problems).toEqual([])`. */
export function watchProblems(page: Page): string[] {
  const problems: string[] = [];
  page.on('console', (m) => {
    if (m.type() === 'error') problems.push(`console: ${m.text()}`);
  });
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
  void page.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (e) =>
      console.error(`CSP violation: ${e.violatedDirective} ${e.blockedURI}`),
    );
  });
  return problems;
}

const FIXTURES = resolve(import.meta.dirname, '../src/mocks/fixtures');

/** A fixture crop as an upload payload. `name` picks the mock scenario (see MockApiClient.scenarioFor). */
export function fixtureImage(name = 'figure2.png', file = 'crop5.png') {
  return { name, mimeType: 'image/png', buffer: readFileSync(resolve(FIXTURES, file)) };
}

/** A PDF as an upload payload (the mock reads only its name; `name` picks the mock scenario like `fixtureImage`'s). */
export function fixturePdf(name = 'paper.pdf') {
  return { name, mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n') };
}

/** Number of horizontal pixels the page overflows by (0 means no sideways scroll). */
export function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

/** Pastes an image the way a browser does: a `paste` event with a File on the clipboard. */
export function pasteImage(page: Page, name = 'image.png', file = 'crop5.png') {
  const bytes = [...readFileSync(resolve(FIXTURES, file))];
  return page.evaluate(
    ({ bytes: data, fileName }) => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([new Uint8Array(data)], fileName, { type: 'image/png' }));
      const event = new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true });
      document.dispatchEvent(event);
      return event.defaultPrevented;
    },
    { bytes, fileName: name },
  );
}

export function pasteText(page: Page, text: string) {
  return page.evaluate((value) => {
    const transfer = new DataTransfer();
    transfer.setData('text/plain', value);
    const event = new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true });
    document.dispatchEvent(event);
    return event.defaultPrevented;
  }, text);
}

/** Selects a file of `bytes` zeros without sending it through Playwright (its payload limit is 50 MB). */
export function chooseLargeFile(page: Page, name: string, bytes: number) {
  return page.evaluate(
    ({ fileName, size }) => {
      const input = document.querySelector<HTMLInputElement>('input[type=file]');
      if (!input) throw new Error('no file input');
      const transfer = new DataTransfer();
      transfer.items.add(new File([new ArrayBuffer(size)], fileName, { type: 'image/png' }));
      input.files = transfer.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    },
    { fileName: name, size: bytes },
  );
}

/** The tile of the structure numbered `n` (its button's name starts with "Structure n"). */
export function tile(page: Page, n: number): Locator {
  return page.getByRole('button', { name: new RegExp(`^Structure ${n}\\b`) });
}

/** Below 900 px the editor is a modal sheet (role dialog); above it a column beside the figure (role complementary). */
export function editorPanel(page: Page): Locator {
  const width = page.viewportSize()?.width ?? 1280;
  return page.getByRole(width < 900 ? 'dialog' : 'complementary', { name: 'Structure editor' });
}

/** Atoms in a molfile, from its counts line (V2000 or V3000). */
export function atomCount(molfile: string): number | null {
  const v3000 = molfile.match(/^M {2}V30 COUNTS (\d+)/m);
  if (v3000?.[1]) return Number(v3000[1]);
  const v2000 = molfile.split('\n')[3]?.match(/^\s*(\d+)\s+\d+.*V2000/);
  return v2000?.[1] ? Number(v2000[1]) : null;
}

/** The Download button of the results screen (the one at the end of the filter row; there is no other). */
export function downloadButton(page: Page): Locator {
  return page.getByRole('main').getByRole('button', { name: /^Download/ });
}

/** Waits for Ketcher's toolbar, then for Indigo to have put the molecule on the canvas. */
export async function waitForEditor(page: Page, settleMs = 1500): Promise<void> {
  await page.waitForSelector('[data-testid=top-toolbar]', { state: 'attached', timeout: 90_000 });
  await page.waitForTimeout(settleMs);
}

/**
 * Waits until the canvas shows the structure the panel's heading names. The heading follows a press of Next or Previous
 * at once, but Ketcher swaps one structure per press, in order, so the canvas trails it; until it has caught up the
 * canvas is busy and cannot be edited. This is the condition to wait for, not a pause: how long it takes depends on
 * the machine.
 */
export async function waitForCanvas(page: Page, timeout = 60_000): Promise<void> {
  await expect(page.locator('.dc-editor__canvas[data-active=true][aria-busy=false]')).toBeVisible({ timeout });
}

/** Clicks the canvas that is showing, then selects all on it. */
async function selectAllOnCanvas(page: Page): Promise<void> {
  const box = await page.locator('.dc-editor__canvas[data-active=true]').boundingBox();
  if (!box) throw new Error('canvas is not showing');
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await page.keyboard.press('Control+a');
}

/** Edits the structure on the canvas the way a user does: click it, select all, Layout (Ctrl+L). */
export async function layoutOnCanvas(page: Page): Promise<void> {
  await selectAllOnCanvas(page);
  await page.keyboard.press('Control+l');
}

/** Empties the canvas: select all, Delete. */
export async function clearCanvas(page: Page): Promise<void> {
  await selectAllOnCanvas(page);
  await page.keyboard.press('Delete');
}

import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { Locator, Page } from '@playwright/test';

// Helpers shared by the parity runs: where the evidence goes, how a screenshot or an observation is
// recorded, and a few inputs that are built in memory (so that no fixture file has to be kept for them).

export type Side = 'legacy' | 'new' | 'webkit' | 'lighthouse';

export const EVIDENCE = resolve(import.meta.dirname, 'evidence');

/** The one small image used on both sides: a crop of structure 1 of the golden paper (also e2e/live/fixtures). */
export const CROP = resolve(import.meta.dirname, '../live/fixtures/00_crop-0.png');
/** golden/single-negatives: regions of the golden paper's pages. `n09_two_5_6.png` holds two structures side by side. */
export const NEGATIVES = process.env.PARITY_NEGATIVES ?? resolve(import.meta.dirname, '../../../golden/single-negatives');

export interface FileSpec {
  name: string;
  mimeType: string;
  bytes: number[];
}

export function fileFrom(path: string, name: string, mimeType = 'image/png'): FileSpec {
  return { name, mimeType, bytes: [...readFileSync(path)] };
}

export function dir(side: Side): string {
  const path = resolve(EVIDENCE, side);
  mkdirSync(path, { recursive: true });
  return path;
}

/** A screenshot into `evidence/<side>/<name>.png`; returns the path relative to web/e2e/parity (for the report). */
export async function shot(page: Page, side: Side, name: string, options: { fullPage?: boolean; locator?: Locator } = {}): Promise<string> {
  const file = resolve(dir(side), `${name}.png`);
  if (options.locator) await options.locator.screenshot({ path: file });
  else await page.screenshot({ path: file, fullPage: options.fullPage ?? false });
  return `evidence/${side}/${name}.png`;
}

/** Observations by parity item, written to `evidence/<side>/facts.json` when the run ends. */
export class Facts {
  private readonly data: Record<string, unknown> = {};
  constructor(
    private readonly side: Side,
    private readonly file = 'facts.json',
  ) {}

  set(key: string, value: unknown): void {
    this.data[key] = value;
    console.log(`FACT ${this.side} ${key}: ${JSON.stringify(value)}`);
  }

  /** Writes the file, keeping what an earlier (partial) run recorded under other keys: `-g` runs add to it. */
  flush(): void {
    const path = resolve(dir(this.side), this.file);
    let before: Record<string, unknown> = {};
    try {
      before = JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>;
    } catch {
      // no earlier run
    }
    writeFileSync(path, `${JSON.stringify({ ...before, ...this.data }, null, 2)}\n`);
  }
}

/** Dispatches the drag events of a drop of `files` onto the element at `selector` (as a browser does for a file dragged in). */
export async function dragEvents(
  page: Page,
  selector: string,
  files: FileSpec[],
  events: ('dragenter' | 'dragover' | 'dragleave' | 'drop')[],
): Promise<void> {
  await page.evaluate(
    ({ sel, specs, names }) => {
      const target = document.querySelector(sel);
      if (!target) throw new Error(`no element ${sel}`);
      const transfer = new DataTransfer();
      for (const spec of specs) transfer.items.add(new File([new Uint8Array(spec.bytes)], spec.name, { type: spec.mimeType }));
      for (const name of names) target.dispatchEvent(new DragEvent(name, { dataTransfer: transfer, bubbles: true, cancelable: true }));
    },
    { sel: selector, specs: files, names: events },
  );
}

/** A paste event carrying one image file, as a browser fires it for Ctrl+V of a copied image. */
export function pasteFile(page: Page, file: FileSpec): Promise<boolean> {
  return page.evaluate((spec) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([new Uint8Array(spec.bytes)], spec.name, { type: spec.mimeType }));
    const event = new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true });
    document.dispatchEvent(event);
    return event.defaultPrevented;
  }, file);
}

/** A PNG of black text on white, drawn in the page (so no fixture): an image with no chemical structure in it. */
export function textImage(page: Page, lines: string[], name = 'text-only.png'): Promise<FileSpec> {
  return page.evaluate(
    async ({ text, fileName }) => {
      const canvas = document.createElement('canvas');
      canvas.width = 520;
      canvas.height = 40 + text.length * 34;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('no 2d context');
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#000';
      ctx.font = '22px sans-serif';
      text.forEach((line, i) => ctx.fillText(line, 16, 36 + i * 34));
      const blob = await new Promise<Blob | null>((done) => canvas.toBlob(done, 'image/png'));
      if (!blob) throw new Error('no blob');
      return { name: fileName, mimeType: 'image/png', bytes: [...new Uint8Array(await blob.arrayBuffer())] };
    },
    { text: lines, fileName: name },
  );
}

/** A blank white PNG (no structure, no text). */
export function blankImage(page: Page, name: string, width = 300, height = 200): Promise<FileSpec> {
  return page.evaluate(
    async ({ w, h, fileName }) => {
      const canvas = document.createElement('canvas');
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('no 2d context');
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, w, h);
      const blob = await new Promise<Blob | null>((done) => canvas.toBlob(done, 'image/png'));
      if (!blob) throw new Error('no blob');
      return { name: fileName, mimeType: 'image/png', bytes: [...new Uint8Array(await blob.arrayBuffer())] };
    },
    { w: width, h: height, fileName: name },
  );
}

/** A valid PDF of `pages` blank A4 pages (hand-written, with a cross-reference table). */
export function blankPdf(pages: number): Buffer {
  const objects: string[] = [];
  const kids = Array.from({ length: pages }, (_, i) => `${3 + i} 0 R`).join(' ');
  objects.push('<< /Type /Catalog /Pages 2 0 R >>');
  objects.push(`<< /Type /Pages /Kids [${kids}] /Count ${pages} >>`);
  for (let i = 0; i < pages; i++) objects.push('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>');
  let out = '%PDF-1.4\n';
  const offsets: number[] = [];
  objects.forEach((body, i) => {
    offsets.push(out.length);
    out += `${i + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (const offset of offsets) out += `${String(offset).padStart(10, '0')} 00000 n \n`;
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, 'latin1');
}

/** Number of horizontal pixels the page overflows by (0 means no sideways scroll). */
export function overflowX(page: Page): Promise<number> {
  return page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
}

/** IDs that appear more than once in the document. */
export function duplicateIds(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const seen = new Map<string, number>();
    for (const el of document.querySelectorAll('[id]')) seen.set(el.id, (seen.get(el.id) ?? 0) + 1);
    return [...seen].filter(([, n]) => n > 1).map(([id, n]) => `${id} x${n}`);
  });
}

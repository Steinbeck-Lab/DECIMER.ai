/**
 * The release gate: `RELEASE=1 npm test` (scripts/check.sh --release, after the build). Skipped otherwise, so the
 * normal checks stay green while the copy still has questions in it.
 *
 * Every `[CONFIRM: ...]` in strings.ts is a statement nobody has confirmed (see the `pages` comment there). None may
 * go live: this fails while one is left in the copy, or in the built files of dist/.
 */
import { describe, expect, test } from 'vitest';
import { strings } from './strings';
import { allStrings } from './test/copy';

const env = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process?.env;
const release = env?.RELEASE === '1';

const MARKER = '[CONFIRM';
/** A marker as the copy writes it. The code that draws markers (pages/Rich.tsx) is in the build too, and must not match. */
const IN_BUILD = /\[CONFIRM: [A-Za-z]/;

describe.skipIf(!release)('release gate: no [CONFIRM] marker is left', () => {
  test('in the copy (strings.ts)', () => {
    const left = allStrings(strings).filter((text) => text.includes(MARKER));
    expect(left.map((text) => text.slice(text.indexOf(MARKER), text.indexOf(MARKER) + 90))).toEqual([]);
  });

  test('in the built files (dist/)', async () => {
    const files = import.meta.glob('../dist/**/*.{html,js,css,json,txt}', { query: '?raw', import: 'default' }) as Record<
      string,
      () => Promise<string>
    >;
    expect(Object.keys(files).length, 'dist/ is empty: build first (npm run build)').toBeGreaterThan(3);
    const left: string[] = [];
    for (const [file, load] of Object.entries(files)) {
      if (IN_BUILD.test(await load())) left.push(file);
    }
    expect(left).toEqual([]);
  });
});

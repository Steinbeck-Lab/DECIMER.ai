/**
 * The reader behind the dist/ half of the release gate (release.test.ts). It must list what is in a build, however
 * odd a file is: the gate is red while a marker is left, and a file that cannot be parsed must not turn that into a
 * crash that names no marker.
 */
import { describe, expect, test } from 'vitest';
import { readBuiltFiles } from './builtFiles';

type Fs = {
  mkdtempSync(prefix: string): string;
  mkdirSync(path: string, options: { recursive: true }): void;
  writeFileSync(path: string, data: string | Uint8Array): void;
  rmSync(path: string, options: { recursive: true; force: true }): void;
};
// Node's modules by a name the type checker does not resolve: src/ has no Node types (tsconfig.app.json), and the
// bundler must leave them alone.
const node = (name: string) => import(/* @vite-ignore */ name) as Promise<unknown>;

describe('readBuiltFiles', () => {
  test('reads every file, whatever it holds, and does not stop at one that is not what its name says', async () => {
    const fs = (await node('node:fs')) as Fs;
    const os = (await node('node:os')) as { tmpdir(): string };
    const root = fs.mkdtempSync(`${os.tmpdir()}/built-files-`);
    try {
      fs.mkdirSync(`${root}/assets/nested`, { recursive: true });
      fs.writeFileSync(`${root}/index.html`, '<p>[CONFIRM: left in the page]</p>');
      fs.writeFileSync(`${root}/assets/app.js`, 'console.log("fine");');
      fs.writeFileSync(`${root}/assets/nested/data.json`, '{"fine": true}');
      fs.writeFileSync(`${root}/assets/broken.json`, '{"cut off": '); // a JSON parser would throw
      fs.writeFileSync(`${root}/assets/gzipped.json`, new Uint8Array([0x1f, 0x8b, 0x08, 0x00, 0xff, 0xfe, 0x7a, 0x80])); // not text at all
      fs.writeFileSync(`${root}/assets/logo.png`, new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x00, 0xff]));
      fs.writeFileSync(`${root}/assets/empty.txt`, '');
      fs.writeFileSync(`${root}/assets/nested/marker.json`, '{"note": "[CONFIRM: in a data file]"}');

      const files = await readBuiltFiles(root);

      expect([...files.keys()].sort()).toEqual([
        'assets/app.js',
        'assets/broken.json',
        'assets/empty.txt',
        'assets/gzipped.json',
        'assets/logo.png',
        'assets/nested/data.json',
        'assets/nested/marker.json',
        'index.html',
      ]);
      expect(files.get('index.html')).toContain('[CONFIRM: left in the page]');
      expect(files.get('assets/nested/marker.json')).toContain('[CONFIRM: in a data file]');
      expect(files.get('assets/empty.txt')).toBe('');
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  test('a directory that is not there is an error that says so, not an empty build', async () => {
    await expect(readBuiltFiles('/no/such/dist')).rejects.toThrow(/dist/);
  });
});

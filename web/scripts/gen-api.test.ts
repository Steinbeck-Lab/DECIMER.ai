// @vitest-environment node
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';
import { expect, test } from 'vitest';

test('generated.ts is what openapi-typescript makes of the committed openapi.json (the full check, with the API, is `npm run check:api`)', () => {
  const root = resolve(import.meta.dirname, '..');
  const result = spawnSync(process.execPath, ['scripts/gen-api.mjs', '--check', '--types-only'], { cwd: root, encoding: 'utf8' });
  expect(result.stderr).toBe('');
  expect(result.status).toBe(0);
});

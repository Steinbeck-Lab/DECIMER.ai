#!/usr/bin/env node
// TypeScript types from the job API's OpenAPI schema. Python is the source of truth.
//
//   npm run gen:api     rewrites src/api/openapi.json and src/api/generated.ts
//   npm run check:api   fails when either is stale (CI): it regenerates both and compares
//   node scripts/gen-api.mjs --check --types-only   skips Python: checks generated.ts against the committed schema
//
// The schema comes from the API's own code (scripts/export-openapi.py, run with `uv run --project ../api`), so
// it needs uv and the API's environment (`uv sync` in ../api once). Set UV to use a uv that is not on PATH.
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const SCHEMA = join(root, 'src/api/openapi.json');
const TYPES = join(root, 'src/api/generated.ts');
const args = new Set(process.argv.slice(2));
const check = args.has('--check');
const typesOnly = args.has('--types-only');

function run(command, argv, options = {}) {
  const result = spawnSync(command, argv, { cwd: root, encoding: 'utf8', ...options });
  if (result.error) throw result.error;
  return result;
}

function findUv() {
  for (const candidate of [process.env.UV, 'uv', join(homedir(), '.local/bin/uv')].filter(Boolean)) {
    if (run(candidate, ['--version']).status === 0) return candidate;
  }
  throw new Error('uv was not found. Install it (https://docs.astral.sh/uv/) or set UV to its path.');
}

function exportSchemaFromApi() {
  const result = run(findUv(), ['run', '--project', '../api', '--no-sync', 'python', 'scripts/export-openapi.py']);
  if (result.status !== 0) {
    throw new Error(`The schema export failed (run "uv sync" in ../api first?):\n${result.stderr}`);
  }
  JSON.parse(result.stdout); // fail here, not later, if it is not JSON
  return result.stdout;
}

function typesFor(schemaPath, outPath) {
  const bin = join(root, 'node_modules/.bin/openapi-typescript');
  const result = run(bin, [schemaPath, '-o', outPath]);
  if (result.status !== 0) throw new Error(`openapi-typescript failed:\n${result.stdout}${result.stderr}`);
  return readFileSync(outPath, 'utf8');
}

const read = (path) => (existsSync(path) ? readFileSync(path, 'utf8') : null);
const scratch = mkdtempSync(join(tmpdir(), 'decimer-api-'));
try {
  const committedSchema = read(SCHEMA);
  const schema = typesOnly ? committedSchema : exportSchemaFromApi();
  if (schema === null) throw new Error('src/api/openapi.json does not exist: run "npm run gen:api" first.');

  const scratchSchema = join(scratch, 'openapi.json');
  writeFileSync(scratchSchema, schema);
  const types = typesFor(scratchSchema, join(scratch, 'generated.ts'));

  if (check) {
    const stale = [];
    if (!typesOnly && committedSchema !== schema) stale.push('src/api/openapi.json (the API changed)');
    if (read(TYPES) !== types) stale.push('src/api/generated.ts');
    if (stale.length > 0) {
      console.error(`Stale: ${stale.join(', ')}.\nRun "npm run gen:api" and commit the result.`);
      process.exitCode = 1;
    } else {
      console.log('src/api/openapi.json and src/api/generated.ts are up to date with the API.');
    }
  } else {
    writeFileSync(SCHEMA, schema);
    writeFileSync(TYPES, types);
    console.log('Wrote src/api/openapi.json and src/api/generated.ts.');
  }
} finally {
  rmSync(scratch, { recursive: true, force: true });
}

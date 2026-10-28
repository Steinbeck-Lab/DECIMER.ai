import { resolve } from 'node:path';
import { defineConfig, devices, type PlaywrightTestConfig } from '@playwright/test';

// The parity check: the behaviour of the earlier Laravel version of decimer.ai compared with the new stack, with a
// screenshot and a recorded observation for each item. Opt-in, like e2e/live/: the files are named `*.parity.ts`, so the
// default `npx playwright test` (which collects `*.spec.ts`) never sees them, and CI never runs them. Nothing here
// starts a server.
//
//   PARITY_NEW_URL     the new stack (default http://127.0.0.1:8090): projects `new`, `webkit` and `lighthouse`
//   PARITY_LEGACY_URL  the earlier version (no default, so that a run never touches production by accident):
//                      project `legacy`; use its https URL, not an http port (Laravel builds https URLs)
//
// The first-visit animation plays once per browser tab (its flag is in session storage, which a storageState cannot
// hold): the specs mark their own tabs as returning (e2e/returning.ts), so it does not play in every test.
//
// Run one project at a time, from web/, in the Playwright image (golden/ mounted for the PDF the runs upload):
//   docker run --rm --network host --user $(id -u):$(id -g) -e HOME=/tmp -e PARITY_NEW_URL=http://127.0.0.1:8090 \
//     -v "$PWD":/work -v "$PWD/../golden":/golden:ro -w /work mcr.microsoft.com/playwright:v1.64.1-noble \
//     npx playwright test --config e2e/parity/parity.config.ts --project new
const newUrl = new URL(process.env.PARITY_NEW_URL ?? 'http://127.0.0.1:8090');
const legacy = process.env.PARITY_LEGACY_URL ? new URL(process.env.PARITY_LEGACY_URL) : null;
const out = resolve(import.meta.dirname, '../../test-results/parity');

const projects: NonNullable<PlaywrightTestConfig['projects']> = [
  {
    name: 'new',
    testMatch: 'new.parity.ts',
    use: { ...devices['Desktop Chrome'], baseURL: `${newUrl.origin}/` },
  },
  {
    // Playwright's WebKit is the Safari engine: the earlier version's Safari warning is re-tested in it.
    name: 'webkit',
    testMatch: 'webkit.parity.ts',
    use: { ...devices['Desktop Safari'], baseURL: `${newUrl.origin}/` },
  },
  {
    name: 'lighthouse',
    testMatch: 'lighthouse.parity.ts',
    use: { baseURL: `${newUrl.origin}/` },
  },
];
if (legacy) {
  projects.unshift({
    name: 'legacy',
    testMatch: 'legacy.parity.ts',
    use: { ...devices['Desktop Chrome'], baseURL: `${legacy.origin}/` },
  });
}

export default defineConfig({
  testDir: '.',
  outputDir: out,
  // One job at a time: the stack is shared and small, and the earlier version is a production site.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 600_000,
  expect: { timeout: 15_000 },
  use: { actionTimeout: 30_000, navigationTimeout: 60_000 },
  reporter: [['list'], ['json', { outputFile: resolve(out, 'report.json') }]],
  projects,
});

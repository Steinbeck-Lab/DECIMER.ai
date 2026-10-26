import { resolve } from 'node:path';
import { defineConfig, devices, type PlaywrightTestConfig } from '@playwright/test';

// The parity sign-off: ROADMAP §6 checked against the live legacy site and the new stack, with a
// screenshot and a recorded observation for each. Opt-in, like e2e/live/: the files are named `*.parity.ts`, so the
// default `npx playwright test` (which collects `*.spec.ts`) never sees them, and CI never runs them. Nothing here
// starts a server. See e2e/parity/REPORT.md for what it found and for how to run it.
//
//   PARITY_NEW_URL     the new stack (default http://127.0.0.1:8090): projects `new`, `webkit` and `lighthouse`
//   PARITY_LEGACY_URL  the legacy site (no default, so that a run never touches production by accident):
//                      project `legacy`; https://decimer.ai, not :8080 (Laravel builds https URLs)
//
// The first-visit animation plays once per browser tab (its flag is in session storage, which a storageState cannot
// hold): the specs mark their own tabs as returning (e2e/returning.ts), so it does not play in every test.
//
// Run one project at a time, from web/, in the Playwright image (see REPORT.md for the full command):
//   npx playwright test --config e2e/parity/parity.config.ts --project new
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
    // Playwright's WebKit is the Safari engine: ROADMAP §6 item 17 (the Safari warning) is re-tested in it.
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
  // One job at a time: the stack is shared and small, and the legacy site is production.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 600_000,
  expect: { timeout: 15_000 },
  use: { actionTimeout: 30_000, navigationTimeout: 60_000 },
  reporter: [['list'], ['json', { outputFile: resolve(out, 'report.json') }]],
  projects,
});

import { resolve } from 'node:path';
import { defineConfig, devices } from '@playwright/test';

// The live run: one spec against a stack that is already running (its edge proxy, e.g. http://127.0.0.1:8090),
// the production build under the real nginx CSP, the real API and workers. Opt-in: nothing here starts a server, and
// without LIVE_BASE_URL the config refuses to load, so the default `npx playwright test` (playwright.config.ts) and
// CI never touch a stack. Run it with `npm run e2e:live` (see e2e/live/README.md).
const base = process.env.LIVE_BASE_URL;
if (!base) {
  throw new Error('LIVE_BASE_URL is not set: the live spec runs against a running stack, e.g. LIVE_BASE_URL=http://127.0.0.1:8090');
}
const origin = new URL(base).origin;
const out = resolve(import.meta.dirname, '../../test-results/live');

export default defineConfig({
  testDir: '.',
  testMatch: 'live.spec.ts',
  outputDir: out,
  // One job at a time: the stack is shared and small.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 300_000,
  reporter: [['list'], ['json', { outputFile: resolve(out, 'report.json') }]],
  use: {
    ...devices['Desktop Chrome'],
    baseURL: `${origin}/`,
    // The first-visit animation (once per tab) is stopped by the spec itself: markReturningTab in live.spec.ts.
  },
  projects: [{ name: 'live' }],
});

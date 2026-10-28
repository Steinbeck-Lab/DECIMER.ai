import { defineConfig, devices } from '@playwright/test';

const PORT = 4173;
/** A second build of the same app whose edit debounce is long (VITE_EDIT_DEBOUNCE_MS), for the test that exports inside it. */
export const SLOW_DEBOUNCE_PORT = 4174;
/** A third build, with VITE_API=http: the app against the job API's real endpoints (answered by the tests). */
export const HTTP_API_PORT = 4175;
const origin = (port: number) => `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e',
  // e2e/live/ runs against a stack that is already up, with its own config (e2e/live/live.config.ts, `npm run e2e:live`).
  testIgnore: '**/live/**',
  timeout: 120_000,
  // GitHub's runners are slower than the VM: Ketcher-heavy tests flake there (2026-10-28). Retries only in CI.
  retries: process.env.CI ? 2 : 0,
  reporter: [['list']],
  use: {
    baseURL: `${origin(PORT)}/`,
    trace: 'retain-on-failure',
    // The 1.9 MB first-visit animation plays once per browser tab. The flag that stops it is in session storage, which
    // `storageState` cannot hold: the specs import `test` from ./e2e/returning.ts, which sets it in every context before
    // the page runs. The tests of the animation (intro.spec.ts) use the plain `test` and start from an empty tab.
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      // Built app under the production CSP, not the dev server.
      command: `npm run build && npx vite preview --port ${PORT} --strictPort --host 127.0.0.1`,
      url: `${origin(PORT)}/`,
      timeout: 300_000,
      reuseExistingServer: false,
    },
    {
      command:
        `VITE_EDIT_DEBOUNCE_MS=4000 npx vite build --outDir node_modules/.e2e-slow-debounce --emptyOutDir && ` +
        `npx vite preview --outDir node_modules/.e2e-slow-debounce --port ${SLOW_DEBOUNCE_PORT} --strictPort --host 127.0.0.1`,
      url: `${origin(SLOW_DEBOUNCE_PORT)}/`,
      timeout: 300_000,
      reuseExistingServer: false,
    },
    {
      command:
        `VITE_API=http npx vite build --outDir node_modules/.e2e-http-api --emptyOutDir && ` +
        `npx vite preview --outDir node_modules/.e2e-http-api --port ${HTTP_API_PORT} --strictPort --host 127.0.0.1`,
      url: `${origin(HTTP_API_PORT)}/`,
      timeout: 300_000,
      reuseExistingServer: false,
    },
  ],
});

import { test as base, type BrowserContext } from '@playwright/test';

/** Makes every page of a context a tab that has shown the animation already (a context made by hand, in a `beforeAll`). */
export async function markReturningTab(context: BrowserContext): Promise<void> {
  await context.addInitScript(() => {
    try {
      window.sessionStorage.setItem('decimer.visited', '1');
    } catch {
      // storage blocked: the page then plays no animation either
    }
  });
}

/**
 * The `test` of every spec that is not about the first-visit logo animation. The animation (1.9 MB, about 8 s) plays
 * when the site is opened in a new browser tab, and the flag that stops it is in the tab's session storage, which a
 * Playwright `storageState` cannot hold. So each context starts, like a tab that has already shown it, with the flag
 * set before the page's own scripts run. The animation's own tests (intro.spec.ts, the first-visit screenshots, the
 * parity run's first visit) say `test.use({ returningTab: false })` or use the plain `test` of `@playwright/test`.
 */
export const test = base.extend<{ returningTab: boolean }>({
  returningTab: [true, { option: true }],
  context: async ({ context, returningTab }, provide) => {
    if (returningTab) await markReturningTab(context);
    await provide(context);
  },
});

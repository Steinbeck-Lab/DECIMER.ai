import { MantineProvider } from '@mantine/core';
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test } from 'vitest';
import { App } from '../App';
import { MockApiClient } from '../mocks/MockApiClient';
import { AppProvider } from '../state/react';
import { cssVariablesResolver, theme } from '../theme';
import { MATOMO_SITE_ID, MATOMO_URL, resetMatomoForTests, startMatomo, trackRoute, trackingEnabled } from './matomo';

type Paq = (string | number)[][];
const paq = () => (window as unknown as { _paq?: Paq })._paq;
const pageViews = () => (paq() ?? []).filter((call) => call[0] === 'trackPageView').length;
const matomoScripts = () => document.querySelectorAll('script[src="https://matomo.nfdi4chem.de/matomo.js"]');

beforeEach(() => {
  window.scrollTo = () => {}; // the app scrolls to the top on a new page; jsdom has no layout
  resetMatomoForTests();
  delete (window as unknown as { _paq?: Paq })._paq;
  document.head.querySelectorAll('script').forEach((script) => script.remove());
  window.location.hash = '';
});
afterEach(() => {
  cleanup();
  window.location.hash = '';
});

describe('where the counter runs', () => {
  test('on a real host of a production build, and nowhere else', () => {
    expect(trackingEnabled('decimer.ai', true)).toBe(true);
    expect(trackingEnabled('www.decimer.ai', true)).toBe(true);
    for (const host of ['beta.decimer.ai', 'decimer.example.org', 'decimer.ai.example.org']) {
      expect(trackingEnabled(host, true), host).toBe(false); // a self-hosted copy counts nothing
    }
    expect(trackingEnabled('decimer.ai', false)).toBe(false); // the dev server
    for (const host of ['localhost', '127.0.0.1', '::1', '[::1]', 'app.localhost']) {
      expect(trackingEnabled(host, true), host).toBe(false); // a trial run on this machine, a test, Lighthouse
    }
  });

  test('a disabled counter loads nothing and leaves no queue behind', () => {
    expect(startMatomo(window, document, false)).toBe(false);
    expect(matomoScripts()).toHaveLength(0);
    expect(paq()).toBeUndefined();
  });

  test('the test environment itself is not a real host: starting with the defaults does nothing', () => {
    expect(startMatomo()).toBe(false);
    expect(matomoScripts()).toHaveLength(0);
  });
});

describe('the earlier version\'s code', () => {
  test('is the same: tracker address, site id and calls in the earlier order, and no disableCookies', () => {
    // resources/views/layouts/default.blade.php of the earlier version: u = "//matomo.nfdi4chem.de/", setSiteId 2,
    // trackPageView, enableLinkTracking, setTrackerUrl (u + 'matomo.php'), setSiteId, then matomo.js (async).
    expect(startMatomo(window, document, true)).toBe(true);
    expect(paq()).toEqual([
      ['trackPageView'],
      ['enableLinkTracking'],
      ['setTrackerUrl', 'https://matomo.nfdi4chem.de/matomo.php'],
      ['setSiteId', '2'],
    ]);
    expect(MATOMO_URL).toBe('https://matomo.nfdi4chem.de/');
    expect(MATOMO_SITE_ID).toBe('2');
    const scripts = matomoScripts();
    expect(scripts).toHaveLength(1);
    expect((scripts[0] as HTMLScriptElement).async).toBe(true);
    expect(JSON.stringify(paq())).not.toMatch(/disableCookies|requireCookieConsent|setDoNotTrack/);
  });

  test('is loaded once, however often it is started', () => {
    expect(startMatomo(window, document, true)).toBe(true);
    expect(startMatomo(window, document, true)).toBe(false);
    expect(startMatomo(window, document, true)).toBe(false);
    expect(matomoScripts()).toHaveLength(1);
    expect(paq()).toHaveLength(4);
    expect(pageViews()).toBe(1);
  });

  test('keeps a queue that something else has started', () => {
    (window as unknown as { _paq: Paq })._paq = [['setCustomDimension', 1, 'x']];
    startMatomo(window, document, true);
    expect(paq()?.[0]).toEqual(['setCustomDimension', 1, 'x']);
    expect(paq()).toHaveLength(5);
  });
});

describe('route changes', () => {
  test('count one page view for each move to another page, with its address and title', () => {
    startMatomo(window, document, true);
    expect(pageViews()).toBe(1); // the page the visit began on
    trackRoute('/'); // the app noting where it began: counted already
    expect(pageViews()).toBe(1);

    window.location.hash = '#/about';
    document.title = 'About · DECIMER.ai';
    trackRoute('/about');
    expect(pageViews()).toBe(2);
    expect(paq()!.slice(-3)).toEqual([
      ['setCustomUrl', window.location.href],
      ['setDocumentTitle', 'About · DECIMER.ai'],
      ['trackPageView'],
    ]);
    expect(window.location.href).toMatch(/#\/about$/);

    trackRoute('/about'); // the same page again: nothing
    expect(pageViews()).toBe(2);
    trackRoute('/privacy');
    expect(pageViews()).toBe(3);
  });

  test('count nothing while the counter is off', () => {
    trackRoute('/');
    trackRoute('/about');
    expect(paq()).toBeUndefined();
  });

  test('follow the hash of the real app', async () => {
    startMatomo(window, document, true);
    render(
      <MantineProvider theme={theme} cssVariablesResolver={cssVariablesResolver} forceColorScheme="light">
        <AppProvider api={new MockApiClient({ latencyMs: 0 })}>
          <App />
        </AppProvider>
      </MantineProvider>,
    );
    await act(async () => {});
    expect(pageViews()).toBe(1); // opening the app adds nothing to the page the code counted
    for (const [hash, views] of [
      ['#/about', 2],
      ['#/privacy', 3],
      ['#/impressum', 4],
      ['#/', 5],
    ] as const) {
      await act(async () => {
        window.location.hash = hash;
      });
      expect(pageViews(), hash).toBe(views);
      expect(paq()!.filter((call) => call[0] === 'setCustomUrl').at(-1)?.[1]).toMatch(new RegExp(`${hash}$`));
    }
  });
});

import { MantineProvider } from '@mantine/core';
import { act, cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { App } from './App';
import { MockApiClient } from './mocks/MockApiClient';
import { AppProvider } from './state/react';
import { strings } from './strings';
import { cssVariablesResolver, theme } from './theme';

afterEach(() => {
  cleanup();
  window.location.hash = '';
});

function renderApp() {
  return render(
    <MantineProvider theme={theme} cssVariablesResolver={cssVariablesResolver} forceColorScheme="light">
      <AppProvider api={new MockApiClient({ latencyMs: 0 })}>
        <App />
      </AppProvider>
    </MantineProvider>,
  );
}

test('the first screen is one target: the wordmark, the tagline and a way to choose files', () => {
  renderApp();
  expect(screen.getByRole('heading', { level: 1, name: strings.appName })).toBeTruthy();
  expect(screen.getByText(strings.tagline)).toBeTruthy();
  const choose = screen.getByRole('button', { name: strings.upload.browse });
  expect(choose).toBeTruthy();
  // Nothing else asks for a decision before the first result.
  expect(screen.queryByRole('button', { name: strings.shell.newUpload })).toBeNull();
});

const footerRow = () => {
  const footer = screen.getByRole('contentinfo');
  return {
    footer,
    row: footer.querySelector<HTMLElement>('.dc-footer__line')!,
    legal: footer.querySelector<HTMLElement>('.dc-footer__legal')!,
    made: footer.querySelector<HTMLElement>('.dc-footer__made')!,
    nav: within(footer).getByRole('navigation', { name: strings.shell.footerNav }),
  };
};
const follows = (a: Element, b: Element) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

test('the footer is one row: the copyright on the left, the coffee credit in the middle, the links on the right', () => {
  renderApp();
  const { row, legal, made, nav, footer } = footerRow();
  expect([legal.parentElement, made.parentElement, nav.parentElement]).toEqual([row, row, row]);
  expect(follows(legal, made) && follows(made, nav)).toBe(true);
  expect(legal.textContent).toBe(strings.shell.copyright(new Date().getFullYear()));
  // Nothing of the old sentence is left.
  expect(footer.textContent).not.toMatch(/Developed by|open an issue|Contact us/);
});

test('the footer links are Contact, Privacy and Impressum, and not About or GitHub, which are in the header', () => {
  renderApp();
  const { footer, nav } = footerRow();
  const links = within(nav).getAllByRole('link');
  expect(links.map((a) => a.textContent)).toEqual([strings.shell.contact, strings.shell.privacy, strings.shell.impressum]);
  expect(links[0]?.getAttribute('href')).toBe(strings.shell.contactUrl);
  expect(within(footer).queryByRole('link', { name: strings.shell.about })).toBeNull();
  expect(within(footer).queryByRole('link', { name: /GitHub/ })).toBeNull();
});

test('the credit in the middle: built with a cup by the author at the Steinbeck Lab, Friedrich Schiller University Jena, each name a link', () => {
  renderApp();
  const { made, footer } = footerRow();
  const c = strings.shell.credit;
  // As read (the cup says "coffee" to a screen reader, each link says it opens in a new tab):
  const spoken = made.textContent!.replaceAll(` ${strings.editor.external}`, '');
  expect(spoken).toBe(
    `${strings.shell.builtWith} ${strings.shell.builtWithIcon} ${strings.shell.builtBy} ${strings.shell.builtByName} ${c.at} ${c.group}, ${c.university}`,
  );
  // The three names leave the site: a new tab, the referrer kept to itself, announced as such to a screen reader.
  const links = within(made).getAllByRole('link') as HTMLAnchorElement[];
  expect(links.map((a) => a.getAttribute('href'))).toEqual(['https://kohulanr.com', c.groupUrl, c.universityUrl]);
  expect(links.map((a) => a.textContent?.replace(` ${strings.editor.external}`, ''))).toEqual([strings.shell.builtByName, c.group, c.university]);
  for (const a of links) {
    expect(a.target, a.href).toBe('_blank');
    expect(a.rel, a.href).toContain('noreferrer');
    expect(a.textContent, a.href).toContain(strings.editor.external);
  }
  // The word "coffee" is there for a screen reader, beside the cup that only decorates.
  expect(made.querySelector(':scope > .dc-sr')?.textContent).toBe(strings.shell.builtWithIcon);
  expect(made.querySelector('.dc-cup')?.getAttribute('aria-hidden')).toBe('true');
  expect(made.querySelector('svg')).toBeTruthy();
  // The footer has its measuring line on top and nothing else drawn: no inspection frame, handles or guides.
  expect(footer.querySelector('.dc-frame')).toBeNull();
  expect(footer.querySelectorAll('.dc-measure')).toHaveLength(1);
});

test('the footer is the compact one on the empty screen only', async () => {
  renderApp();
  expect(screen.getByRole('contentinfo').getAttribute('data-compact')).toBe('true');
  cleanup();
  window.location.hash = '#/privacy';
  renderApp();
  await act(async () => {});
  expect(screen.getByRole('contentinfo').getAttribute('data-compact')).toBe('false');
});

test("the footer carries the earlier version's copyright line, with the current year", () => {
  vi.useFakeTimers({ toFake: ['Date'] });
  try {
    vi.setSystemTime(new Date('2031-03-04T12:00:00Z'));
    renderApp();
    expect(screen.getByRole('contentinfo').textContent).toContain('© 2031 DECIMER Project. All rights reserved.');
  } finally {
    vi.useRealTimers();
  }
});

test('the About route renders the page, and says nothing about STOUT or Google Analytics', async () => {
  window.location.hash = '#/about';
  renderApp();
  await act(async () => {});
  expect(screen.getByRole('heading', { level: 1, name: strings.pages.about.title })).toBeTruthy();
  const text = document.body.textContent ?? '';
  expect(text).not.toMatch(/STOUT/i);
  expect(text).not.toMatch(/Google Analytics/i);
});

test('the Privacy route renders its page', async () => {
  window.location.hash = '#/privacy';
  renderApp();
  await act(async () => {});
  expect(screen.getByRole('heading', { level: 1, name: strings.pages.privacy.title })).toBeTruthy();
});

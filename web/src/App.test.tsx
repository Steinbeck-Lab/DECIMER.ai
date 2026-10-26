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

test('the footer carries the lab credit, the GitHub link and the coffee credit', () => {
  renderApp();
  expect(screen.getByRole('link', { name: strings.shell.credit.group })).toBeTruthy();
  expect(screen.getByRole('link', { name: strings.shell.credit.university })).toBeTruthy();
  const footer = screen.getByRole('contentinfo');
  expect(footer.textContent).toContain(`${strings.shell.builtWith}`);
  expect(footer.textContent).toContain(strings.shell.builtBy);
  expect(footer.querySelector('svg')).toBeTruthy();
});

test('the footer links Privacy, Impressum and GitHub, and not About, which is in the header', () => {
  renderApp();
  const footer = screen.getByRole('contentinfo');
  const nav = within(footer).getByRole('navigation', { name: strings.shell.footerNav });
  expect(within(nav).getAllByRole('link').map((a) => a.textContent)).toEqual([
    strings.shell.privacy,
    strings.shell.impressum,
    strings.shell.github,
  ]);
  expect(within(footer).queryByRole('link', { name: strings.shell.about })).toBeNull();
  // The credit and the legal pages share one line of the footer.
  const line = nav.parentElement!;
  expect(line.textContent).toContain(strings.shell.credit.before.trim());
  expect(within(line).getByRole('link', { name: strings.shell.contact })).toBeTruthy();
  expect(within(line).getByRole('link', { name: strings.shell.reportIssue })).toBeTruthy();
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

test("the footer carries legacy's copyright line, with the current year", () => {
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

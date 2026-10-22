import { MantineProvider } from '@mantine/core';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
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

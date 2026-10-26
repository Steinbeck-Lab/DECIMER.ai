import { MantineProvider } from '@mantine/core';
import { act, cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import { App } from '../App';
import { MockApiClient } from '../mocks/MockApiClient';
import { AppProvider } from '../state/react';
import { strings } from '../strings';
import { cssVariablesResolver, theme } from '../theme';

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

test('the empty screen hangs About and GitHub from the top edge as two ribbon links with their own names', () => {
  renderApp();
  const header = screen.getByRole('banner');
  const nav = within(header).getByRole('navigation', { name: strings.shell.mainNav });
  const about = within(nav).getByRole('link', { name: strings.shell.about });
  const github = within(nav).getByRole('link', { name: strings.shell.github });
  expect(about.getAttribute('href')).toBe('#/about');
  expect(github.getAttribute('href')).toBe(strings.shell.githubUrl);
  expect(github.getAttribute('target')).toBe('_blank');
  expect(github.getAttribute('rel')).toContain('noreferrer');
  // The cloth and the word printed on it are decoration: the link's name is its aria-label.
  for (const link of [about, github]) {
    expect(link.querySelector('canvas')?.getAttribute('aria-hidden')).toBe('true');
    expect(link.querySelector('.dc-ribbon__label')?.getAttribute('aria-hidden')).toBe('true');
  }
  // The wordmark is the big logo of the page: the header does not repeat it.
  expect(within(header).queryByRole('link', { name: strings.shell.home })).toBeNull();
});

test('every other page has the wordmark and plain links in the header, not ribbons', async () => {
  window.location.hash = '#/about';
  renderApp();
  await act(async () => {});
  // (The page's own title block is a <header> too, so the site header is found by its class.)
  const header = document.querySelector<HTMLElement>('.dc-header');
  if (!header) throw new Error('no site header');
  expect(within(header).getByRole('link', { name: strings.shell.home })).toBeTruthy();
  const about = within(header).getByRole('link', { name: strings.shell.about });
  expect(about.getAttribute('aria-current')).toBe('page');
  expect(header.querySelector('.dc-ribbon')).toBeNull();
});

test('the drop pill names its action, the paste shortcut and, for assistive technology, the formats and limits', () => {
  renderApp();
  expect(screen.getByText(strings.upload.title)).toBeTruthy();
  const choose = screen.getByRole('button', { name: strings.upload.browse });
  const facts = document.getElementById(choose.getAttribute('aria-describedby') ?? '');
  expect(facts?.textContent).toContain(strings.upload.formats);
  expect(facts?.textContent).toContain(strings.upload.limits);
  // jsdom is not a Mac: the pill says Ctrl+V.
  expect(screen.getByText(new RegExp(`${strings.upload.pasteLead}\\s+${strings.upload.pasteKeyOther.replace('+', '\\+')}`))).toBeTruthy();
  // The one quiet line, from the limits in the contract: never a claim that nothing is tracked.
  const quiet = screen.getByText(strings.upload.quietLine);
  expect(quiet.textContent).not.toMatch(/track/i);
  // The frame is decoration.
  expect(document.querySelector('.dc-frame')?.getAttribute('aria-hidden')).toBe('true');
});

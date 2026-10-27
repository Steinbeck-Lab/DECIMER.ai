import { MantineProvider } from '@mantine/core';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
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

test('a ribbon is still a plain link to a click: the browser does not drag it, and a press that does not pull follows it', () => {
  renderApp();
  const about = within(screen.getByRole('navigation', { name: strings.shell.mainNav })).getByRole('link', { name: strings.shell.about });
  expect(about.getAttribute('draggable')).toBe('false');
  fireEvent.pointerDown(about, { button: 0, pointerId: 1, pointerType: 'mouse', clientX: 10, clientY: 40 });
  fireEvent.pointerMove(about, { pointerId: 1, pointerType: 'mouse', clientX: 11, clientY: 41 });
  fireEvent.pointerUp(about, { pointerId: 1, pointerType: 'mouse', clientX: 11, clientY: 41 });
  const click = new MouseEvent('click', { bubbles: true, cancelable: true });
  about.dispatchEvent(click);
  expect(click.defaultPrevented).toBe(false);
});

/**
 * Gives jsdom just enough of a canvas for the ribbons to build their cloth (every drawing call does nothing) and
 * pointer capture that remembers who holds it, for as long as the test runs.
 */
function withFakeCanvas() {
  const noop = () => {};
  const context = new Proxy({}, { get: (_, key) => (key === 'canvas' ? undefined : noop), set: () => true });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation((() => context) as never);
  vi.stubGlobal('Path2D', class { moveTo() {} lineTo() {} closePath() {} });
  const captured = new WeakMap<Element, Set<number>>();
  const held = (el: Element) => captured.get(el) ?? captured.set(el, new Set()).get(el)!;
  Element.prototype.setPointerCapture = function (id: number) {
    held(this).add(id);
  };
  Element.prototype.releasePointerCapture = function (id: number) {
    held(this).delete(id);
  };
  Element.prototype.hasPointerCapture = function (id: number) {
    return held(this).has(id);
  };
  return () => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    for (const key of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) delete (Element.prototype as Partial<Element>)[key];
  };
}

test('a press that leaves the ribbon and is let go elsewhere is over: hovering back does not grab the cloth, a click still follows the link', () => {
  const restore = withFakeCanvas();
  try {
    renderApp();
    const about = within(screen.getByRole('navigation', { name: strings.shell.mainNav })).getByRole('link', { name: strings.shell.about });
    const mouse = { pointerId: 1, pointerType: 'mouse' };
    // Down near the edge, out of the ribbon before the pull starts, and released somewhere the link does not hear of.
    fireEvent.pointerDown(about, { ...mouse, button: 0, buttons: 1, clientX: 2, clientY: 60 });
    fireEvent.pointerLeave(about, { ...mouse, buttons: 1, clientX: -3, clientY: 60 });
    fireEvent.pointerUp(document.body, { ...mouse, button: 0, buttons: 0, clientX: -40, clientY: 60 });
    about.releasePointerCapture(1); // what the browser does on any pointerup
    // The pointer comes back with no button held, far enough to have been a pull.
    fireEvent.pointerEnter(about, { ...mouse, buttons: 0, clientX: 20, clientY: 80 });
    fireEvent.pointerMove(about, { ...mouse, buttons: 0, clientX: 30, clientY: 90 });
    expect(about.dataset.pulling).toBeUndefined();
    expect(about.hasPointerCapture(1)).toBe(false);
    // A click elsewhere does not end in the link, and a click on it follows it.
    fireEvent.pointerDown(document.body, { ...mouse, button: 0, buttons: 1 });
    fireEvent.pointerUp(document.body, { ...mouse, button: 0, buttons: 0 });
    expect(about.hasPointerCapture(1)).toBe(false);
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    about.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(false);
  } finally {
    restore();
  }
});

test('a real pull of the ribbon grabs the cloth, and the click that ends it does not follow the link', () => {
  const restore = withFakeCanvas();
  try {
    renderApp();
    const about = within(screen.getByRole('navigation', { name: strings.shell.mainNav })).getByRole('link', { name: strings.shell.about });
    const mouse = { pointerId: 1, pointerType: 'mouse' };
    fireEvent.pointerDown(about, { ...mouse, button: 0, buttons: 1, clientX: 20, clientY: 100 });
    expect(about.hasPointerCapture(1)).toBe(true);
    fireEvent.pointerMove(about, { ...mouse, buttons: 1, clientX: 40, clientY: 110 });
    expect(about.dataset.pulling).toBe('true');
    fireEvent.pointerUp(about, { ...mouse, button: 0, buttons: 0, clientX: 40, clientY: 110 });
    expect(about.dataset.pulling).toBeUndefined();
    expect(about.hasPointerCapture(1)).toBe(false);
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    about.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
  } finally {
    restore();
  }
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

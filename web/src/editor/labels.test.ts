import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { keepKetcherLabelled, labelKetcher } from './labels';

// The markup of Ketcher 3.18.0 that Lighthouse flags (button-name, label): two dropdown triangles of the top
// toolbar, each beside the button it belongs to, and the clipboard textarea.
const TRIANGLE = 'copy-button-dropdown-triangle';

function ketcherMarkup(): string {
  return `
    <div class="Ketcher-root">
      <div class="App-module_top">
        <div class="group">
          <button title="Copy (Ctrl+C)" data-testid="copy-button"><svg/></button>
          <button type="button" data-testid="${TRIANGLE}"><svg/></button>
        </div>
        <div class="group">
          <button title="Aromatize (Alt+A)" data-testid="Aromatize button"><svg/></button>
          <button type="button" data-testid="${TRIANGLE}"><svg/></button>
        </div>
        <button title="Undo (Ctrl+Z)">U</button>
      </div>
      <div class="App-module_app"><textarea class="cliparea" data-cliparea="true"></textarea></div>
    </div>`;
}

let hosts: HTMLElement[] = [];
let stops: (() => void)[] = [];

// Writing a label is itself a change the observer sees. A pass that wrote it again every time would make the
// observer call itself for ever (a livelock that freezes the page, and a test that never ends). Every test counts
// the writes and stops them at 50, so that such a bug fails here, loudly, instead of hanging.
let writes = 0;
beforeEach(() => {
  writes = 0;
  const write = Element.prototype.setAttribute;
  vi.spyOn(Element.prototype, 'setAttribute').mockImplementation(function (this: Element, name: string, value: string) {
    if (++writes <= 50) write.call(this, name, value);
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  stops.forEach((stop) => stop());
  hosts.forEach((h) => h.remove());
  stops = [];
  hosts = [];
  expect(writes, 'labels were written over and over: the observer is feeding itself').toBeLessThanOrEqual(12);
});

function host(html = ''): HTMLElement {
  const el = document.createElement('div');
  el.innerHTML = html;
  document.body.append(el);
  hosts.push(el);
  return el;
}
const keep = (el: HTMLElement) => {
  const stop = keepKetcherLabelled(el);
  stops.push(stop);
  return stop;
};
const triangles = (el: ParentNode) => [...el.querySelectorAll<HTMLElement>(`[data-testid="${TRIANGLE}"]`)];
/** MutationObserver callbacks run as microtasks. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test('the dropdown triangles are named after the button beside them, without its shortcut, and the clipboard textarea is named', () => {
  const el = host(ketcherMarkup());
  labelKetcher(el);
  expect(triangles(el).map((t) => t.getAttribute('aria-label'))).toEqual(['More options for Copy', 'More options for Aromatize']);
  expect(el.querySelector('textarea.cliparea')?.getAttribute('aria-label')).toBe('Clipboard for the structure editor');
});

test('a triangle with no named button beside it still gets a name', () => {
  const el = host(`<div class="Ketcher-root"><div><button data-testid="${TRIANGLE}"></button></div></div>`);
  labelKetcher(el);
  expect(triangles(el)[0]?.getAttribute('aria-label')).toBe('More options');
});

test('nothing else is touched: other buttons, and controls that already have a name', () => {
  const el = host(ketcherMarkup());
  const before = [...el.querySelectorAll('button')].filter((b) => !b.matches(`[data-testid="${TRIANGLE}"]`)).map((b) => b.outerHTML);
  labelKetcher(el);
  const after = [...el.querySelectorAll('button')].filter((b) => !b.matches(`[data-testid="${TRIANGLE}"]`)).map((b) => b.outerHTML);
  expect(after).toEqual(before);

  // If Ketcher names them itself one day, its name stays.
  const named = host(
    `<div class="Ketcher-root"><button data-testid="${TRIANGLE}" aria-label="Ketcher's own"></button><textarea class="cliparea" aria-label="Own"></textarea></div>`,
  );
  labelKetcher(named);
  expect(triangles(named)[0]?.getAttribute('aria-label')).toBe("Ketcher's own");
  expect(named.querySelector('textarea')?.getAttribute('aria-label')).toBe('Own');
});

test('only inside Ketcher: a control of ours with the same class or test id is left alone', () => {
  const el = host(`<button data-testid="${TRIANGLE}"></button><textarea class="cliparea"></textarea>` + ketcherMarkup());
  labelKetcher(el);
  expect(el.querySelector(':scope > button')?.hasAttribute('aria-label')).toBe(false);
  expect(el.querySelector(':scope > textarea')?.hasAttribute('aria-label')).toBe(false);
});

test('Ketcher mounts after the editor panel does: the labels follow it in', async () => {
  const el = host(); // the empty canvas host, Suspense still loading
  keep(el);
  el.insertAdjacentHTML('beforeend', ketcherMarkup());
  await settle();
  expect(triangles(el).map((t) => t.getAttribute('aria-label'))).toEqual(['More options for Copy', 'More options for Aromatize']);
  expect(el.querySelector('textarea.cliparea')?.getAttribute('aria-label')).toBe('Clipboard for the structure editor');
});

test('Ketcher re-renders its toolbar: the new buttons are labelled again', async () => {
  const el = host(ketcherMarkup());
  keep(el);
  await settle();
  const top = el.querySelector('.App-module_top')!;
  top.innerHTML = top.innerHTML.replace(/ aria-label="[^"]*"/g, ''); // React replaced the buttons with fresh, unlabelled nodes
  expect(triangles(el).some((t) => t.hasAttribute('aria-label'))).toBe(false);
  await settle();
  expect(triangles(el).map((t) => t.getAttribute('aria-label'))).toEqual(['More options for Copy', 'More options for Aromatize']);
});

test('a label that is removed comes back', async () => {
  const el = host(ketcherMarkup());
  keep(el);
  await settle();
  const textarea = el.querySelector('textarea.cliparea')!;
  textarea.removeAttribute('aria-label');
  await settle();
  expect(textarea.getAttribute('aria-label')).toBe('Clipboard for the structure editor');
});

test('after the stop function nothing is labelled any more', async () => {
  const el = host();
  const stop = keep(el);
  stop();
  el.insertAdjacentHTML('beforeend', ketcherMarkup());
  await settle();
  expect(el.querySelector('textarea.cliparea')?.hasAttribute('aria-label')).toBe(false);
});

test('each label is written once: the observer does not feed itself', async () => {
  const el = host(ketcherMarkup());
  keep(el);
  await settle();
  expect(writes).toBe(3); // two triangles and the textarea
});

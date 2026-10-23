import { strings } from '../strings';

/**
 * Two small corrections to the markup of Ketcher 3.18.0, made from outside because the markup is Ketcher's. Neither
 * touches anything else in Ketcher, and each leaves alone a control that Ketcher has already dealt with itself, so a
 * Ketcher that fixes one of them wins. Upstream text: docs/ketcher-a11y-upstream.md.
 *
 * 1. Names for the controls that Lighthouse flags and that have none (button-name, label): the two dropdown triangles
 *    of the top toolbar (Ketcher gives both the test id `copy-button-dropdown-triangle`, each sits beside the button
 *    it opens) and the clipboard textarea.
 * 2. The toolbars' scroll arrows are taken out of the tab order. Ketcher draws an arrow (a plain button) after the
 *    last button of a toolbar that is longer than its space, and stops drawing it as soon as the end of the toolbar is
 *    in view. Focusing the last button ("Add Image", "Extended Table") scrolls the toolbar, so a Tab pressed within
 *    the 300 ms that takes lands on the arrow, which Ketcher then removes: focus falls to the body for one press.
 *    The arrows are a pointer affordance; every button stays reachable by Tab, which scrolls it into view.
 *
 * Ketcher is React: it replaces toolbar buttons when it re-renders, so both are applied again whenever the markup
 * under `host` changes (`keepKetcherFixed`).
 */
const TRIANGLES = '.Ketcher-root [data-testid="copy-button-dropdown-triangle"]';
const CLIPBOARD = '.Ketcher-root textarea.cliparea';
/** `arrow-scroll-right-button` and `arrow-scroll-left-button`, of the left, right and top toolbars. */
const SCROLL_ARROWS = '.Ketcher-root [data-testid^="arrow-scroll-"]';

/** "Copy (Ctrl+C)" is the button's title; its name is "Copy". */
function nameOf(button: Element | null): string {
  if (!button) return '';
  const raw = button.getAttribute('aria-label') || button.getAttribute('title') || button.textContent || '';
  return raw.replace(/\s*\([^)]*\)\s*$/, '').trim();
}

function setLabel(el: Element, label: string): void {
  // `hasAttribute` first: a name Ketcher gives itself stays, and our own is not written again (no loop with the observer).
  if (!el.hasAttribute('aria-label') && !el.hasAttribute('aria-labelledby')) el.setAttribute('aria-label', label);
}

/** Names the controls in `host` that have none, once (1). */
export function labelKetcher(host: ParentNode): void {
  for (const triangle of host.querySelectorAll(TRIANGLES)) {
    const beside = nameOf(triangle.previousElementSibling);
    setLabel(triangle, beside ? strings.editor.ketcher.moreOptionsFor(beside) : strings.editor.ketcher.moreOptions);
  }
  for (const textarea of host.querySelectorAll(CLIPBOARD)) setLabel(textarea, strings.editor.ketcher.clipboard);
}

/** Takes the scroll arrows in `host` out of the tab order, once (2). A tabindex that Ketcher sets itself stays. */
export function skipScrollArrows(host: ParentNode): void {
  for (const arrow of host.querySelectorAll(SCROLL_ARROWS)) if (!arrow.hasAttribute('tabindex')) arrow.setAttribute('tabindex', '-1');
}

/**
 * Applies both to what is in `host` (the editor panel's canvas, which Ketcher mounts into after it loads) and again
 * after every change to its markup, including a label being removed. Returns the function that stops it.
 */
export function keepKetcherFixed(host: HTMLElement): () => void {
  const fix = () => {
    labelKetcher(host);
    skipScrollArrows(host);
  };
  fix();
  // Subtree changes arrive in batches (the canvas redraws a lot); the pass is three narrow queries. A new arrow is
  // fixed in the microtask after React draws it, before any key can be pressed.
  const observer = new MutationObserver(fix);
  observer.observe(host, { childList: true, subtree: true, attributes: true, attributeFilter: ['aria-label'] });
  return () => observer.disconnect();
}

import { strings } from '../strings';

/**
 * Names for the controls of Ketcher 3.18.0 that Lighthouse flags and that have none (button-name, label): the two
 * dropdown triangles of the top toolbar (Ketcher gives both the test id `copy-button-dropdown-triangle`, each
 * sits beside the button it opens) and the clipboard textarea. Everything else in Ketcher is untouched, and so is
 * a control that has a name, so a Ketcher that labels these itself wins. Upstream text: docs/ketcher-a11y-upstream.md.
 *
 * Ketcher is React: it replaces toolbar buttons when it re-renders, so the labels are applied again whenever the
 * markup under `host` changes (`keepKetcherLabelled`).
 */
const TRIANGLES = '.Ketcher-root [data-testid="copy-button-dropdown-triangle"]';
const CLIPBOARD = '.Ketcher-root textarea.cliparea';

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

/** Names the controls in `host` that have none, once. */
export function labelKetcher(host: ParentNode): void {
  for (const triangle of host.querySelectorAll(TRIANGLES)) {
    const beside = nameOf(triangle.previousElementSibling);
    setLabel(triangle, beside ? strings.editor.ketcher.moreOptionsFor(beside) : strings.editor.ketcher.moreOptions);
  }
  for (const textarea of host.querySelectorAll(CLIPBOARD)) setLabel(textarea, strings.editor.ketcher.clipboard);
}

/**
 * Labels what is in `host` (the editor panel's canvas, which Ketcher mounts into after it loads) and again after
 * every change to its markup, including a label being removed. Returns the function that stops it.
 */
export function keepKetcherLabelled(host: HTMLElement): () => void {
  labelKetcher(host);
  // Subtree changes arrive in batches (the canvas redraws a lot); the pass is two narrow queries.
  const observer = new MutationObserver(() => labelKetcher(host));
  observer.observe(host, { childList: true, subtree: true, attributes: true, attributeFilter: ['aria-label'] });
  return () => observer.disconnect();
}

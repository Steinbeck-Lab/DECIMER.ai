import type { KeyboardEvent } from 'react';

/**
 * While the canvas shows another structure than the heading names (its swaps are queued), Ketcher must not receive
 * the keyboard either: Ctrl+A, Delete, Ctrl+L or a typed letter would change a structure that is about to be replaced
 * (or that is the wrong one). Tab still works, so focus can leave the canvas. Escape passes too: with focus in the
 * canvas the panel leaves it to Ketcher (EditorPanel's `onKeyDown`), and Ketcher's Escape only switches back to the
 * select tool, which does no harm in the middle of a swap. Meant for the capture phase of the canvas host, which
 * comes before Ketcher's own listener.
 */
export function blockKeysWhileBusy(event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey' | 'stopPropagation' | 'preventDefault'>): void {
  if (event.key === 'Tab' || event.key === 'Escape') return;
  event.stopPropagation();
  // The browser's own shortcuts (reload, copy of the page selection ...) keep working; only typing is stopped.
  if (!event.ctrlKey && !event.metaKey && !event.altKey) event.preventDefault();
}

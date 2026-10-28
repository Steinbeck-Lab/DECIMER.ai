import type { KeyboardEvent } from 'react';

/**
 * Ketcher 3.18.0 binds Shift+Tab (and Escape) to its four selection tools, "Rectangle Selection" first. The key handler
 * on its root takes every Shift+Tab inside the editor that is not typed into a text field and cancels it
 * (`preventDefault`), so the browser never moves focus backwards from a toolbar button or from the clipboard field that
 * Ketcher keeps focus in: a keyboard user is stuck there and has to Tab all the way round the panel. Upstream text:
 * web/docs/ketcher-a11y-upstream.md, item 5.
 *
 * Here Shift+Tab is for focus. It is stopped before Ketcher sees it and not cancelled, so the browser moves focus to the
 * previous control in the panel's tab order. Nothing else changes: Rectangle Selection is still the toolbar button and
 * Escape, every other key (Escape and the shortcuts of the canvas included) goes to Ketcher as before, and the panel's
 * focus trap still wraps backwards at its first control ("Previous structure"), which comes before the canvas.
 *
 * Meant for the capture phase of the canvas host, which comes before Ketcher's own listener (like `blockKeysWhileBusy`,
 * which lets Tab and Escape through and so needs this in front of it).
 */
export function keepShiftTabForFocus(event: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'ctrlKey' | 'metaKey' | 'altKey' | 'stopPropagation'>): void {
  if (event.key === 'Tab' && event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey) event.stopPropagation();
}

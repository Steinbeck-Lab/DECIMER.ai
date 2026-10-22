import { useEffect } from 'react';
import { imagesFromClipboard } from './files';

/**
 * A paste that carries an image starts the job at once ("auto-start on paste"
 * is a hard requirement). A paste that carries only text is left alone, so
 * pasting into a text field still works. Returns whether the event was taken.
 */
export function handlePaste(event: ClipboardEvent, submit: (files: File[]) => void): boolean {
  if (event.defaultPrevented) return false;
  // One image per paste: several on the clipboard would be several jobs' worth of guessing.
  const files = imagesFromClipboard(event.clipboardData, 1);
  if (files.length === 0) return false;
  event.preventDefault();
  submit(files);
  return true;
}

/** Listens on the whole document, not on a focused element, so Ctrl+V works wherever the focus is. */
export function usePasteToUpload(enabled: boolean, submit: (files: File[]) => void): void {
  useEffect(() => {
    if (!enabled) return;
    const onPaste = (event: ClipboardEvent) => {
      handlePaste(event, submit);
    };
    document.addEventListener('paste', onPaste);
    return () => document.removeEventListener('paste', onPaste);
  }, [enabled, submit]);
}

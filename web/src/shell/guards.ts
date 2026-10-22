import { useEffect } from 'react';

/** Closing or reloading the tab would lose every edit and check: ask first. */
export function guardUnload(event: BeforeUnloadEvent): void {
  event.preventDefault();
  // Older browsers still need a value; the text is never shown.
  event.returnValue = '';
}

export function useUnloadGuard(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    window.addEventListener('beforeunload', guardUnload);
    return () => window.removeEventListener('beforeunload', guardUnload);
  }, [active]);
}

/**
 * A file dropped anywhere but a drop zone makes the browser open it in this
 * tab, which throws the results away. Cancel such drags, unless a drop zone has
 * claimed them. Other drags (text, links) are not ours to touch.
 */
export function guardStrayDrop(event: DragEvent): void {
  // Something on the page has taken this drag already, such as the drop zones in Ketcher's Open and Image
  // dialogs (react-dropzone prevents the default and sets dropEffect "copy"). This listener runs after
  // it; overwriting that would make the browser refuse a drop the page wants.
  if (event.defaultPrevented) return;
  if (!event.dataTransfer?.types.includes('Files')) return;
  event.preventDefault();
  if (event.type === 'dragover') event.dataTransfer.dropEffect = 'none';
}

/** Active wherever there is no drop zone to take the file: working, results and failed. */
export function useStrayDropGuard(active: boolean): void {
  useEffect(() => {
    if (!active) return;
    window.addEventListener('dragover', guardStrayDrop);
    window.addEventListener('drop', guardStrayDrop);
    return () => {
      window.removeEventListener('dragover', guardStrayDrop);
      window.removeEventListener('drop', guardStrayDrop);
    };
  }, [active]);
}

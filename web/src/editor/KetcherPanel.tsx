import { lazy, Suspense } from 'react';
import { strings } from '../strings';
import type { KetcherEditorProps } from './KetcherEditor';

// Loaded on first use only: the editor and its 11.8 MB Indigo WASM must not
// slow down the upload page.
const KetcherEditor = lazy(() => import('./KetcherEditor'));

/**
 * Starts fetching the editor's JS before it is needed: the first time the
 * pointer moves over the results or keyboard focus lands on a structure
 * (decision D13). The WASM still loads only when the editor mounts.
 * Safe to call any number of times.
 */
export function preloadEditor(): void {
  void import('./KetcherEditor');
}

/** Own markup and styles (styles/editor.css), so the placeholder does not depend on which Mantine sheets are loaded. */
function Loading() {
  return (
    <div className="dc-editor-loading" role="status">
      <span className="dc-spinner" aria-hidden="true" />
      <span>{strings.editorLoading}</span>
    </div>
  );
}

export function KetcherPanel(props: KetcherEditorProps) {
  return (
    <Suspense fallback={<Loading />}>
      <KetcherEditor {...props} />
    </Suspense>
  );
}

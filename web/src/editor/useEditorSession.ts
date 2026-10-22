import type { Ketcher } from 'ketcher-core';
import { useEffect, useMemo } from 'react';
import { EDIT_DEBOUNCE_MS } from '../config';
import { useController } from '../state/react';
import { EditorSession, type KetcherLike } from './session';

/**
 * The session that keeps the one Ketcher instance in step with the store, and
 * the editor's flush registered with the controller so an export always sees the
 * latest edit. `null` until Ketcher is ready.
 */
export function useEditorSession(ketcher: Ketcher | null): EditorSession | null {
  const controller = useController();

  const session = useMemo(
    () =>
      ketcher
        ? new EditorSession(ketcher as unknown as KetcherLike, {
            onEdit: (index, molfile) => controller.edit(index, molfile),
            onRevert: (index) => controller.reset(index),
          }, EDIT_DEBOUNCE_MS)
        : null,
    [ketcher, controller],
  );

  useEffect(() => {
    if (!session) return;
    session.attach();
    const unregister = controller.registerEditorFlush(() => session.flush());
    return () => {
      unregister();
      session.dispose();
    };
  }, [session, controller]);

  return session;
}

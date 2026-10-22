import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react';
import type { ApiClient } from '../api/client';
import { AppController } from './controller';
import type { State } from './model';
import { viewsOf, type ResultView } from './selectors';

const ControllerContext = createContext<AppController | null>(null);

export function AppProvider({ api, children }: { api: ApiClient; children: ReactNode }) {
  const [controller] = useState(() => new AppController(api));
  useEffect(() => () => controller.dispose(), [controller]);
  return <ControllerContext value={controller}>{children}</ControllerContext>;
}

export function useController(): AppController {
  const controller = useContext(ControllerContext);
  if (!controller) throw new Error('useController outside AppProvider');
  return controller;
}

/** The whole state; it only changes identity when the reducer changes something. */
export function useAppState(): State {
  const { store } = useController();
  return useSyncExternalStore(store.subscribe, store.getState);
}

/** Every result of the job with the user's edits applied, in result order. */
export function useResultViews(state: State): ResultView[] {
  return useMemo(() => viewsOf({ job: state.job, edits: state.edits }), [state.job, state.edits]);
}

export interface Store<S, A> {
  getState(): S;
  dispatch(action: A): void;
  subscribe(listener: () => void): () => void;
}

/**
 * A reducer behind a synchronous store. `getState()` is current the moment
 * `dispatch()` returns, which React's own useReducer cannot promise. The export
 * relies on that: it flushes the editor, then reads the edits, with nothing in
 * between (the ZIP race of the earlier version).
 */
export function createStore<S, A>(reducer: (state: S, action: A) => S, initial: S): Store<S, A> {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    getState: () => state,
    dispatch(action) {
      const next = reducer(state, action);
      if (next === state) return;
      state = next;
      listeners.forEach((l) => l());
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
}

import { expect, test, vi } from 'vitest';
import { EditorSession, type KetcherLike } from './session';
import { ServiceActivity } from './serviceActivity';

/** A Ketcher stand-in: setMolecule/getMolfile with a canvas, and change events like the real one. */
function fakeKetcher() {
  const handlers = new Set<() => void>();
  const listeners: Record<string, Set<() => void>> = {};
  let canvas = '';
  let stamp = 0;
  const ketcher: KetcherLike & {
    draw(s: string): void;
    canvasText(): string;
    sets: string[];
    clears: number;
    /**
     * Structures Ketcher cannot load. The real one swallows the error (runAsyncAction): setMolecule
     * resolves, the canvas keeps what it had, and `eventBus` emits FAILURE.
     */
    unloadable: Set<string>;
    /** Structures for which setMolecule rejects (not what Ketcher does, but the session must cope). */
    throwing: Set<string>;
  } = {
    sets: [],
    clears: 0,
    unloadable: new Set(),
    throwing: new Set(),
    eventBus: {
      addListener: (event: string, fn: () => void) => void (listeners[event] ??= new Set()).add(fn),
      removeListener: (event: string, fn: () => void) => void listeners[event]?.delete(fn),
    },
    editor: {
      clearHistory: () => {
        ketcher.clears++;
      },
      struct: (value: null) => {
        if (value === null) canvas = '';
      },
    },
    async setMolecule(s: string) {
      ketcher.sets.push(s);
      if (ketcher.throwing.has(s)) throw new Error(`Ketcher cannot load ${s}`);
      if (ketcher.unloadable.has(s)) {
        listeners.FAILURE?.forEach((fn) => fn());
        return undefined; // swallowed, like runAsyncAction does
      }
      canvas = s;
      handlers.forEach((h) => h()); // loading fires change events too
    },
    // The header's timestamp line changes on every read, as in Ketcher.
    async getMolfile() {
      return `\n  Ketcher ${++stamp}\n\n${canvas}`;
    },
    changeEvent: {
      add: (h) => void handlers.add(h),
      remove: (h) => void handlers.delete(h),
    },
    draw(s: string) {
      canvas = s;
      handlers.forEach((h) => h());
    },
    canvasText: () => canvas,
  };
  return ketcher;
}

function setup() {
  vi.useFakeTimers();
  const ketcher = fakeKetcher();
  const edits: Array<[number, string]> = [];
  const reverts: number[] = [];
  const session = new EditorSession(
    ketcher,
    { onEdit: (i, m) => edits.push([i, m.split('\n').slice(3).join('\n')]), onRevert: (i) => reverts.push(i) },
    100,
  );
  session.attach();
  return { ketcher, session, edits, reverts };
}

test('loading a result reports no edit, even though loading fires change events', async () => {
  const { session, edits, reverts } = setup();
  await session.load(0, 'A', null);
  await vi.advanceTimersByTimeAsync(500);
  expect(edits).toEqual([]);
  expect(reverts).toEqual([]);
  vi.useRealTimers();
});

test('a user edit is reported once, after the debounce, for the right result', async () => {
  const { ketcher, session, edits } = setup();
  await session.load(0, 'A', null);
  ketcher.draw('A+');
  ketcher.draw('A++');
  expect(edits).toEqual([]);
  await vi.advanceTimersByTimeAsync(150);
  expect(edits).toEqual([[0, 'A++']]);
  vi.useRealTimers();
});

test('an edit still waiting is committed to the result being left, not to the next one', async () => {
  const { ketcher, session, edits } = setup();
  await session.load(0, 'A', null);
  ketcher.draw('A-edited'); // debounce has not fired
  await session.load(1, 'B', null);
  await vi.advanceTimersByTimeAsync(500);
  expect(edits).toEqual([[0, 'A-edited']]);
  expect(ketcher.canvasText()).toBe('B');
  vi.useRealTimers();
});

test('coming back to an edited result shows its edit and reports nothing', async () => {
  const { ketcher, session, edits } = setup();
  await session.load(0, 'A', null);
  ketcher.draw('A-edited');
  await session.flush();
  await session.load(1, 'B', null);
  await session.load(0, 'A', 'A-edited');
  await vi.advanceTimersByTimeAsync(500);
  expect(ketcher.canvasText()).toBe('A-edited');
  expect(edits).toEqual([[0, 'A-edited']]);
  vi.useRealTimers();
});

test('an edit that ends equal to the recognised structure is a revert', async () => {
  const { ketcher, session, edits, reverts } = setup();
  await session.load(0, 'A', null);
  ketcher.draw('A-edited');
  await session.flush();
  ketcher.draw('A'); // undo
  await session.flush();
  expect(edits).toEqual([[0, 'A-edited']]);
  expect(reverts).toEqual([0]);
  vi.useRealTimers();
});

test('flush commits an edit that the debounce has not delivered yet', async () => {
  const { ketcher, session, edits } = setup();
  await session.load(2, 'C', null);
  ketcher.draw('C-edited');
  expect(edits).toEqual([]);
  await session.flush();
  expect(edits).toEqual([[2, 'C-edited']]);
  vi.useRealTimers();
});

test('reset puts the recognised structure back and drops an edit that was waiting', async () => {
  const { ketcher, session, edits } = setup();
  await session.load(0, 'A', null);
  ketcher.draw('A-typed-just-now');
  await session.reset(0, 'A');
  await vi.advanceTimersByTimeAsync(500);
  expect(ketcher.canvasText()).toBe('A');
  expect(edits).toEqual([]);
  vi.useRealTimers();
});

test('ten quick swaps end on the last one with every result keeping its own state', async () => {
  const { ketcher, session, edits } = setup();
  await session.load(0, 'A', null);
  ketcher.draw('A-edited');
  const swaps: Promise<void>[] = [];
  for (let i = 0; i < 10; i++) swaps.push(session.load(i % 2 === 0 ? 1 : 0, i % 2 === 0 ? 'B' : 'A', i % 2 === 0 ? null : 'A-edited'));
  swaps.push(session.load(1, 'B', null));
  await Promise.all(swaps);
  await vi.advanceTimersByTimeAsync(500);
  expect(session.currentIndex).toBe(1);
  expect(ketcher.canvasText()).toBe('B');
  expect(edits).toEqual([[0, 'A-edited']]);
  vi.useRealTimers();
});

test('after dispose, changes are ignored', async () => {
  const { ketcher, session, edits } = setup();
  await session.load(0, 'A', null);
  session.dispose();
  ketcher.draw('A-edited');
  await vi.advanceTimersByTimeAsync(500);
  expect(edits).toEqual([]);
  vi.useRealTimers();
});

test('forget starts clean: the next job\'s result 0 does not inherit the last job\'s baseline or canvas', async () => {
  const { ketcher, session, edits, reverts } = setup();
  await session.load(0, 'A', null);
  await session.forget();
  expect(session.currentIndex).toBeNull();
  await session.load(0, 'Z', null); // a different molecule behind the same index
  ketcher.draw('A'); // coincides with the old job's baseline for index 0
  await session.flush();
  expect(reverts).toEqual([]);
  expect(edits).toEqual([[0, 'A']]);
  vi.useRealTimers();
});

test('the undo history is cleared after every load and reset, so Ctrl+Z cannot bring back another result', async () => {
  const { ketcher, session } = setup();
  await session.load(0, 'A', null);
  expect(ketcher.clears).toBe(1);
  await session.load(1, 'B', null);
  expect(ketcher.clears).toBe(2);
  await session.load(0, 'A', 'A-edited');
  expect(ketcher.clears).toBe(3);
  await session.reset(0, 'A');
  expect(ketcher.clears).toBe(4);
  vi.useRealTimers();
});

test('an edited result that is loaded again has no baseline: only Reset brings it back to recognised', async () => {
  const { ketcher, session, edits, reverts } = setup();
  await session.load(0, 'A', 'A-edited'); // opened with an edit already in the store
  ketcher.draw('A'); // the user types the recognised structure by hand: that is still an edit of the edited one
  await session.flush();
  expect(reverts).toEqual([]);
  expect(edits).toEqual([[0, 'A']]);
  vi.useRealTimers();
});

test('a failed load leaves the canvas empty and no current result: later drawing is nobody\'s edit', async () => {
  const { ketcher, session, edits, reverts } = setup();
  ketcher.unloadable.add('BAD');
  await session.load(0, 'A', null);
  ketcher.draw('A-edited'); // still waiting in the debounce when the user opens result 1
  await expect(session.load(1, 'BAD', null)).rejects.toThrow('could not load');

  // The edit belongs to result 0 and was committed to it before the swap.
  expect(edits).toEqual([[0, 'A-edited']]);
  // The canvas no longer shows result 0's structure, and nothing claims to be current.
  expect(ketcher.canvasText()).toBe('');
  expect(session.currentIndex).toBeNull();

  // Whatever the user draws now is not an edit of result 0 (the one the canvas used to show) or of 1.
  ketcher.draw('scribble');
  await vi.advanceTimersByTimeAsync(500);
  await session.flush();
  expect(edits).toEqual([[0, 'A-edited']]);
  expect(reverts).toEqual([]);
  vi.useRealTimers();
});

test('after a failed load the next load works and edits are attributed again', async () => {
  const { ketcher, session, edits } = setup();
  ketcher.unloadable.add('BAD');
  await session.load(0, 'A', null);
  await session.load(1, 'BAD', null).catch(() => undefined);
  await session.load(2, 'C', null);
  expect(session.currentIndex).toBe(2);
  ketcher.draw('C-edited');
  await session.flush();
  expect(edits).toEqual([[2, 'C-edited']]);
  vi.useRealTimers();
});

test('the current result is cleared before the load starts, not only when it fails', async () => {
  const { ketcher, session } = setup();
  await session.load(0, 'A', null);
  let seenDuringLoad: number | null | undefined;
  const original = ketcher.setMolecule.bind(ketcher);
  ketcher.setMolecule = async (s: string) => {
    if (s === 'B') seenDuringLoad = session.currentIndex;
    return original(s);
  };
  await session.load(1, 'B', null);
  expect(seenDuringLoad).toBeNull(); // a change event while B is loading cannot be filed under A
  expect(session.currentIndex).toBe(1);
  vi.useRealTimers();
});

test('a setMolecule that rejects is handled the same way as one that swallows its error', async () => {
  const { ketcher, session } = setup();
  ketcher.throwing.add('BAD');
  await session.load(0, 'A', null);
  await expect(session.load(1, 'BAD', null)).rejects.toThrow('cannot load BAD');
  expect(ketcher.canvasText()).toBe('');
  expect(session.currentIndex).toBeNull();
  vi.useRealTimers();
});

test('if even clearing the canvas fails, the load error is still the one reported and nothing is current', async () => {
  const { ketcher, session } = setup();
  ketcher.throwing.add('BAD');
  ketcher.editor.struct = () => {
    throw new Error('cannot clear');
  };
  await session.load(0, 'A', null);
  await expect(session.load(1, 'BAD', null)).rejects.toThrow('cannot load BAD');
  expect(session.currentIndex).toBeNull();
  vi.useRealTimers();
});

test('Ketcher failures outside a load are not mistaken for one', async () => {
  const { ketcher, session } = setup();
  await session.load(0, 'A', null);
  ketcher.eventBus.addListener('FAILURE', () => undefined);
  // A failure that happened before this load must not fail it.
  ketcher.unloadable.add('ZZZ');
  await ketcher.setMolecule('ZZZ');
  await expect(session.load(1, 'B', null)).resolves.toBeUndefined();
  expect(session.currentIndex).toBe(1);
  vi.useRealTimers();
});

// ---- races between a swap and what the user (or Ketcher) is doing -----------------------------------------------

/** A session wired to a `ServiceActivity`, as the real editor is (its struct service reports to it). Fake timers. */
function setupWithActivity() {
  vi.useFakeTimers();
  const ketcher = fakeKetcher();
  const activity = new ServiceActivity();
  const edits: Array<[number, string]> = [];
  const session = new EditorSession(
    ketcher,
    { onEdit: (i, m) => edits.push([i, m.split('\n').slice(3).join('\n')]), onRevert: () => undefined },
    100,
    activity,
  );
  session.attach();
  /** `settled` looks again one turn of the event loop later, which fake timers only run when told. */
  const finished = async <T>(work: Promise<T>) => {
    await vi.advanceTimersByTimeAsync(10);
    return work;
  };
  /**
   * A server call of Ketcher that is still running (Layout, Clean Up ...): its answer replaces the whole canvas
   * when it arrives, with the structure the call was started on. The tracker is told, as the wrapped struct
   * service of the real editor does.
   */
  const runningLayout = (laidOut: string) => {
    let answer!: () => void;
    const call = new Promise<void>((resolve) => {
      answer = resolve;
    });
    // What Ketcher does with the promise it gets from the service: draw the answer.
    void activity.track(call, true).then(
      () => ketcher.draw(laidOut),
      () => undefined,
    );
    return answer;
  };
  return { ketcher, session, edits, finished, runningLayout, activity };
}

test('a Layout still running when the user steps on is kept, on the result it was started on', async () => {
  const { ketcher, session, edits, finished, runningLayout } = setupWithActivity();
  await finished(session.load(0, 'A', null));

  const answer = runningLayout('A-laid-out');
  const swap = session.load(1, 'B', null); // the user pressed Next while Indigo is still working
  await vi.advanceTimersByTimeAsync(50);
  expect(ketcher.canvasText()).toBe('A'); // the swap waits for the answer: it would be overwritten by it
  answer();
  await finished(swap);
  await vi.advanceTimersByTimeAsync(500);

  expect(edits).toEqual([[0, 'A-laid-out']]); // result 0 has its layout; result 1 inherited nothing
  expect(ketcher.canvasText()).toBe('B');
  expect(session.currentIndex).toBe(1);
  vi.useRealTimers();
});

test('an export waits for a server call of Ketcher that is still running', async () => {
  const { session, edits, finished, runningLayout } = setupWithActivity();
  await finished(session.load(0, 'A', null));

  const answer = runningLayout('A-laid-out');
  const flushed = session.flush();
  await vi.advanceTimersByTimeAsync(50);
  expect(edits).toEqual([]);
  answer();
  await finished(flushed);
  expect(edits).toEqual([[0, 'A-laid-out']]);
  vi.useRealTimers();
});

test('a Reset waits for a running server call, so its answer cannot land on the recognised structure', async () => {
  const { ketcher, session, edits, finished, runningLayout } = setupWithActivity();
  await finished(session.load(0, 'A', null));

  const answer = runningLayout('A-laid-out');
  const reset = session.reset(0, 'A');
  await vi.advanceTimersByTimeAsync(50); // without the wait, the reset has put 'A' on the canvas by now
  answer();
  await finished(reset);
  await vi.advanceTimersByTimeAsync(500);
  expect(ketcher.canvasText()).toBe('A');
  expect(edits).toEqual([]);
  vi.useRealTimers();
});

test('a Layout that answers only after the cap is dropped: it is never filed under another result', async () => {
  const { ketcher, session, edits, finished, runningLayout } = setupWithActivity();
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  await finished(session.load(0, 'A', null));
  const answer = runningLayout('A-laid-out');
  const swap = session.load(1, 'B', null);
  await vi.advanceTimersByTimeAsync(5100); // the swap gave up waiting at 5 s and went on
  await finished(swap);
  expect(ketcher.canvasText()).toBe('B');
  expect(warn).toHaveBeenCalledTimes(1);
  answer(); // 5.1 s after the call was made
  await vi.advanceTimersByTimeAsync(500);

  expect(ketcher.canvasText()).toBe('B'); // not the old structure, laid out
  expect(edits).toEqual([]); // and nothing is filed under result 1
  expect(session.currentIndex).toBe(1);
  warn.mockRestore();
  vi.useRealTimers();
});

test('a call that never answers delays one swap by the cap, not every later one', async () => {
  const { ketcher, session, finished, runningLayout } = setupWithActivity();
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  await finished(session.load(0, 'A', null));
  runningLayout('never drawn'); // never answered

  const first = session.load(1, 'B', null);
  await vi.advanceTimersByTimeAsync(4999);
  expect(ketcher.canvasText()).toBe('A');
  await vi.advanceTimersByTimeAsync(10);
  await finished(first);
  expect(ketcher.canvasText()).toBe('B');

  const second = session.load(2, 'C', null);
  await vi.advanceTimersByTimeAsync(10); // no more than the turn of the event loop that `settled` takes
  await second;
  expect(ketcher.canvasText()).toBe('C');
  expect(session.shownIndex).toBe(2);
  warn.mockRestore();
  vi.useRealTimers();
});

test('a call that is older than the cap when the user steps on is not waited for at all', async () => {
  const { ketcher, session, finished, runningLayout } = setupWithActivity();
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  await finished(session.load(0, 'A', null));
  runningLayout('never drawn');
  await vi.advanceTimersByTimeAsync(20_000); // the user worked on, then pressed Next
  const swap = session.load(1, 'B', null);
  await vi.advanceTimersByTimeAsync(10);
  await swap;
  expect(ketcher.canvasText()).toBe('B');
  warn.mockRestore();
  vi.useRealTimers();
});

test('stepping on and straight back brings the edit back to the canvas, although it reached the store only during the swap', async () => {
  const { ketcher, session, edits } = setup();
  await session.load(0, 'A', null);
  ketcher.draw('A-edited'); // the debounce has not fired: the store does not know yet
  const stored = () => edits.find(([i]) => i === 0)?.[1] ?? null;
  // Next, then Previous, before the first swap has run: when Previous is asked for, result 0 has no edit in the store.
  const next = session.load(1, 'B', () => stored());
  const previous = session.load(0, 'A', () => stored());
  await Promise.all([next, previous]);
  await vi.advanceTimersByTimeAsync(500);

  expect(edits).toEqual([[0, 'A-edited']]);
  expect(ketcher.canvasText()).toBe('A-edited'); // not the recognised structure under an "Edited" tile
  expect(session.currentIndex).toBe(0);
  vi.useRealTimers();
});

test('shownIndex names the result on the canvas only once every swap that was asked for has finished', async () => {
  const { session } = setup();
  const seen: Array<number | null> = [];
  const unsubscribe = session.subscribe(() => seen.push(session.shownIndex));
  expect(session.shownIndex).toBeNull();

  const first = session.load(0, 'A', null);
  const second = session.load(1, 'B', null);
  expect(session.shownIndex).toBeNull(); // the canvas is about to change twice
  await first;
  expect(session.shownIndex).toBeNull(); // 0 is on it for a moment, but 1 is still queued
  await second;
  expect(session.shownIndex).toBe(1);
  expect(seen.at(-1)).toBe(1);
  expect(seen.filter((v) => v === 0)).toEqual([]); // never announced as ready while another swap was waiting

  unsubscribe();
  await session.load(2, 'C', null);
  expect(seen.at(-1)).toBe(1); // no more calls after unsubscribing
  vi.useRealTimers();
});

test('shownIndex stays null after a failed load, and a later load makes it ready again', async () => {
  const { ketcher, session } = setup();
  ketcher.unloadable.add('BAD');
  await session.load(0, 'A', null);
  await session.load(1, 'BAD', null).catch(() => undefined);
  expect(session.shownIndex).toBeNull();
  await session.load(2, 'C', null);
  expect(session.shownIndex).toBe(2);
  vi.useRealTimers();
});

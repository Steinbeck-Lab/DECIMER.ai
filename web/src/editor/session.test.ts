import { expect, test, vi } from 'vitest';
import { EditorSession, type KetcherLike } from './session';

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

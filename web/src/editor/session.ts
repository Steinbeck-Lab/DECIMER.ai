import { sameStructure } from './molfile';
import { serial } from './serial';
import type { ServiceActivity } from './serviceActivity';

/** The part of Ketcher the session uses (and a fake can stand in for in tests). */
export interface KetcherLike {
  getMolfile(): Promise<string>;
  setMolecule(structure: string): Promise<unknown>;
  changeEvent: { add(handler: () => void): void; remove(handler: () => void): void };
  editor: { clearHistory(): void; /** `struct(null)` empties the canvas (what Ketcher's Clear Canvas does). */ struct(value: null): unknown };
  /** Ketcher's event emitter; `setMolecule` reports a failure here instead of rejecting. */
  eventBus: {
    addListener(event: string, listener: () => void): unknown;
    removeListener(event: string, listener: () => void): unknown;
  };
}

/** `KetcherAsyncEvents.FAILURE` of ketcher-core. */
const FAILURE = 'FAILURE';

export interface SessionCallbacks {
  /** The user changed the structure of result `index`. */
  onEdit(index: number, molfile: string): void;
  /** The user's edits of result `index` cancel out: the canvas is back at the recognised structure. */
  onRevert(index: number): void;
}

/**
 * Keeps the single Ketcher instance and the store in step.
 *
 * - `load` swaps the canvas to another result. Everything is queued (`serial`),
 *   so a fast second click cannot interleave with a swap in flight.
 * - Loading a molecule is an undoable step in Ketcher, so Ctrl+Z after a swap
 *   would bring back the previous result's structure and record it as an edit
 *   of this one. The undo history is cleared after every load.
 * - Ketcher's change events are debounced and then compared with the last
 *   molfile the session saw. Loading also fires change events, but it does not
 *   change that molfile, so only real edits are reported.
 * - A result loaded from its recognised structure has a baseline: the molfile
 *   the canvas had right after the load. (Ketcher re-centres a loaded structure,
 *   so the same molecule gets different coordinates in each load: only a
 *   baseline taken after this load can be compared.) An edit that ends equal to
 *   it is reported as a revert, so "modified" means "differs from recognised".
 * - `flush` commits an edit still waiting in the debounce. The export awaits it.
 * - Ketcher's own server calls (Layout, Clean Up ...) answer later and put the answer on the canvas whatever is on it
 *   then. Every swap, reset and flush therefore waits until none is running, so the answer is filed under the result
 *   it was asked for and cannot land on the next one. A call that has not answered after a few seconds stops being
 *   waited for, and its answer is turned into an error when it comes (see `serviceActivity.ts`, which also lists
 *   what is not covered).
 * - What a swap shows is decided when it runs, not when it was asked for: the edit of a result that was left a moment
 *   ago reaches the store only while the swap away from it runs, and a swap back to it, queued earlier, must show it.
 */
export class EditorSession {
  private readonly run = serial();
  private current: number | null = null;
  private baseline: string | null = null;
  private lastSeen = '';
  private timer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;
  /** Swaps (load, reset, forget) that were asked for and have not finished. */
  private pending = 0;
  private readonly listeners = new Set<() => void>();
  private readonly onChange = () => {
    if (this.disposed || this.current === null) return;
    this.clearTimer();
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.run(() => this.commit());
    }, this.debounceMs);
  };

  constructor(
    private readonly ketcher: KetcherLike,
    private readonly callbacks: SessionCallbacks,
    private readonly debounceMs = 250,
    /** Ketcher's server calls (`ServiceActivity`): the session waits for them, and ends their epoch when it leaves a result. */
    private readonly service: Pick<ServiceActivity, 'settled' | 'leave'> = { settled: () => Promise.resolve(), leave: () => Promise.resolve() },
  ) {}

  /** Starts listening to Ketcher. Separate from the constructor so React can create sessions without side effects. */
  attach(): void {
    this.disposed = false;
    this.ketcher.changeEvent.add(this.onChange);
  }

  /** The result whose structure is on the canvas; null while a swap is in flight, after a failed load, and before the first one. */
  get currentIndex(): number | null {
    return this.current;
  }

  /**
   * The result whose structure is on the canvas once every swap that was asked for has finished; null until then.
   * The panel treats the canvas as ready (editable) only for the result it names.
   */
  get shownIndex(): number | null {
    return this.pending === 0 ? this.current : null;
  }

  /** Calls `listener` whenever `shownIndex` may have changed (for `useSyncExternalStore`). */
  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => void this.listeners.delete(listener);
  };

  /**
   * Shows result `index`: its edit when it has one, otherwise the recognised structure. `edited` may be a function:
   * it is called when the swap runs, after the edit of the result being left has been committed.
   */
  load(index: number, original: string, edited: string | null | (() => string | null)): Promise<void> {
    this.clearTimer();
    return this.swap(async () => {
      await this.service.leave(); // a Layout started on the result we are leaving must land on it, not on the next one
      await this.commit(); // an edit made a moment ago belongs to the result we are leaving
      const stored = typeof edited === 'function' ? edited() : edited;
      await this.show(index, stored ?? original, stored === null);
    });
  }

  /** Puts the recognised structure back on the canvas and drops edits still waiting to be reported. */
  reset(index: number, original: string): Promise<void> {
    this.clearTimer();
    return this.swap(async () => {
      await this.service.leave(); // its answer would otherwise replace the recognised structure
      await this.show(index, original, true);
    });
  }

  /** A new job starts: nothing of the old one may leak into it (result indexes repeat). */
  forget(): Promise<void> {
    this.clearTimer();
    return this.swap(async () => {
      this.current = null;
      this.baseline = null;
      this.lastSeen = '';
    });
  }

  flush(): Promise<void> {
    this.clearTimer();
    return this.run(async () => {
      await this.service.settled(); // an export right after Layout must carry the laid-out structure
      await this.commit();
    });
  }

  dispose(): void {
    this.disposed = true;
    this.clearTimer();
    this.ketcher.changeEvent.remove(this.onChange);
  }

  /** Queues a swap and keeps count of those not finished, so `shownIndex` stays null while the canvas is about to change. */
  private swap(task: () => Promise<void>): Promise<void> {
    this.pending++;
    this.notify();
    return this.run(task).finally(() => {
      this.pending--;
      this.notify();
    });
  }

  private notify(): void {
    this.listeners.forEach((listener) => listener());
  }

  private clearTimer(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private async show(index: number, structure: string, recognised: boolean): Promise<void> {
    // Nothing is current while the swap is in flight: a change event now cannot be filed under
    // the result that is being left, and a load that fails leaves no result claiming the canvas.
    this.current = null;
    this.baseline = null;
    this.lastSeen = '';
    try {
      await this.loadMolecule(structure);
    } catch (error) {
      // The canvas may still show the previous structure; it must not sit there under another number.
      try {
        this.ketcher.editor.struct(null);
        this.ketcher.editor.clearHistory();
      } catch {
        // Nothing more can be done; the original error is the one to report.
      }
      throw error;
    }
    this.ketcher.editor.clearHistory();
    this.current = index;
    this.lastSeen = await this.ketcher.getMolfile();
    this.baseline = recognised ? this.lastSeen : null;
  }

  /**
   * Ketcher's `setMolecule` does not reject when the structure cannot be loaded: it logs, emits
   * FAILURE on its event bus and resolves with nothing, leaving the previous structure on the
   * canvas. Listen for that, or a failed load would look like a successful one.
   */
  private async loadMolecule(structure: string): Promise<void> {
    let failed = false;
    const onFailure = () => {
      failed = true;
    };
    this.ketcher.eventBus.addListener(FAILURE, onFailure);
    try {
      await this.ketcher.setMolecule(structure);
    } finally {
      this.ketcher.eventBus.removeListener(FAILURE, onFailure);
    }
    if (failed) throw new Error('Ketcher could not load the structure');
  }

  private async commit(): Promise<void> {
    const index = this.current;
    if (index === null || this.disposed) return;
    const molfile = await this.ketcher.getMolfile();
    if (sameStructure(molfile, this.lastSeen)) return;
    this.lastSeen = molfile;
    if (this.baseline !== null && sameStructure(molfile, this.baseline)) this.callbacks.onRevert(index);
    else this.callbacks.onEdit(index, molfile);
  }
}

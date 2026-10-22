import { sameStructure } from './molfile';
import { serial } from './serial';

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
 */
export class EditorSession {
  private readonly run = serial();
  private current: number | null = null;
  private baseline: string | null = null;
  private lastSeen = '';
  private timer: ReturnType<typeof setTimeout> | null = null;
  private disposed = false;
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

  /** Shows result `index`: its edit when it has one, otherwise the recognised structure. */
  load(index: number, original: string, edited: string | null): Promise<void> {
    this.clearTimer();
    return this.run(async () => {
      await this.commit(); // an edit made a moment ago belongs to the result we are leaving
      await this.show(index, edited ?? original, edited === null);
    });
  }

  /** Puts the recognised structure back on the canvas and drops edits still waiting to be reported. */
  reset(index: number, original: string): Promise<void> {
    this.clearTimer();
    return this.run(() => this.show(index, original, true));
  }

  /** A new job starts: nothing of the old one may leak into it (result indexes repeat). */
  forget(): Promise<void> {
    this.clearTimer();
    return this.run(async () => {
      this.current = null;
      this.baseline = null;
      this.lastSeen = '';
    });
  }

  flush(): Promise<void> {
    this.clearTimer();
    return this.run(() => this.commit());
  }

  dispose(): void {
    this.disposed = true;
    this.clearTimer();
    this.ketcher.changeEvent.remove(this.onChange);
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

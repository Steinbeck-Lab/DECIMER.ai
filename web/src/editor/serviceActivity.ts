/**
 * Ketcher's Layout, Clean Up, Aromatize and the like are calls to its struct service (Indigo). Ketcher copies the
 * canvas when it makes the call and, when the answer comes, puts the changed copy back as the whole canvas. Nothing
 * stops that answer from arriving after the editor has moved on to another result: it would then overwrite the new
 * structure with the old one, and the session would file it as an edit of the new result. So the editor needs to
 * know when such a call is running, waits for it before it leaves a result, and, when it cannot wait any longer,
 * makes sure the answer is never drawn.
 *
 * Known gap: for a structure with S-group types Ketcher does not support, its write-back (`load` in
 * ketcher-react's `ui/state/shared.ts`, `hasUnsupportedGroups`) stops to ask the user to confirm, after our
 * wrapper has seen the answer. That answer can then land arbitrarily late and is not covered. DECIMER's own
 * molfiles (RDKit, CDK) carry no S-groups, so this needs a user who draws one and then runs Layout on it.
 *
 * Second known gap: `convert` (Open, drag-and-drop) writes the canvas through `load()` but is not one of the
 * writers above, so its answer is never turned stale. It is waited for like any call, so it lands on the result it
 * started on unless it takes longer than the cap (5 s); only a hung Indigo worker gets there, since a molfile
 * converts in milliseconds and a slow start keeps the canvas busy anyway (our own swap needs convert too).
 */

/**
 * The struct-service methods whose answer Ketcher writes back as the whole canvas, the ones that can clobber a result
 * the editor has moved on to. Taken from ketcher-react 3.18: `serverTransform` (`ui/action/server.ts`: layout, clean,
 * aromatize, dearomatize, calculateCip, toggleExplicitHydrogens; `ui/state/server/index.js`: automap), the editor's
 * `onAromatizeStruct` and `onDearomatizeStruct`, and ketcher-core's own `layout`, `aromatize` and `dearomatize`.
 * Everything else that returns a promise (convert, check, calculate, recognize, generateImageAsBase64 ...) is still
 * waited for, but its answer is always given as it is.
 */
export const CANVAS_WRITERS: ReadonlySet<string> = new Set([
  'layout',
  'clean',
  'aromatize',
  'dearomatize',
  'calculateCip',
  'automap',
  'toggleExplicitHydrogens',
]);

/**
 * `info()` is the handshake with Indigo: Ketcher keeps its promise as `server`, and every action starts with
 * `server.then(...)`. It answers when the WASM (11.8 MB) has started, however long that takes, so it is neither waited
 * for nor ever turned into an error.
 */
const HANDSHAKE: ReadonlySet<string> = new Set(['info']);

/** How long a swap waits for a call of Ketcher to answer. After this the call stops counting and its answer is dropped. */
export const SETTLE_CAP_MS = 5000;

/** What a call of Ketcher's struct service gets back when its answer comes after the editor has left its result. */
export class StaleAnswerError extends Error {
  constructor() {
    super('Ketcher answered after the structure it was asked about had been replaced; the answer was not used.');
    this.name = 'StaleAnswerError';
  }
}

interface Call {
  /** The epoch the call was made in; a canvas-writing call is stale when the epoch has ended by the time it is answered. */
  epoch: number;
  startedAt: number;
  writesCanvas: boolean;
}

export class ServiceActivity {
  private epoch = 0;
  private readonly calls = new Set<Call>();
  private readonly watchers = new Set<() => void>();

  /**
   * Follows `call` until it is answered, and returns the promise Ketcher is to use in its place. It gives the same
   * answer or error, except that the answer of a call that writes the canvas (`writesCanvas`), when it comes after
   * the epoch of the call has ended, is turned into a rejection (`StaleAnswerError`): Ketcher then reports an error
   * instead of drawing the old structure. Any other call gets its answer whenever it comes.
   */
  track<T>(call: PromiseLike<T>, writesCanvas: boolean): Promise<T> {
    const entry: Call = { epoch: this.epoch, startedAt: Date.now(), writesCanvas };
    this.calls.add(entry);
    return new Promise<T>((resolve, reject) => {
      const answered = () => {
        this.calls.delete(entry);
        this.watchers.forEach((watcher) => watcher());
      };
      call.then(
        (value) => {
          answered();
          if (!entry.writesCanvas || entry.epoch === this.epoch) resolve(value);
          else reject(new StaleAnswerError());
        },
        (error: unknown) => {
          answered();
          reject(error);
        },
      );
    });
  }

  /**
   * Resolves once no call is running and Ketcher has applied the last answer. Ketcher does that in promise
   * callbacks that run after ours, so the check is repeated one turn of the event loop later. A call that has been
   * running for `capMs` stops counting (with a warning): the editor must not be stuck behind it, nor wait for it
   * again at every later swap.
   */
  settled(capMs = SETTLE_CAP_MS): Promise<void> {
    return new Promise((resolve) => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const check = () => {
        clearTimeout(timer);
        const now = Date.now();
        for (const call of this.calls) {
          if (now - call.startedAt < capMs) continue;
          this.calls.delete(call);
          console.warn(`Ketcher: a call to its structure service has not answered in ${capMs} ms; going on without it.`);
        }
        if (this.calls.size === 0) {
          timer = setTimeout(() => {
            if (this.calls.size > 0) return check();
            this.watchers.delete(check);
            resolve();
          }, 0);
          return;
        }
        const oldest = Math.min(...[...this.calls].map((call) => call.startedAt));
        timer = setTimeout(check, oldest + capMs - now);
      };
      this.watchers.add(check);
      check();
    });
  }

  /**
   * For a swap to another result: `settled`, and then the epoch ends. Whatever was asked of Ketcher before this and
   * still has no answer (a call that outlived the cap) is not to be drawn on the structure that comes next.
   */
  async leave(capMs = SETTLE_CAP_MS): Promise<void> {
    await this.settled(capMs);
    this.epoch++;
  }
}

/** The one tracker of the page: there is one Ketcher struct service, and one editor session. */
export const serviceActivity = new ServiceActivity();

function isThenable(value: unknown): value is PromiseLike<unknown> {
  return typeof (value as { then?: unknown } | null)?.then === 'function';
}

/**
 * `service` with every method that returns a promise reported to `activity` (all but `info`, see `HANDSHAKE`), and
 * the answers of the canvas-writing ones (`CANVAS_WRITERS`) held to the epoch rule. A proxy, not a subclass, so it
 * follows whatever methods the service has now or gets later; it calls them on the service itself, so private
 * fields and methods bound by Ketcher (`service.layout.bind(service)`) keep working.
 */
export function trackCalls<T extends object>(service: T, activity: ServiceActivity): T {
  return new Proxy(service, {
    get(target, property) {
      const value: unknown = Reflect.get(target, property, target);
      if (typeof value !== 'function') return value;
      return (...args: unknown[]) => {
        const result: unknown = value.apply(target, args);
        if (!isThenable(result) || HANDSHAKE.has(String(property))) return result;
        return activity.track(result, CANVAS_WRITERS.has(String(property)));
      };
    },
  });
}

import { afterEach, expect, test, vi } from 'vitest';
import { CANVAS_WRITERS, ServiceActivity, StaleAnswerError, trackCalls } from './serviceActivity';

afterEach(() => vi.useRealTimers());

function deferred<T = void>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

test('settled resolves at once when no call is pending', async () => {
  vi.useFakeTimers();
  const activity = new ServiceActivity();
  const done = vi.fn();
  void activity.settled().then(done);
  await vi.advanceTimersByTimeAsync(1);
  expect(done).toHaveBeenCalled();
});

test('settled waits for a call, and then for what Ketcher does with the answer in later promise callbacks', async () => {
  vi.useFakeTimers();
  const activity = new ServiceActivity();
  const call = deferred<string>();
  let applied = false;
  // Ketcher attaches its own callbacks after the tracker's, and applies the answer a few promise turns later.
  const answer = activity.track(call.promise, true);
  void answer.then(() => Promise.resolve()).then(() => Promise.resolve()).then(() => {
    applied = true;
  });
  let settled = false;
  void activity.settled().then(() => {
    settled = true;
    expect(applied).toBe(true);
  });
  await vi.advanceTimersByTimeAsync(100);
  expect(settled).toBe(false);
  call.resolve('answer');
  await vi.advanceTimersByTimeAsync(5);
  expect(settled).toBe(true);
});

test('a call that fails is not pending any more, and its failure still reaches whoever made the call', async () => {
  vi.useFakeTimers();
  const activity = new ServiceActivity();
  const call = deferred();
  const tracked = activity.track(call.promise, true);
  const caught = expect(tracked).rejects.toThrow('indigo failed');
  let settled = false;
  void activity.settled().then(() => (settled = true));
  call.reject(new Error('indigo failed'));
  await caught;
  await vi.advanceTimersByTimeAsync(5);
  expect(settled).toBe(true);
});

test('a call that starts while settled is waiting is waited for as well', async () => {
  vi.useFakeTimers();
  const activity = new ServiceActivity();
  const first = deferred();
  const second = deferred();
  void activity.track(first.promise, true);
  let settled = false;
  void activity.settled().then(() => (settled = true));
  first.resolve();
  void activity.track(second.promise, true); // started in the turn after the first answer
  await vi.advanceTimersByTimeAsync(50);
  expect(settled).toBe(false);
  second.resolve();
  await vi.advanceTimersByTimeAsync(5);
  expect(settled).toBe(true);
});

test('settled gives up when a call never answers, so the editor cannot be stuck behind it', async () => {
  vi.useFakeTimers();
  const activity = new ServiceActivity();
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  void activity.track(new Promise(() => undefined), true);
  let settled = false;
  void activity.settled(1000).then(() => (settled = true));
  await vi.advanceTimersByTimeAsync(999);
  expect(settled).toBe(false);
  await vi.advanceTimersByTimeAsync(2);
  expect(settled).toBe(true);
  expect(warn).toHaveBeenCalledTimes(1);
  warn.mockRestore();
});

test('an answer after the epoch has ended becomes a rejection, one before it is given as it is', async () => {
  vi.useFakeTimers();
  const activity = new ServiceActivity();
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  const early = deferred<string>();
  const late = deferred<string>();
  const earlyAnswer = activity.track(early.promise, true);
  const lateAnswer = activity.track(late.promise, true);
  early.resolve('drawn');
  await expect(earlyAnswer).resolves.toBe('drawn');

  const left = activity.leave(100); // `late` has not answered: it is given up after the cap
  await vi.advanceTimersByTimeAsync(200);
  await left;
  late.resolve('too late');
  await expect(lateAnswer).rejects.toBeInstanceOf(StaleAnswerError);
  vi.restoreAllMocks();
});

test('a call made after the epoch ended belongs to the new one and is answered normally', async () => {
  vi.useFakeTimers();
  const activity = new ServiceActivity();
  const left = activity.leave();
  await vi.advanceTimersByTimeAsync(5);
  await left;
  const call = deferred<string>();
  const answer = activity.track(call.promise, true);
  call.resolve('fresh');
  await expect(answer).resolves.toBe('fresh');
});

class Service {
  readonly #name = 'indigo';
  readonly gate = deferred<string>();
  layout(input: string): Promise<string> {
    return this.gate.promise.then((answer) => `${this.#name}:${input}:${answer}`);
  }
  info(): string {
    return this.#name;
  }
  get ready(): boolean {
    return true;
  }
}

test('trackCalls follows the calls that return a promise and passes everything else through, keeping `this` intact', async () => {
  vi.useFakeTimers();
  const activity = new ServiceActivity();
  const service = new Service();
  const wrapped = trackCalls(service, activity);
  expect(wrapped.info()).toBe('indigo'); // a private field is read through the wrapper
  expect(wrapped.ready).toBe(true);

  const bound = wrapped.layout.bind(wrapped); // Ketcher binds the methods when it builds its API
  const answer = bound('A');
  let settled = false;
  void activity.settled().then(() => (settled = true));
  await vi.advanceTimersByTimeAsync(50);
  expect(settled).toBe(false);
  service.gate.resolve('done');
  await expect(answer).resolves.toBe('indigo:A:done');
  await vi.advanceTimersByTimeAsync(5);
  expect(settled).toBe(true);
});

/** What the struct service of Ketcher looks like to the wrapper: a few methods, each answered when the test says so. */
function fakeIndigo() {
  const gates: Record<string, ReturnType<typeof deferred<string>>> = {};
  const call = (name: string) => () => (gates[name] ??= deferred<string>()).promise;
  return {
    gates,
    service: {
      info: call('info'),
      convert: call('convert'),
      check: call('check'),
      calculate: call('calculate'),
      layout: call('layout'),
      clean: call('clean'),
      aromatize: call('aromatize'),
      dearomatize: call('dearomatize'),
      calculateCip: call('calculateCip'),
      automap: call('automap'),
      toggleExplicitHydrogens: call('toggleExplicitHydrogens'),
    },
  };
}

/** The epoch ends: a swap has run (a pending call outlived the cap, so it was given up). */
async function endEpoch(activity: ServiceActivity) {
  const left = activity.leave(10);
  await vi.advanceTimersByTimeAsync(20);
  await left;
}

test('info() answering after an epoch has ended still resolves: Ketcher keeps it as the server every later call starts from', async () => {
  vi.useFakeTimers();
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  const activity = new ServiceActivity();
  const { gates, service } = fakeIndigo();
  const info = trackCalls(service, activity).info(); // Indigo is still starting
  await endEpoch(activity); // the first structure is loaded and the next one asked for: epochs end
  await endEpoch(activity);
  gates.info?.resolve('indigo is up'); // the WASM has arrived at last
  await expect(info).resolves.toBe('indigo is up');
  vi.restoreAllMocks();
});

test('info() is not waited for either: a swap does not sit behind the start of Indigo', async () => {
  vi.useFakeTimers();
  const activity = new ServiceActivity();
  void trackCalls(fakeIndigo().service, activity).info();
  let left = false;
  void activity.leave().then(() => (left = true));
  await vi.advanceTimersByTimeAsync(5);
  expect(left).toBe(true);
});

test('only the calls whose answer Ketcher writes on the canvas can be stale', async () => {
  vi.useFakeTimers();
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  const activity = new ServiceActivity();
  const { gates, service } = fakeIndigo();
  const wrapped = trackCalls(service, activity);
  const names = Object.keys(service).filter((name) => name !== 'info');
  const answers = Object.fromEntries(names.map((name) => [name, (wrapped as Record<string, () => Promise<string>>)[name]?.()]));
  await endEpoch(activity); // none has answered yet: all are given up
  for (const name of names) gates[name]?.resolve(name);
  for (const name of names) {
    const answer = answers[name] as Promise<string>;
    if (CANVAS_WRITERS.has(name)) await expect(answer, name).rejects.toBeInstanceOf(StaleAnswerError);
    else await expect(answer, name).resolves.toBe(name);
  }
  expect([...CANVAS_WRITERS].sort()).toEqual(['aromatize', 'automap', 'calculateCip', 'clean', 'dearomatize', 'layout', 'toggleExplicitHydrogens']);
  vi.restoreAllMocks();
});

test('a call that does not write the canvas (convert, check) is still waited for', async () => {
  vi.useFakeTimers();
  const activity = new ServiceActivity();
  const { gates, service } = fakeIndigo();
  const answer = trackCalls(service, activity).convert(); // a structure pasted a moment ago
  let settled = false;
  void activity.settled().then(() => (settled = true));
  await vi.advanceTimersByTimeAsync(50);
  expect(settled).toBe(false);
  gates.convert?.resolve('pasted');
  await expect(answer).resolves.toBe('pasted');
  await vi.advanceTimersByTimeAsync(5);
  expect(settled).toBe(true);
});

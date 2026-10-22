// Runs async tasks one at a time in call order, so a fast second click cannot
// interleave with a molecule swap that is still in flight.
export function serial() {
  let tail: Promise<unknown> = Promise.resolve();
  return <T>(task: () => Promise<T>): Promise<T> => {
    const run = tail.then(task, task);
    tail = run.catch(() => undefined);
    return run;
  };
}

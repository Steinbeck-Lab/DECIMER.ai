import { expect, test } from 'vitest';
import { serial } from './serial';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

test('runs tasks in call order even when an earlier one is slower', async () => {
  const run = serial();
  const order: string[] = [];
  await Promise.all([
    run(async () => {
      await sleep(20);
      order.push('slow');
    }),
    run(async () => {
      order.push('fast');
    }),
  ]);
  expect(order).toEqual(['slow', 'fast']);
});

test('a failing task does not block the next one', async () => {
  const run = serial();
  await expect(run(() => Promise.reject(new Error('boom')))).rejects.toThrow('boom');
  await expect(run(async () => 'next')).resolves.toBe('next');
});

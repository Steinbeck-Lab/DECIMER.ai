import { expect, test } from 'vitest';
import { STAGE_ORDER, type StageState } from '../api/contract';
import { stageProgress } from './selectors';

/** The stage list of a job: upload, then render, segment, recognise and check in the given states. */
const stages = (...states: StageState[]) => ({
  stages: STAGE_ORDER.map((name, i) => ({ name, state: i === 0 ? ('done' as const) : (states[i - 1] ?? 'pending') })),
});

test('before the job exists the first stage is the current one', () => {
  expect(stageProgress(null)).toMatchObject({ current: 0, active: [], doneCount: 0 });
});

test('a job that has not started a stage is at the first one after the upload', () => {
  expect(stageProgress(stages())).toMatchObject({ current: 1, active: [], doneCount: 1 });
});

test('one active stage is the current one', () => {
  expect(stageProgress(stages('active'))).toMatchObject({ current: 1, active: [1], doneCount: 1 });
  expect(stageProgress(stages('done', 'done', 'active'))).toMatchObject({ current: 3, active: [3], doneCount: 3 });
});

test('with several stages active at once the current one is the latest, and all of them are listed', () => {
  expect(stageProgress(stages('active', 'active'))).toMatchObject({ current: 2, active: [1, 2], doneCount: 1 });
  expect(stageProgress(stages('done', 'active', 'active', 'active'))).toMatchObject({
    current: 4,
    active: [2, 3, 4],
    doneCount: 2,
  });
});

test('between two stages the first one that is not done is the current one', () => {
  expect(stageProgress(stages('done', 'done'))).toMatchObject({ current: 3, active: [], doneCount: 3 });
});

test('a finished job stays on the last stage', () => {
  expect(stageProgress(stages('done', 'done', 'done', 'done'))).toMatchObject({
    current: 4,
    active: [],
    doneCount: 5,
  });
});

test('the stages that run alongside the current one are named, in pipeline order', () => {
  expect(stageProgress(stages('active', 'active', 'active')).alongside).toEqual(['render', 'segment']);
  expect(stageProgress(stages('done', 'active')).alongside).toEqual([]);
  expect(stageProgress(null).alongside).toEqual([]);
});

test('each stage has its own state, and a stage the record leaves out is pending', () => {
  const { states } = stageProgress({ stages: [{ name: 'upload', state: 'done' }, { name: 'segment', state: 'active' }] });
  expect(states).toEqual({ upload: 'done', render: 'pending', segment: 'active', recognise: 'pending', check: 'pending' });
});

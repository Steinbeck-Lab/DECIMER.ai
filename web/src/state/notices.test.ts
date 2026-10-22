import { expect, test } from 'vitest';
import { jobNotices } from './notices';

const base = { pages: 3, pagesProcessed: 3, structuresDropped: 0, truncated: false, warnings: [] };

test('a job within the limits has no notices', () => {
  expect(jobNotices(base)).toEqual([]);
});

test('pages cut off: our wording from the numbers, once, even though the API also sends a pages_truncated warning', () => {
  const notices = jobNotices({
    ...base,
    pages: 14,
    pagesProcessed: 10,
    truncated: true,
    warnings: [{ code: 'pages_truncated', message: 'The document has 14 pages. Only the first 10 were processed.' }],
  });
  expect(notices).toEqual(['This document has 14 pages. Only the first 10 were processed.']);
});

test('structures dropped by the cap are named, with the number', () => {
  expect(jobNotices({ ...base, structuresDropped: 3, truncated: true })).toEqual([
    '3 more structures were found. Only the first 20 are shown.',
  ]);
  expect(jobNotices({ ...base, structuresDropped: 1, truncated: true })).toEqual([
    '1 more structure was found. Only the first 20 are shown.',
  ]);
});

test('a warning code this version does not know is shown as the API worded it', () => {
  expect(jobNotices({ ...base, warnings: [{ code: 'image_downscaled', message: 'One image was reduced in size.' }] })).toEqual([
    'One image was reduced in size.',
  ]);
});

test('truncated with no reason given still says so', () => {
  expect(jobNotices({ ...base, truncated: true })).toEqual([
    'This upload was longer than the limits, so only part of it was processed.',
  ]);
});

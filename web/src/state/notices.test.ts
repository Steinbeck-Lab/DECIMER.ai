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

test('the structure cap is said once: our wording from the count, not the API structures_truncated sentence as well', () => {
  // What the API sends for the golden PDF (32 structures found, 20 kept, structure 13 not recognised).
  const notices = jobNotices({
    ...base,
    pages: 10,
    pagesProcessed: 10,
    structuresDropped: 12,
    truncated: true,
    warnings: [
      { code: 'structures_truncated', message: '32 structures were found. Only the first 20 were processed.' },
      { code: 'recognition_failed', message: 'Structure 13 could not be recognised.' },
    ],
  });
  // The other warning is a different fact and stays.
  expect(notices).toEqual(['12 more structures were found. Only the first 20 are shown.', 'Structure 13 could not be recognised.']);
});

test('a structures_truncated warning with no dropped count to word it from is shown as the API worded it', () => {
  expect(
    jobNotices({
      ...base,
      truncated: true,
      warnings: [{ code: 'structures_truncated', message: '32 structures were found. Only the first 20 were processed.' }],
    }),
  ).toEqual(['32 structures were found. Only the first 20 were processed.']);
});

test('pages and structures both cut off: one sentence each, then the other warnings', () => {
  const notices = jobNotices({
    ...base,
    pages: 14,
    pagesProcessed: 10,
    structuresDropped: 3,
    truncated: true,
    warnings: [
      { code: 'pages_truncated', message: 'The document has 14 pages. Only the first 10 were processed.' },
      { code: 'structures_truncated', message: '23 structures were found. Only the first 20 were processed.' },
    ],
  });
  expect(notices).toEqual([
    'This document has 14 pages. Only the first 10 were processed.',
    '3 more structures were found. Only the first 20 are shown.',
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

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

test('the warnings about one structure or page each are one sentence for each kind, with a proper list of numbers', () => {
  const warning = (code: string, message: string) => ({ code, message });
  expect(
    jobNotices({
      ...base,
      warnings: [1, 4, 8, 9, 10].map((n) => warning('recognition_failed', `Structure ${n} could not be recognised.`)),
    }),
  ).toEqual(['Structures 1, 4, 8, 9 and 10 could not be recognised.']);
  // One is singular; two are "a and b"; they are in order and each number is there once.
  expect(jobNotices({ ...base, warnings: [warning('recognition_failed', 'Structure 1 could not be recognised.')] })).toEqual([
    'Structure 1 could not be recognised.',
  ]);
  expect(
    jobNotices({
      ...base,
      warnings: [
        warning('recognition_failed', 'Structure 9 could not be recognised.'),
        warning('recognition_failed', 'Structure 2 could not be recognised.'),
        warning('recognition_failed', 'Structure 9 could not be recognised.'),
      ],
    }),
  ).toEqual(['Structures 2 and 9 could not be recognised.']);
});

test('different kinds stay separate lines, each where its first warning was, among warnings that are not about a structure', () => {
  const notices = jobNotices({
    ...base,
    warnings: [
      { code: 'page_render_failed', message: 'Page 3 could not be rendered and was skipped.' },
      { code: 'recognition_failed', message: 'Structure 5 could not be recognised.' },
      { code: 'page_render_failed', message: 'Page 7 could not be rendered and was skipped.' },
      { code: 'classification_failed', message: 'Structure 2 could not be classified.' },
      { code: 'recognition_failed', message: 'Structure 6 could not be recognised.' },
      { code: 'fragments_trimmed', message: 'Structure 4: the prediction had several fragments. Only the first was kept.' },
      { code: 'fragments_trimmed', message: 'Structure 8: the prediction had several fragments. Only the first was kept.' },
      { code: 'classifier_unavailable', message: 'The classifier is unavailable, so the structures were not checked.' },
      { code: 'page_segmentation_failed', message: 'Page 2 could not be searched for structures.' },
    ],
  });
  expect(notices).toEqual([
    'Pages 3 and 7 could not be rendered and were skipped.',
    'Structures 5 and 6 could not be recognised.',
    'Structure 2 could not be classified.',
    'Structures 4 and 8: the predictions had several fragments. Only the first of each was kept.',
    'The classifier is unavailable, so the structures were not checked.',
    'Page 2 could not be searched for structures.',
  ]);
});

test('a warning of a counted kind with no number in its text is shown as the API worded it', () => {
  expect(jobNotices({ ...base, warnings: [{ code: 'recognition_failed', message: 'Something could not be recognised.' }] })).toEqual([
    'Something could not be recognised.',
  ]);
});

test('structures the model cut off are one sentence made from the results, after the warnings the API sent', () => {
  expect(jobNotices(base, [4])).toEqual(["Structure 4 was cut off at the model's length limit; check it in the editor."]);
  expect(jobNotices(base, [9, 1, 4, 8, 10, 1])).toEqual([
    "Structures 1, 4, 8, 9 and 10 were cut off at the model's length limit; check them in the editor.",
  ]);
  expect(
    jobNotices({ ...base, warnings: [{ code: 'recognition_failed', message: 'Structure 2 could not be recognised.' }] }, [3, 5]),
  ).toEqual([
    'Structure 2 could not be recognised.',
    "Structures 3 and 5 were cut off at the model's length limit; check them in the editor.",
  ]);
  // None cut off (or all edited): nothing is said.
  expect(jobNotices(base, [])).toEqual([]);
});

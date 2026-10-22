import { expect, test } from 'vitest';
import { normaliseMolfile, sameStructure } from './molfile';

const body = ' 1  0  0  0  0  0  0  0  0  0999 V2000\n    0.0000    0.0000    0.0000 C   0  0\nM  END\n';

test('two reads that differ only in the timestamp line are the same structure', () => {
  const a = `\n  Ketcher 10221513242D 1   1.00000     0.00000     0\n\n${body}`;
  const b = `\n  Ketcher 10221514002D 1   1.00000     0.00000     0\n\n${body}`;
  expect(sameStructure(a, b)).toBe(true);
});

test('a moved atom is a different structure', () => {
  const a = `\n  Ketcher 1\n\n${body}`;
  const b = `\n  Ketcher 1\n\n${body.replace('0.0000    0.0000', '1.2500    0.0000')}`;
  expect(sameStructure(a, b)).toBe(false);
});

test('line endings and trailing whitespace do not matter', () => {
  expect(normaliseMolfile('t\r\np\r\nc\r\nbody\r\n\r\n')).toBe('body');
});

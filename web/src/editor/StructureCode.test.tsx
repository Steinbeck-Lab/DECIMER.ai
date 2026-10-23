import { MantineProvider } from '@mantine/core';
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import { asCdkOnly, fixtureResults } from '../mocks/fixtures';
import type { Result } from '../api/contract';
import type { EditEntry } from '../state/model';
import { viewOf } from '../state/selectors';
import { smilesLine, StructureCode } from './StructureCode';

afterEach(cleanup);

const [first] = fixtureResults(1);
if (!first) throw new Error('no fixture');
const ethanol: Result = first;

function show(result: Result, edit?: EditEntry) {
  render(
    <MantineProvider>
      <StructureCode view={viewOf(result, edit)} />
    </MantineProvider>,
  );
}

const waiting: EditEntry = { molfile: 'x', modified: true, checked: false, derived: null, validating: true, validationFailed: false };

test('an RDKit-valid structure shows its SMILES, copyable, with no note on who accepted it', () => {
  show(ethanol);
  expect(screen.getByLabelText('SMILES').textContent).toBe(ethanol.smiles);
  expect(screen.getByRole('button', { name: 'Copy SMILES' }).hasAttribute('disabled')).toBe(false);
  expect(screen.queryByText(/CDK/)).toBeNull();
});

test('a structure only CDK accepted shows DECIMER\'s SMILES with the CDK note, and the SMILES can be copied', () => {
  show(asCdkOnly(ethanol));
  expect(screen.getByLabelText('SMILES').textContent).toBe(ethanol.smiles);
  expect(screen.getByText('Accepted by CDK only (RDKit could not read it)')).toBeTruthy();
  expect(screen.queryByText('No valid SMILES')).toBeNull();
  expect(screen.getByRole('button', { name: 'Copy SMILES' }).hasAttribute('disabled')).toBe(false);
});

test('a structure nobody accepted has no SMILES to show or copy, and no CDK note', () => {
  show({ ...ethanol, valid: false, validBy: null, inchikey: '', name: null, nameTier: null, nameVerified: null });
  expect(screen.getByLabelText('SMILES').textContent).toBe('No valid SMILES');
  expect(screen.getByRole('button', { name: 'Copy SMILES' }).hasAttribute('disabled')).toBe(true);
  expect(screen.queryByText(/CDK/)).toBeNull();
});

test('while an edit is unanswered nothing of the CDK result is shown: it is about the structure before the edit', () => {
  show(asCdkOnly(ethanol), waiting);
  expect(screen.getByLabelText('SMILES').textContent).toBe('Updating…');
  expect(screen.queryByText(/CDK/)).toBeNull();
  expect(screen.getByRole('button', { name: 'Copy SMILES' }).hasAttribute('disabled')).toBe(true);
});

test('after an edit the server\'s answer decides: RDKit accepts it, and the CDK note is gone', () => {
  const answered: EditEntry = {
    molfile: 'x',
    modified: true,
    checked: false,
    derived: { smiles: 'CCO', inchikey: 'LFQSCWFLJHTTHZ-UHFFFAOYSA-N', svg: '', valid: true, name: null, nameTier: null, nameVerified: null },
    validating: false,
    validationFailed: false,
  };
  show(asCdkOnly(ethanol), answered);
  expect(screen.getByLabelText('SMILES').textContent).toBe('CCO');
  expect(screen.queryByText(/CDK/)).toBeNull();
});

test('smilesLine: the CDK line needs a SMILES to show', () => {
  expect(smilesLine({ stale: false, validating: false, valid: false, validBy: 'cdk', smiles: '' }).text).toBe('No valid SMILES');
});

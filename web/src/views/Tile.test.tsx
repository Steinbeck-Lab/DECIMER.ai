import { MantineProvider } from '@mantine/core';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';
import { asCdkOnly, fixtureResults } from '../mocks/fixtures';
import { viewOf } from '../state/selectors';
import type { EditEntry } from '../state/model';
import { Tile } from './Tile';

afterEach(cleanup);

const [ethanol] = fixtureResults(1);
if (!ethanol) throw new Error('no fixture');

function show(result = ethanol, edit?: EditEntry) {
  const view = viewOf(result!, edit);
  render(
    <MantineProvider>
      <ul>
        <Tile view={view} selected={false} order={0} onSelect={vi.fn()} />
      </ul>
    </MantineProvider>,
  );
  return screen.getByRole('button');
}

const hoverText = async (button: HTMLElement) => {
  fireEvent.focus(button);
  return (await screen.findByRole('tooltip', undefined, { timeout: 2000 })).textContent;
};

test('a structure only CDK accepted says so on the tile instead of "Invalid SMILES"', () => {
  const button = show(asCdkOnly(ethanol));
  expect(button.textContent).toContain('Accepted by CDK only');
  expect(button.textContent).not.toContain('Invalid SMILES');
  expect(button.getAttribute('data-status')).toBe('review'); // still low trust
});

test('its hover says that RDKit could not read it, then that there is no name', async () => {
  const text = await hoverText(show(asCdkOnly(ethanol)));
  expect(text).toBe('Accepted by CDK only (RDKit could not read it)No verified name');
});

test('a structure nobody accepted keeps its "Invalid SMILES" flag and no CDK line', async () => {
  const button = show({ ...ethanol, valid: false, validBy: null, inchikey: '', name: null, nameTier: null, nameVerified: null });
  expect(button.textContent).toContain('Invalid SMILES');
  expect(button.textContent).not.toContain('CDK');
  expect(await hoverText(button)).toBe('No verified name');
});

test('an RDKit-valid structure has neither flag nor CDK line', async () => {
  const button = show();
  expect(button.textContent).not.toContain('Invalid SMILES');
  expect(button.textContent).not.toContain('CDK');
  expect(await hoverText(button)).not.toContain('CDK');
});

test('while an edit is unanswered the tile says nothing about CDK: the values are from before it', async () => {
  const button = show(asCdkOnly(ethanol), {
    molfile: 'x',
    modified: true,
    checked: false,
    derived: null,
    validating: true,
    validationFailed: false,
  });
  expect(button.textContent).not.toContain('CDK');
  expect(await hoverText(button)).toBe('Updating name and SMILES…');
});

const cropOf = (button: HTMLElement) => button.querySelector<HTMLImageElement>('img.dc-tile__crop');

test('the original crop is shown as a picture while it loads', () => {
  const button = show();
  expect(cropOf(button)?.getAttribute('src')).toBe(ethanol.cropUrl);
  expect(button.textContent).not.toContain('No picture');
});

test('a crop that is not there (404) is a quiet "No picture", never a broken image', () => {
  const button = show();
  fireEvent.error(cropOf(button)!);
  expect(cropOf(button)).toBeNull(); // no <img> left to show the browser's broken-image icon
  const placeholder = button.querySelector('.dc-tile__crop');
  expect(placeholder?.textContent).toBe('No picture');
  expect(placeholder?.getAttribute('data-crop-missing')).toBe('true');
  // the rest of the tile is untouched: the depiction, the number, the status
  expect(button.querySelector('img.dc-tile__structure')).not.toBeNull();
  expect(button.querySelector('.dc-tile__number')?.textContent).toBe(String(ethanol.index + 1));
});

test('only the selected tile stands in the inspection frame, which is decoration', () => {
  const view = viewOf(ethanol!);
  const { container, rerender } = render(
    <MantineProvider>
      <ul>
        <Tile view={view} selected={false} order={0} onSelect={vi.fn()} />
      </ul>
    </MantineProvider>,
  );
  expect(container.querySelector('.dc-sel')).toBeNull();
  rerender(
    <MantineProvider>
      <ul>
        <Tile view={view} selected order={0} onSelect={vi.fn()} />
      </ul>
    </MantineProvider>,
  );
  const frame = container.querySelector('.dc-sel');
  expect(frame?.getAttribute('aria-hidden')).toBe('true');
  expect(frame?.querySelectorAll('.dc-sel__handle')).toHaveLength(4);
  // It is not inside the button, so the button is still named by the structure alone.
  expect(screen.getByRole('button').contains(frame)).toBe(false);
});

test('the button is named "Structure n", the name and the status, in that order; the footer number is for the eye', () => {
  const button = show();
  expect(button.textContent?.startsWith(`Structure ${ethanol!.number}`)).toBe(true);
  expect(button.querySelector('.dc-tile__id')?.getAttribute('aria-hidden')).toBe('true');
  expect(button.querySelector('.dc-status .dc-dot')?.getAttribute('data-status')).toBe('unchecked');
});

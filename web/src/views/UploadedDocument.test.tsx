import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import { UploadedDocument } from './UploadedDocument';

afterEach(cleanup);

const urls = (n: number) => Array.from({ length: n }, (_, i) => `/api/jobs/j/media/page-${i}.png`);
const toggle = () => screen.getByRole('button', { name: 'Display uploaded document' });

test('there is nothing to toggle for a job without page images', () => {
  render(<UploadedDocument pageUrls={[]} />);
  expect(screen.queryByRole('button')).toBeNull();
});

test('shut, it fetches nothing: the toggle is collapsed and no image is rendered', () => {
  render(<UploadedDocument pageUrls={urls(3)} />);
  expect(toggle().getAttribute('aria-expanded')).toBe('false');
  expect(document.querySelectorAll('img')).toHaveLength(0);
  // The strip the button controls exists but is not exposed.
  expect(document.getElementById(toggle().getAttribute('aria-controls') ?? '')?.hidden).toBe(true);
  expect(screen.queryByRole('region')).toBeNull();
});

test('opening shows every page in order, lazy, each named "Page n of N"; shutting removes them again', () => {
  render(<UploadedDocument pageUrls={urls(10)} />);
  fireEvent.click(toggle());
  expect(toggle().getAttribute('aria-expanded')).toBe('true');
  const images = screen.getAllByRole('img');
  expect(images.map((img) => img.getAttribute('alt'))).toEqual(Array.from({ length: 10 }, (_, i) => `Page ${i + 1} of 10`));
  expect(images.map((img) => img.getAttribute('src'))).toEqual(urls(10));
  expect(images.every((img) => img.getAttribute('loading') === 'lazy')).toBe(true);
  // The strip scrolls inside its own box, which a keyboard can reach and a screen reader can name.
  const strip = screen.getByRole('region', { name: 'Uploaded document, 10 pages. Scroll sideways to see them all.' });
  expect(strip.getAttribute('tabindex')).toBe('0');
  expect(strip.id).toBe(toggle().getAttribute('aria-controls'));
  expect(strip.contains(images[9] ?? null)).toBe(true);
  fireEvent.click(toggle());
  expect(document.querySelectorAll('img')).toHaveLength(0);
});

test('a single image is "Page 1 of 1" and its region says "1 page"', () => {
  render(<UploadedDocument pageUrls={urls(1)} />);
  fireEvent.click(toggle());
  expect(screen.getByRole('img', { name: 'Page 1 of 1' })).toBeTruthy();
  // One page has nothing to scroll to, so the name does not say to scroll.
  expect(screen.getByRole('region').getAttribute('aria-label')).toBe('Uploaded document, 1 page');
});

test('a page that cannot be loaded (the worker skipped it) says so in its place, and the others stay', () => {
  render(<UploadedDocument pageUrls={urls(3)} />);
  fireEvent.click(toggle());
  fireEvent.error(screen.getByRole('img', { name: 'Page 2 of 3' }));
  expect(screen.queryByRole('img', { name: 'Page 2 of 3' })).toBeNull();
  expect(screen.getByText('Page 2 could not be shown.')).toBeTruthy();
  expect(screen.getAllByRole('img').map((img) => img.getAttribute('alt'))).toEqual(['Page 1 of 3', 'Page 3 of 3']);
});

test('the toggle is a real button, so Enter and Space work (no handler on a div)', () => {
  render(<UploadedDocument pageUrls={urls(2)} />);
  expect(toggle().tagName).toBe('BUTTON');
  expect(toggle().getAttribute('type')).toBe('button');
});

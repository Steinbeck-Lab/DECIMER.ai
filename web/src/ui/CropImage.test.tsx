import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, expect, test } from 'vitest';
import { CropImage } from './CropImage';

afterEach(cleanup);

test('shows the crop, and "No picture" in its place when it fails to load', () => {
  const { container } = render(<CropImage className="crop" src="/a.png" alt="Original crop" />);
  const image = container.querySelector('img');
  expect(image?.getAttribute('alt')).toBe('Original crop');
  fireEvent.error(image!);
  expect(container.querySelector('img')).toBeNull();
  const none = container.querySelector('span');
  expect(none?.textContent).toBe('No picture');
  expect(none?.className).toBe('crop dc-crop--none');
});

test('another crop is tried again after one failed', () => {
  const { container, rerender } = render(<CropImage src="/a.png" alt="" />);
  fireEvent.error(container.querySelector('img')!);
  expect(container.textContent).toBe('No picture');
  rerender(<CropImage src="/b.png" alt="" />);
  expect(container.querySelector('img')?.getAttribute('src')).toBe('/b.png');
});

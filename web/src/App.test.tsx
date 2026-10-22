import { MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';
import { App } from './App';
import { strings } from './strings';

test('renders the app name', () => {
  render(
    <MantineProvider>
      <App />
    </MantineProvider>,
  );
  expect(screen.getByRole('heading', { name: strings.appName })).toBeTruthy();
});

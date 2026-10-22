// jsdom has no matchMedia; Mantine reads it for the colour scheme.
// (Tests that opt into the node environment have no window.)
if (typeof window !== 'undefined') Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

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

// jsdom has no canvas: getContext would log "not implemented" on every ribbon. A null context is what an old browser gives,
// and the ribbons handle it (the label sits still, no cloth is drawn).
if (typeof HTMLCanvasElement !== 'undefined') HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;

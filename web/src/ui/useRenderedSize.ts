import { useLayoutEffect, useRef, useState } from 'react';

/**
 * The rendered size of an element in whole pixels, kept up to date as the window changes: an inspection frame's label
 * prints it. It is read in a layout effect, so the label is there on the first paint; with no ResizeObserver (an old
 * browser, a test) there is no size to read and none is returned.
 */
export function useRenderedSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const measure = () => {
      const { width, height } = el.getBoundingClientRect();
      const next = { width: Math.round(width), height: Math.round(height) };
      setSize((old) => (old && old.width === next.width && old.height === next.height ? old : next));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, size] as const;
}

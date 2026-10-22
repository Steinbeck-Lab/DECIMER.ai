/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Which backend the app talks to: `mock` (the default, until P3-12 provides an HTTP client). */
  readonly VITE_API?: 'mock' | 'http';
  /** Quiet time after the last canvas change before an edit is reported (ms). Tests lengthen it; unset in production. */
  readonly VITE_EDIT_DEBOUNCE_MS?: string;
}

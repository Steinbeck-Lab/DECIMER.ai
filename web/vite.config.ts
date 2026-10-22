/// <reference types="vitest/config" />
import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The CSP the production nginx will send. Preview serves it too, so the
// Ketcher spike and e2e tests run under the real policy.
export const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self' 'wasm-unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "worker-src 'self' blob:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
].join('; ');

export default defineConfig({
  // Relative base: the same build serves /beta/ and / (ROADMAP §3).
  base: './',
  plugins: [react()],
  // Ketcher's bundle still references Node's `global`.
  define: { global: 'globalThis' },
  build: {
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, 'index.html'),
        ketcherSpike: resolve(import.meta.dirname, 'spike/ketcher.html'),
      },
    },
  },
  preview: {
    headers: { 'Content-Security-Policy': CONTENT_SECURITY_POLICY },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['src/test/setup.ts'],
  },
});

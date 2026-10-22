/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The CSP the production nginx will send. Preview serves it too, so the
// e2e tests run under the real policy.
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
  // Ketcher's bundle still references Node's `global`, and its `util` polyfill reads `process.env.NODE_DEBUG`
  // (the dev server has no `process`; production builds never reach it).
  define: { global: 'globalThis', 'process.env.NODE_DEBUG': 'undefined' },
  // `npm run dev` against a local API: VITE_API=http VITE_API_PROXY=http://127.0.0.1:8000 npm run dev
  server: process.env.VITE_API_PROXY ? { proxy: { '/api': process.env.VITE_API_PROXY } } : {},
  preview: {
    headers: { 'Content-Security-Policy': CONTENT_SECURITY_POLICY },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}', 'scripts/*.test.ts'],
    setupFiles: ['src/test/setup.ts'],
  },
});

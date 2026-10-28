import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist', 'playwright-report', 'test-results', 'src/api/generated.ts'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { globals: globals.browser },
    plugins: { 'react-hooks': reactHooks },
    rules: reactHooks.configs.recommended.rules,
  },
  {
    // The e2e specs take `test` from ./returning.ts: its contexts start as a tab that has shown the logo animation (the
    // flag is in session storage, which a Playwright storageState cannot hold). A spec that needs the plain `test` is
    // listed below, with the reason.
    files: ['e2e/**/*.ts'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: '@playwright/test',
              importNames: ['test'],
              message: "Import `test` from './returning' (e2e/returning.ts): a plain context plays the 1.9 MB logo animation.",
            },
          ],
        },
      ],
    },
  },
  {
    files: [
      'e2e/returning.ts', // extends the plain test
      'e2e/intro.spec.ts', // the animation itself: every test starts in a fresh tab
      'e2e/lighthouse.spec.ts', // makes its own persistent context
      'e2e/parity/**/*.ts', // makes its own contexts (new, lighthouse) or runs against the earlier version
      'e2e/live/**/*.ts', // makes its own context
    ],
    rules: { 'no-restricted-imports': 'off' },
  },
);

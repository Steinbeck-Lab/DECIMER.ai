// Self-hosted fonts (the CSP allows no font CDN): Wix Madefor Display for headlines and big numbers, Wix Madefor Text for
// the interface and names, Geist Mono for labels, measurements and SMILES (a mono where 1 and l, 0 and O are told apart).
// Latin subsets only.
import '@fontsource/wix-madefor-display/latin-400.css';
import '@fontsource/wix-madefor-display/latin-500.css';
import '@fontsource/wix-madefor-text/latin-400.css';
import '@fontsource/wix-madefor-text/latin-400-italic.css';
import '@fontsource/wix-madefor-text/latin-500.css';
import '@fontsource/wix-madefor-text/latin-600.css';
import '@fontsource/geist-mono/latin-400.css';
// Only the Mantine styles of the components used (Menu, Tooltip, Modal): the full sheet is 295 KB.
import '@mantine/core/styles/default-css-variables.css';
import '@mantine/core/styles/baseline.css';
import '@mantine/core/styles/global.css';
import '@mantine/core/styles/UnstyledButton.css';
import '@mantine/core/styles/Paper.css';
import '@mantine/core/styles/Popover.css';
import '@mantine/core/styles/Menu.css';
import '@mantine/core/styles/Tooltip.css';
import '@mantine/core/styles/Overlay.css';
import '@mantine/core/styles/ModalBase.css';
import '@mantine/core/styles/Modal.css';
import '@mantine/core/styles/CloseButton.css';
import './styles/base.css';
import './styles/shell.css';
import './styles/home.css';
import './styles/run.css';
import './styles/figure.css';
import './styles/editor.css';
import './styles/pages.css';
import { MantineProvider } from '@mantine/core';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { startMatomo } from './analytics/matomo';
import { createApiClient } from './api';
import { AppProvider } from './state/react';
import { strings } from './strings';
import { cssVariablesResolver, theme } from './theme';

// Error reports are off unless the build names a GlitchTip (VITE_SENTRY_DSN, env.d.ts). Vite replaces the variable, so a
// build without one has no reporting code at all; with one, the SDK is a chunk of its own, off the way of the first paint.
if (import.meta.env.VITE_SENTRY_DSN) {
  import('./observability/sentry').then(
    (reporting) => reporting.startErrorReporting(),
    () => {}, // the chunk did not load (offline): the page does not need it
  );
}
startMatomo();

const root = document.getElementById('root');
if (!root) throw new Error('#root element missing');
const reactRoot = createRoot(root);

// One client for the page, chosen by VITE_API (src/api/index.ts).
createApiClient().then(
  (api) =>
    reactRoot.render(
      <StrictMode>
        <MantineProvider theme={theme} cssVariablesResolver={cssVariablesResolver} forceColorScheme="light">
          <AppProvider api={api}>
            <App />
          </AppProvider>
        </MantineProvider>
      </StrictMode>,
    ),
  (error: unknown) => {
    console.error(error);
    reactRoot.render(<p className="dc-startup-error">{strings.startupFailed}</p>);
  },
);

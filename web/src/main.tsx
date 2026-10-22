// Self-hosted fonts (the CSP allows no font CDN): Archivo for compound numbers and titles,
// Source Sans 3 for the interface, JetBrains Mono for SMILES. Latin subsets only.
import '@fontsource/archivo/latin-800.css';
import '@fontsource/jetbrains-mono/latin-400.css';
import '@fontsource/source-sans-3/latin-400.css';
import '@fontsource/source-sans-3/latin-400-italic.css';
import '@fontsource/source-sans-3/latin-500.css';
import '@fontsource/source-sans-3/latin-600.css';
import '@fontsource/source-sans-3/latin-700.css';
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
import './styles/figure.css';
import './styles/editor.css';
import './styles/pages.css';
import { MantineProvider } from '@mantine/core';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { createApiClient } from './api';
import { AppProvider } from './state/react';
import { strings } from './strings';
import { cssVariablesResolver, theme } from './theme';

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

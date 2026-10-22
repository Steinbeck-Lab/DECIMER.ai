import { Center, Loader, Stack, Text } from '@mantine/core';
import { lazy, Suspense } from 'react';
import { strings } from '../strings';
import type { KetcherEditorProps } from './KetcherEditor';

// Loaded on first use only: the editor and its 11.8 MB Indigo WASM must not
// slow down the upload page.
const KetcherEditor = lazy(() => import('./KetcherEditor'));

export function KetcherPanel(props: KetcherEditorProps) {
  return (
    <Suspense
      fallback={
        <Center h="100%">
          <Stack align="center">
            <Loader />
            <Text size="sm">{strings.editorLoading}</Text>
          </Stack>
        </Center>
      }
    >
      <KetcherEditor {...props} />
    </Suspense>
  );
}

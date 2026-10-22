// Phase F Ketcher spike. Throwaway page: proves one editor can be
// reused across results without cross-talk, under the production CSP.
import '@mantine/core/styles.css';
import { Badge, Button, Card, Grid, Group, MantineProvider, Stack, Text } from '@mantine/core';
import type { Ketcher } from 'ketcher-core';
import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { KetcherPanel } from '../editor/KetcherPanel';
import { serial } from '../editor/serial';

const RESULTS = [
  { name: 'caffeine', smiles: 'CN1C=NC2=C1C(=O)N(C(=O)N2C)C' },
  { name: 'aspirin', smiles: 'CC(=O)OC1=CC=CC=C1C(=O)O' },
  { name: 'benzene', smiles: 'C1=CC=CC=C1' },
];

interface SpikeApi {
  ready: boolean;
  editorReadyMs?: number;
  select: (index: number) => Promise<void>;
  current: () => number | null;
  stored: () => Record<number, string>;
  ketcher?: Ketcher;
}
declare global {
  interface Window {
    __spike: SpikeApi;
  }
}

function Spike() {
  const ketcherRef = useRef<Ketcher | null>(null);
  const store = useRef<Record<number, string>>({});
  const current = useRef<number | null>(null);
  const run = useRef(serial());
  const editClickedAt = useRef(0);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  const select = (index: number) =>
    run.current(async () => {
      const ketcher = ketcherRef.current;
      if (!ketcher) throw new Error('editor not ready');
      if (current.current !== null) store.current[current.current] = await ketcher.getMolfile();
      const result = RESULTS[index];
      if (!result) throw new Error(`no result ${index}`);
      await ketcher.setMolecule(store.current[index] ?? result.smiles);
      current.current = index;
      setSelected(index);
    });

  useEffect(() => {
    window.__spike = {
      ...window.__spike,
      select,
      current: () => current.current,
      stored: () => ({ ...store.current }),
    };
  });

  return (
    <Grid p="md">
      <Grid.Col span={4}>
        <Stack>
          {RESULTS.map((r, i) => (
            <Card key={r.name} withBorder>
              <Group justify="space-between">
                <Text fw={600}>{r.name}</Text>
                {selected === i && <Badge>editing</Badge>}
              </Group>
              <Button
                mt="sm"
                size="xs"
                data-testid={`edit-${i}`}
                onClick={() => {
                  if (!open) {
                    editClickedAt.current = performance.now();
                    setOpen(true);
                  } else {
                    void select(i);
                  }
                }}
              >
                Edit
              </Button>
            </Card>
          ))}
        </Stack>
      </Grid.Col>
      <Grid.Col span={8}>
        <div style={{ height: 600, border: '1px solid var(--mantine-color-gray-4)' }}>
          {open && (
            <KetcherPanel
              onReady={(ketcher) => {
                ketcherRef.current = ketcher;
                window.__spike = {
                  ...window.__spike,
                  ketcher,
                  ready: true,
                  editorReadyMs: performance.now() - editClickedAt.current,
                };
              }}
            />
          )}
        </div>
      </Grid.Col>
    </Grid>
  );
}

window.__spike = {
  ready: false,
  select: () => Promise.reject(new Error('not mounted')),
  current: () => null,
  stored: () => ({}),
};
const root = document.getElementById('root');
if (!root) throw new Error('#root element missing');
createRoot(root).render(
  <MantineProvider>
    <Spike />
  </MantineProvider>,
);

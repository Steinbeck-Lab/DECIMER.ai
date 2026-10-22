// Shape of the hooks the spike page exposes on window (src/spike/main.tsx).
interface SpikeKetcher {
  getSmiles(): Promise<string>;
  getMolfile(): Promise<string>;
  addFragment(structure: string): Promise<void>;
}
interface Window {
  __spike: {
    ready: boolean;
    editorReadyMs?: number;
    select: (index: number) => Promise<void>;
    current: () => number | null;
    stored: () => Record<number, string>;
    ketcher?: SpikeKetcher;
  };
}

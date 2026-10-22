import 'ketcher-react/dist/index.css';
import type { Ketcher } from 'ketcher-core';
import { Editor } from 'ketcher-react';
import { StandaloneStructServiceProvider } from 'ketcher-standalone/dist/binaryWasm';

// One provider for the whole page: a second Indigo service is what makes
// several editors receive each other's results (Ketcher #12200).
const structServiceProvider = new StandaloneStructServiceProvider();

export interface KetcherEditorProps {
  onReady: (ketcher: Ketcher) => void;
}

export default function KetcherEditor({ onReady }: KetcherEditorProps) {
  return (
    <Editor
      staticResourcesUrl=""
      structServiceProvider={structServiceProvider}
      errorHandler={(message) => console.error('Ketcher:', message)}
      buttons={{ recognize: { hidden: true }, miew: { hidden: true } }}
      disableMacromoleculesEditor
      onInit={onReady}
    />
  );
}

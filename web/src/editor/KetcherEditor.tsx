import 'ketcher-react/dist/index.css';
import type { Ketcher, StructServiceProvider } from 'ketcher-core';
import { Editor } from 'ketcher-react';
import { StandaloneStructServiceProvider } from 'ketcher-standalone/dist/binaryWasm';
import { serviceActivity, trackCalls } from './serviceActivity';

// One provider for the whole page: a second Indigo service is what makes
// several editors receive each other's results (Ketcher #12200).
const indigo = new StandaloneStructServiceProvider();
// Its service reports every call to `serviceActivity`, so the session can wait for a Layout that is still running.
const structServiceProvider: StructServiceProvider = {
  mode: indigo.mode,
  createStructService: (options) => trackCalls(indigo.createStructService(options), serviceActivity),
};

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

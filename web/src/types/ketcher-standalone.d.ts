// ketcher-standalone ships types for its binaryWasm build but does not list
// them under "exports", so TypeScript cannot find them on its own.
declare module 'ketcher-standalone/dist/binaryWasm' {
  export * from 'ketcher-standalone';
}

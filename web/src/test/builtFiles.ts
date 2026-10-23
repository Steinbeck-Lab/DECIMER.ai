/**
 * The files of a built directory (dist/), each read as text, for the release gate (release.test.ts).
 *
 * They are read with Node's file system, not with `import.meta.glob(..., { query: '?raw' })`: a glob sends every file
 * through Vite's module pipeline, which parses a `.json` as JSON and so throws on one that is not (a build can hold
 * one that is binary or cut off), and the gate then fails with a parse error that names no marker. Here a file is
 * bytes: read as Latin-1, so that nothing can fail to decode, and a marker (plain ASCII) is found in it wherever it
 * is, in a script, a page or a data file. The only error is a directory that is not there.
 */
type Entry = { name: string; isDirectory(): boolean; isFile(): boolean };
type Fs = {
  readdirSync(dir: string, options: { withFileTypes: true }): Entry[];
  readFileSync(file: string, encoding: 'latin1'): string;
};

// By a name the type checker does not resolve: src/ has no Node types (tsconfig.app.json), and the bundler must leave
// the module alone. Only tests import this file, and they run under Node.
const nodeModule = (name: string): Promise<unknown> => import(/* @vite-ignore */ name);

/** Every file under the directory `root`, by its path relative to it (`assets/app.js`), as text. Throws if `root` cannot be read. */
export async function readBuiltFiles(root: string): Promise<Map<string, string>> {
  const fs = (await nodeModule('node:fs')) as Fs;
  const files = new Map<string, string>();
  const walk = (dir: string, prefix: string): void => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) walk(`${dir}/${entry.name}`, `${prefix}${entry.name}/`);
      else if (entry.isFile()) files.set(`${prefix}${entry.name}`, fs.readFileSync(`${dir}/${entry.name}`, 'latin1'));
    }
  };
  walk(root.replace(/\/+$/, ''), '');
  return files;
}

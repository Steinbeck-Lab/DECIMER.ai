/**
 * Molfiles from Ketcher start with a title line, a program/timestamp line and a
 * comment line. The timestamp changes every minute, so two reads of the same
 * structure can differ there. Compare the rest only.
 */
export function normaliseMolfile(molfile: string): string {
  return molfile.replace(/\r\n?/g, '\n').split('\n').slice(3).join('\n').trimEnd();
}

export function sameStructure(a: string, b: string): boolean {
  return normaliseMolfile(a) === normaliseMolfile(b);
}

import { strings } from '../strings';
import type { ResultView } from '../state/selectors';
import { staleLabel, validityHover } from '../ui/format';
import { CopyButton } from './CopyButton';

/**
 * What the panel shows of the SMILES. While the server has not answered an edit nothing is known about the new
 * structure. A structure that RDKit refused and only CDK read (decision D4) has no valid SMILES in RDKit's sense, but
 * DECIMER's own text is what the user may want to copy, so it is shown with a note on who accepted it. A structure
 * nobody accepts has none.
 */
export function smilesLine(view: Pick<ResultView, 'stale' | 'validating' | 'valid' | 'validBy' | 'smiles'>): {
  text: string;
  /** Empty when there is nothing to copy. */
  copy: string;
  /** Lines under the SMILES about who accepted it. */
  notes: string[];
} {
  if (view.stale) return { text: staleLabel(view.validating), copy: '', notes: [] };
  if (view.valid && view.smiles) return { text: view.smiles, copy: view.smiles, notes: [] };
  if (view.validBy === 'cdk' && view.smiles) return { text: view.smiles, copy: view.smiles, notes: validityHover(view.validBy) };
  return { text: strings.editor.noSmiles, copy: '', notes: [] };
}

/** The SMILES and the molfile of the structure in the panel, each with its copy button. */
export function StructureCode({ view }: { view: ResultView }) {
  const line = smilesLine(view);
  return (
    <div className="dc-code" aria-busy={view.validating}>
      <div className="dc-code__head">
        <span className="dc-code__label" id="dc-smiles-label">
          {strings.editor.smiles}
        </span>
        <CopyButton value={line.copy} label={strings.editor.copySmiles} />
      </div>
      <code className="dc-code__text" aria-labelledby="dc-smiles-label" data-stale={view.stale}>
        {line.text}
      </code>
      {line.notes.map((note) => (
        <p key={note} className="dc-code__note">
          {note}
        </p>
      ))}
      <p className="dc-code__status" role="status">
        {view.validating && strings.editor.updating}
        {view.validationFailed && strings.editor.updateFailed}
      </p>
      <details className="dc-molfile">
        <summary>{strings.editor.molfile}</summary>
        <div className="dc-molfile__head">
          <CopyButton value={view.molfile} label={strings.editor.copyMolfile} />
        </div>
        <pre className="dc-molfile__text">{view.molfile}</pre>
      </details>
    </div>
  );
}

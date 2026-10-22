import type { ApiErrorCode, Job, ValidateResponse } from '../api/contract';
import type { SelectionError } from '../upload/files';

export type Phase = 'empty' | 'working' | 'results' | 'failed';
export type Filter = 'all' | 'review' | 'edited';

/** What the user sees for one result. One status per result, by priority: checked, edited, not sure, review, unchecked. */
export type Status = 'checked' | 'edited' | 'review' | 'notsure' | 'unchecked';

export type Problem =
  | { kind: 'selection'; error: SelectionError }
  | { kind: 'api'; code: ApiErrorCode; /** `detail.message` from the server, if any. */ message: string | null };

export type Failure = { kind: 'job'; message: string | null } | { kind: 'connection' };

export interface SourceFile {
  name: string;
  size: number;
  /** Object URL of an image preview (pasted or uploaded images only). */
  previewUrl?: string;
}

/** The values the server recomputes after an edit. */
export type Derived = ValidateResponse;

/** What the user changed on one result. Only touched results have an entry. */
export interface EditEntry {
  /** The edited molfile, null when the result was only marked as checked. */
  molfile: string | null;
  modified: boolean;
  checked: boolean;
  /** Server values for `molfile`; null until the first validate answer. */
  derived: Derived | null;
  validating: boolean;
  validationFailed: boolean;
}

export interface Notice {
  id: number;
  kind: 'info' | 'error';
  text: string;
}

export interface State {
  phase: Phase;
  /** Shown on the empty screen: why the last upload was refused. */
  problem: Problem | null;
  failure: Failure | null;
  sources: SourceFile[];
  startedAt: number | null;
  jobId: string | null;
  job: Job | null;
  /** Keyed by Result.index. */
  edits: Record<number, EditEntry>;
  /** Result.index of the structure open in the editor. */
  selected: number | null;
  /**
   * The editor has been opened once. It then stays mounted for the life of the
   * page, because Ketcher's workers cannot be destroyed.
   */
  editorMounted: boolean;
  filter: Filter;
  /** Results whose problem report was sent. */
  reported: Record<number, boolean>;
  /** An export is running. Both Download menus are off until it ends. */
  exporting: boolean;
  notice: Notice | null;
}

export const initialState: State = {
  phase: 'empty',
  problem: null,
  failure: null,
  sources: [],
  startedAt: null,
  jobId: null,
  job: null,
  edits: {},
  selected: null,
  editorMounted: false,
  filter: 'all',
  reported: {},
  exporting: false,
  notice: null,
};

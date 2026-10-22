import type { Job } from '../api/contract';
import {
  initialState,
  type Derived,
  type EditEntry,
  type Filter,
  type Notice,
  type Problem,
  type SourceFile,
  type State,
} from './model';

export type Action =
  | { type: 'submit'; sources: SourceFile[]; at: number }
  | { type: 'jobCreated'; id: string }
  | { type: 'rejected'; problem: Problem }
  | { type: 'jobUpdated'; job: Job }
  | { type: 'connectionLost' }
  | { type: 'jobExpired' }
  | { type: 'resume' }
  | { type: 'startOver' }
  | { type: 'dismissProblem' }
  | { type: 'select'; index: number | null }
  | { type: 'filter'; filter: Filter }
  | { type: 'edit'; index: number; molfile: string }
  | { type: 'reset'; index: number }
  | { type: 'check'; index: number; checked: boolean }
  | { type: 'validated'; index: number; molfile: string; derived: Derived }
  | { type: 'validationFailed'; index: number; molfile: string }
  | { type: 'reported'; index: number }
  | { type: 'exporting'; value: boolean }
  | { type: 'notice'; notice: Notice | null };

function withEdit(state: State, index: number, entry: EditEntry | null): State {
  const edits = { ...state.edits };
  if (entry) edits[index] = entry;
  else delete edits[index];
  return { ...state, edits };
}

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'submit':
      return {
        ...initialState,
        editorMounted: state.editorMounted,
        phase: 'working',
        sources: action.sources,
        startedAt: action.at,
      };

    case 'jobCreated':
      return state.phase === 'working' && state.jobId === null ? { ...state, jobId: action.id } : state;

    case 'rejected':
      // A refused upload leaves the user where they were: the empty screen with a message.
      return { ...initialState, editorMounted: state.editorMounted, problem: action.problem };

    case 'jobUpdated': {
      if (state.jobId !== action.job.id || state.phase !== 'working') return state;
      const { job } = action;
      if (job.status === 'done') return { ...state, job, phase: 'results' };
      if (job.status === 'failed') {
        return { ...state, job, phase: 'failed', failure: { kind: 'job', message: job.error ?? null } };
      }
      return { ...state, job };
    }

    case 'connectionLost':
      return state.phase === 'working'
        ? { ...state, phase: 'failed', failure: { kind: 'connection' } }
        : state;

    case 'jobExpired':
      return state.phase === 'working' ? { ...state, phase: 'failed', failure: { kind: 'expired' } } : state;

    // Back to the job screen on the same job (the id and the last answer were kept), only from a lost connection.
    case 'resume':
      return state.phase === 'failed' && state.failure?.kind === 'connection' && state.jobId !== null
        ? { ...state, phase: 'working', failure: null }
        : state;

    case 'startOver':
      return { ...initialState, editorMounted: state.editorMounted };

    case 'dismissProblem':
      return state.problem ? { ...state, problem: null } : state;

    case 'select':
      if (state.selected === action.index) return state;
      return { ...state, selected: action.index, editorMounted: state.editorMounted || action.index !== null };

    case 'filter':
      return state.filter === action.filter ? state : { ...state, filter: action.filter };

    case 'edit': {
      const previous = state.edits[action.index];
      if (previous?.modified && previous.molfile === action.molfile) return state;
      return withEdit(state, action.index, {
        molfile: action.molfile,
        modified: true,
        // Checking vouched for the earlier content, so a new edit has to be checked again.
        checked: false,
        derived: previous?.derived ?? null,
        validating: true,
        validationFailed: false,
      });
    }

    case 'reset':
      return state.edits[action.index] ? withEdit(state, action.index, null) : state;

    case 'check': {
      const previous = state.edits[action.index];
      if (action.checked) {
        if (previous?.checked) return state;
        return withEdit(state, action.index, {
          molfile: previous?.molfile ?? null,
          modified: previous?.modified ?? false,
          derived: previous?.derived ?? null,
          validating: previous?.validating ?? false,
          validationFailed: previous?.validationFailed ?? false,
          checked: true,
        });
      }
      if (!previous?.checked) return state;
      return previous.modified
        ? withEdit(state, action.index, { ...previous, checked: false })
        : withEdit(state, action.index, null);
    }

    case 'validated': {
      const entry = state.edits[action.index];
      // An answer for an older edit is dropped: the newer one is still on its way.
      if (!entry || entry.molfile !== action.molfile) return state;
      return withEdit(state, action.index, {
        ...entry,
        derived: action.derived,
        validating: false,
        validationFailed: false,
      });
    }

    case 'validationFailed': {
      const entry = state.edits[action.index];
      if (!entry || entry.molfile !== action.molfile) return state;
      return withEdit(state, action.index, { ...entry, validating: false, validationFailed: true });
    }

    case 'reported':
      return { ...state, reported: { ...state.reported, [action.index]: true } };

    case 'exporting':
      return state.exporting === action.value ? state : { ...state, exporting: action.value };

    case 'notice':
      return { ...state, notice: action.notice };
  }
}

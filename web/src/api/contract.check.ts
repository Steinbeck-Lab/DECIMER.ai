/**
 * Compile-time checks of what the UI assumes about the API types generated from its OpenAPI schema. There is
 * nothing to run: if a regenerated `generated.ts` breaks one of these, `npm run typecheck` fails here, at the
 * assumption, instead of somewhere in the UI. Fix the UI (or this file, on purpose) and keep the check.
 */
import type { components } from './generated';
import type {
  ExportFormat,
  ExportRequest,
  Job,
  JobStatus,
  Result,
  Stage,
  StageName,
  StageState,
  ValidateRequest,
  ValidateResponse,
} from './contract';
import { STAGE_ORDER } from './contract';

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
type Assert<T extends true> = T;

type Schemas = components['schemas'];

export type Checks = [
  // The states the UI has a branch for. A new one in the API must be handled before it is accepted here.
  Assert<Equal<JobStatus, 'queued' | 'running' | 'done' | 'failed'>>,
  Assert<Equal<StageState, 'pending' | 'active' | 'done'>>,
  Assert<Equal<StageName, 'upload' | 'render' | 'segment' | 'recognise' | 'check'>>,
  // STAGE_ORDER lists every stage the API has.
  Assert<Equal<(typeof STAGE_ORDER)[number], StageName>>,
  Assert<Equal<Stage, { name: StageName; state: StageState }>>,
  // Nullable fields the UI treats as "absent", not as zero or false.
  Assert<Equal<Job['queuePosition'], number | null>>,
  Assert<Equal<Job['error'], string | null>>,
  // One URL per page of the upload, in page order, for the "Display uploaded document" strip.
  Assert<Equal<Job['pageUrls'], string[]>>,
  Assert<Equal<Result['isStructure'], boolean | null>>,
  Assert<Equal<Result['name'], string | null>>,
  // Who accepted the SMILES: the tile and the editor have wording for exactly these (strings.ts, `cdkOnly`).
  Assert<Equal<Result['validBy'], 'rdkit' | 'cdk' | null>>,
  Assert<Equal<Result['nameTier'], string | null>>,
  Assert<Equal<Result['nameVerified'], string | null>>,
  // The validate answer carries the name fields exactly as a result does, so the two cannot drift.
  Assert<Equal<Pick<ValidateResponse, 'name' | 'nameTier' | 'nameVerified'>, Pick<Result, 'name' | 'nameTier' | 'nameVerified'>>>,
  // Every field of the validate answer, so that a new one is noticed here and not in a reducer.
  Assert<Equal<keyof ValidateResponse, 'smiles' | 'inchikey' | 'svg' | 'valid' | 'name' | 'nameTier' | 'nameVerified'>>,
  Assert<Equal<ValidateRequest, { molfile: string }>>,
  // The formats the Download menu has an item for; a new one in the API needs one.
  Assert<Equal<ExportFormat, 'zip' | 'sdf' | 'csv'>>,
  // What the UI sends (`buildExportRequest`) is what the API takes.
  Assert<ExportRequest extends Schemas['ExportRequest'] ? true : false>,
  Assert<Equal<keyof Schemas['ExportRequest'], keyof ExportRequest>>,
  // The result list is the UI's Result.
  Assert<Equal<Job['results'], Result[]>>,
  // Everything the generated result has is in the UI's, under the same name.
  Assert<keyof Schemas['ResultView'] extends keyof Result ? true : false>,
  Assert<keyof Schemas['JobView'] extends keyof Job ? true : false>,
  // A result the API sends is the UI's, with the fields it may leave out filled in.
  Assert<Required<Schemas['ResultView']> extends Result ? true : false>,
];

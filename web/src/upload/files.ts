import { LIMITS } from '../api/contract';
import { strings } from '../strings';

export type FileKind = 'pdf' | 'image';

/** Passed to react-dropzone, and so to the browser's file picker. */
export const ACCEPT = {
  'application/pdf': ['.pdf'],
  'image/png': ['.png'],
  'image/jpeg': ['.jpg', '.jpeg'],
  'image/webp': ['.webp'],
  'image/heic': ['.heic'],
  'image/heif': ['.heif'],
} as const;

const IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'webp', 'heic', 'heif'];
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/heic', 'image/heif'];

export type SelectionError =
  | { code: 'none' }
  | { code: 'tooMany'; max: number }
  | { code: 'format'; names: string[] }
  | { code: 'mixed' }
  | { code: 'manyPdfs' }
  | { code: 'size'; megabytes: number };

export type Selection =
  | { ok: true; files: File[]; kind: FileKind }
  | { ok: false; error: SelectionError };

type FileLike = Pick<File, 'name' | 'type'>;

/**
 * The extension decides first (browsers often report an empty type for HEIC),
 * the MIME type is the fallback. The server checks magic bytes again.
 */
export function kindOf(file: FileLike): FileKind | null {
  const dot = file.name.lastIndexOf('.');
  const extension = dot >= 0 ? file.name.slice(dot + 1).toLowerCase() : '';
  if (extension === 'pdf') return 'pdf';
  if (IMAGE_EXTENSIONS.includes(extension)) return 'image';
  if (!extension) {
    if (file.type === 'application/pdf') return 'pdf';
    if (IMAGE_TYPES.includes(file.type)) return 'image';
  }
  return null;
}

/** Legacy rules (ROADMAP §3, §6 item 4): one PDF or some images, never both; 100 MB in total. */
export function checkSelection(files: readonly File[]): Selection {
  if (files.length === 0) return { ok: false, error: { code: 'none' } };
  if (files.length > LIMITS.maxUploadFiles) {
    return { ok: false, error: { code: 'tooMany', max: LIMITS.maxUploadFiles } };
  }

  const unsupported = files.filter((f) => kindOf(f) === null).map((f) => f.name);
  if (unsupported.length > 0) return { ok: false, error: { code: 'format', names: unsupported } };

  const pdfs = files.filter((f) => kindOf(f) === 'pdf').length;
  if (pdfs > 0 && pdfs < files.length) return { ok: false, error: { code: 'mixed' } };
  if (pdfs > 1) return { ok: false, error: { code: 'manyPdfs' } };

  const bytes = files.reduce((sum, f) => sum + f.size, 0);
  if (bytes > LIMITS.maxUploadBytes) {
    return { ok: false, error: { code: 'size', megabytes: Math.ceil(bytes / 1024 / 1024) } };
  }
  return { ok: true, files: [...files], kind: pdfs > 0 ? 'pdf' : 'image' };
}

export function describeSelectionError(error: SelectionError): string {
  switch (error.code) {
    case 'none':
      return strings.upload.errorNone;
    case 'tooMany':
      return strings.upload.errorTooMany(error.max);
    case 'format':
      return strings.upload.errorFormat(error.names);
    case 'mixed':
      return strings.upload.errorMixed;
    case 'manyPdfs':
      return strings.upload.errorManyPdfs;
    case 'size':
      return strings.upload.errorSize(error.megabytes);
  }
}

const EXTENSION_FOR_TYPE: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/webp': 'webp',
  'image/heic': 'heic',
  'image/heif': 'heif',
};

/**
 * Image files on the clipboard (at most `limit`). Text, HTML and anything that is not an image
 * return an empty list, so the caller leaves the paste alone. Screenshots arrive
 * as a nameless `image.png`; they get a readable name instead.
 */
export function imagesFromClipboard(data: Pick<DataTransfer, 'items' | 'files'> | null, limit = Infinity): File[] {
  if (!data) return [];
  const found: File[] = [];
  for (const item of Array.from(data.items ?? [])) {
    if (item.kind !== 'file' || !item.type.startsWith('image/')) continue;
    const file = item.getAsFile();
    if (file) found.push(file);
  }
  if (found.length === 0) {
    // Some browsers only fill `files`.
    for (const file of Array.from(data.files ?? [])) {
      if (file.type.startsWith('image/')) found.push(file);
    }
  }
  return found.slice(0, limit).map((file, i, kept) => {
    if (file.name && file.name !== 'image.png') return file;
    const extension = EXTENSION_FOR_TYPE[file.type] ?? 'png';
    const name = `${strings.upload.pastedName}${kept.length > 1 ? `-${i + 1}` : ''}.${extension}`;
    return new File([file], name, { type: file.type, lastModified: file.lastModified });
  });
}

/**
 * What may leave the browser in an error report The reports go to a self-hosted GlitchTip, but they are
 * kept for 90 days, so they follow the rule of the privacy text: no structure, no file, no address. The API has the
 * same scrubber in Python (api/src/decimer_api/scrub.py); the two are kept in step on purpose.
 *
 * - `scrubEvent` is the SDK's `beforeSend`. It is an ALLOWLIST: it builds a new event from the few fields that are
 *   needed to read a crash (the exception types and messages, the stack as function names and line numbers, the SDK
 *   version) and drops everything else, so a field a newer SDK adds cannot leak by default.
 * - `scrubText` cleans the free text that is kept (messages, exception values, tags): a molfile (the whole text goes),
 *   file names, job ids, IP addresses and SMILES strings are replaced by a placeholder. It is a best-effort pattern
 *   match, because a message can carry anything; the allowlist is what keeps the rest out, and the app does not put
 *   structures into messages.
 *
 * Pure: no SDK code is imported (types only), no DOM, no global state. The expressions are plain (no look-behind:
 * a browser that lacks it would fail the whole module, and with it the app).
 */
import type { Event, EventHint } from '@sentry/browser';

export const MOLFILE_REMOVED = '[molfile removed]';
export const FILE = '[file]';
export const IP = '[ip]';
export const SMILES = '[smiles]';
export const JOB_ID = ':id';
export const SCRUB_FAILED = '[scrub failed]';
/** A text longer than this is cut before the patterns run: an error text can hold a whole upload. */
export const MAX_TEXT_CHARS = 20_000;
export const TRUNCATED = '...[truncated]';
/** The only request headers that are kept (compared in lower case). */
export const SAFE_HEADERS: ReadonlySet<string> = new Set(['content-type', 'content-length']);
/** The only contexts that are kept, and the only entries of them. */
export const CONTEXTS_KEPT = ['runtime', 'os'] as const;
const CONTEXT_ENTRIES_KEPT = ['name', 'version'] as const;

// --- patterns ---------------------------------------------------------------------------------------------------
// (JS `\b`, `\w` and `\d` are ASCII only, which is what these patterns mean.)

const MOLFILE = /M {2}END|V[23]000|\$\$\$\$/;
const FILE_EXTENSIONS =
  '(?:pdf|png|jpe?g|jfif|webp|heic|heif|avif|gif|tiff?|bmp|svg|eps|mol2?|sdf|smi|smiles|cdx(?:ml)?|ket|cml|rxn|rdf|zip|txt|csv|docx?|pptx?|xlsx?|odt|rtf)';

/** What a token may not contain: white space, quotes of every kind, brackets that are not SMILES, separators, and the punctuation that ends a sentence or joins a query (`!`, `?`, `&`). */
const DELIMITERS = '\\s"\'`<>,;|{}!?&\u00ab\u00bb\u201c\u201d\u2018\u2019';

/** `scheme://host/path?query#fragment` in a text: the query string and the fragment go (`?smiles=CCO`). */
const URL_QUERY = /\b([a-z][a-z0-9+.-]{0,15}:\/\/[^\s"'<>?#]*)[?#][^\s"'<>]*/gi;
/** ... also without a scheme (`/api/x?smiles=CCO`, `host:8000/x?a=b`): a word with a `/` and then a `?`. A SMILES has `/` but never `?`. */
const WORD = /[^\s"'<>]+/g;
/** A quoted name may hold spaces (`'Zou et al 2024 final.pdf'`). Quotes of every kind a message may use. */
const QUOTES = [
  ['"', '"'],
  ["'", "'"],
  ['`', '`'],
  ['\u00ab', '\u00bb'],
  ['\u201c', '\u201d'],
  ['\u2018', '\u2019'],
] as const;
const QUOTED_FILES = QUOTES.map(([open, close]) => new RegExp(`${open}[^${close}\\n]{1,200}?\\.${FILE_EXTENSIONS}${close}`, 'gi'));
/**
 * A bare name, with whatever directory is in front of it. It starts at the start of a token (the first group is the
 * delimiter in front, kept), so a long token with no name in it is read once, not once per character. The name may be
 * followed by a full stop that ends a sentence (`could not read x.pdf. Then`), but not by a word or by a dot and more
 * (`x.pdf.bak`, `x.pdf.<address>`: what comes after could still change in a later step, and the result must not).
 */
const FILE_TOKEN = new RegExp(`(^|[${DELIMITERS}])[^${DELIMITERS}]*[^${DELIMITERS}/\\\\]\\.${FILE_EXTENSIONS}(?!\\w)(?!\\.\\S)`, 'gi');
const JOB_ID_PATTERN = /\b[0-9a-f]{32}\b/gi;
const IPV4 = /\b(?:\d{1,3}\.){3}\d{1,3}\b/g;
/** Candidates only: `isIPv6` decides (a time like 12:30:45 or `std::vector` is not an address). */
const IPV6 = /(^|[^0-9A-Za-z:.])((?:[0-9A-Fa-f]{0,4}:){2,8}[0-9A-Fa-f]{0,4})(?![0-9A-Za-z:.])/g;

// SMILES. A token is a SMILES when all of it is made of SMILES pieces. Bracket atoms follow the real grammar (isotope,
// symbol, chirality, H count, charge, class), so `[ip]` and `[file]`, our own placeholders, are not taken for atoms.
const BRACKET_ATOM = String.raw`\[\d*(?:[A-Z][a-z]?|se|as|te|[bcnops]|\*)(?:@@?)?(?:H\d*)?(?:[+-]{1,2}\d*)?(?::\d+)?\]`;
const ORGANIC_ATOM = 'Br|Cl|[BCNOPSFI]|[bcnops]';
const SMILES_PIECE = String.raw`(?:${BRACKET_ATOM}|${ORGANIC_ATOM}|[()=#\-+\\/:~@.*$]|%\d\d|\d)`;
const SMILES_FULL = new RegExp(`^${SMILES_PIECE}+$`);
/** A SMILES starts with an atom: never with a digit or a bond (so not `10s`, `25C`). */
const STARTS_WITH_ATOM = new RegExp(`^(?:${BRACKET_ATOM}|${ORGANIC_ATOM})`);
const MARKER = /[\d[\]()=#@/\\.%*$+:~-]/;
const UPPER_ATOMS = /^(?:Br|Cl|[BCNOPSFI])+$/;
const TOKEN = new RegExp(`[^${DELIMITERS}]+`, 'g');
/** `smiles=CCO`, `x:CC(=O)O`: a name (of one letter too), then the SMILES. */
const KEY_VALUE = /^[A-Za-z_][A-Za-z_0-9]*[=:]([^]+)$/;
const OPENING = '([{';
const CLOSING = ')]}.,:;';
const MAX_NESTING = 3;
const MIN_SMILES_CHARS = 3;
/** A SMILES that starts with a lower-case (aromatic) atom is a ring: `n=1` is a counter, not a molecule. */
const MIN_LOWER_START_CHARS = 5;
/** Upper-case atom letters alone (CCCCO) with no marker: common words such as INFO or SPIN are made of the same letters. */
const MIN_UPPER_SMILES_CHARS = 5;

function looksLikeSmiles(token: string): boolean {
  if (token.length < MIN_SMILES_CHARS || !SMILES_FULL.test(token)) return false;
  if (!STARTS_WITH_ATOM.test(token)) return false; // a date, a version, a number, `10s`: no SMILES starts like that
  if (token >= 'a' && token < '{' && token.length < MIN_LOWER_START_CHARS) return false;
  if (UPPER_ATOMS.test(token)) return token.includes('C') || token.length >= MIN_UPPER_SMILES_CHARS;
  return MARKER.test(token); // a plain lower-case word made of atom letters is a word
}

/**
 * `((CCO)).` as the brackets in front, the middle, and the brackets and punctuation behind, with as few of them taken off
 * as will do: every way to take off up to MAX_NESTING in front and MAX_NESTING + 1 behind. (A bracket atom such as `[Cl-]`
 * ends in a closing bracket: only the candidate that leaves it be is a SMILES.)
 */
function* unwrap(token: string): Generator<[lead: string, core: string, trail: string]> {
  let front = 0;
  while (front < token.length && OPENING.includes(token[front]!)) front++;
  let back = 0;
  while (back < token.length && CLOSING.includes(token[token.length - 1 - back]!)) back++;
  const options: [head: number, tail: number][] = [];
  for (let head = 0; head <= Math.min(front, MAX_NESTING); head++) {
    for (let tail = 0; tail <= Math.min(back, MAX_NESTING + 1); tail++) {
      if (head || tail) options.push([head, tail]);
    }
  }
  options.sort((a, b) => a[0] + a[1] - (b[0] + b[1])); // (stable: fewer in front first among equals)
  for (const [head, tail] of options) {
    if (head + tail < token.length) yield [token.slice(0, head), token.slice(head, token.length - tail), token.slice(token.length - tail)];
  }
}

/**
 * The token with a SMILES in it replaced: all of it, or what is left of it inside brackets (`(CCO).`, `[SMILES:CCO]`), or
 * its value after a name (`id=CCO`, `mol:{smiles=CCO}`), or one part of a path or a URL (`/names/CCO`).
 */
function scrubToken(token: string, depth = 0): string {
  if (looksLikeSmiles(token)) return SMILES;
  if (depth >= MAX_NESTING) return token;
  for (const [lead, core, trail] of unwrap(token)) {
    const inner = scrubToken(core, depth + 1);
    if (inner !== core) return lead + inner + trail;
  }
  const value = KEY_VALUE.exec(token)?.[1];
  if (value !== undefined) {
    const inner = scrubToken(value, depth + 1);
    if (inner !== value) return token.slice(0, token.length - value.length) + inner;
  }
  if (token.includes('/')) {
    const parts = token.split('/');
    const scrubbed = parts.map((part) => scrubToken(part, depth + 1));
    if (scrubbed.some((part, index) => part !== parts[index])) return scrubbed.join('/');
  }
  return token;
}

/** Cuts a word with a `/` in front of its first `?` at the `?` (`/api/x?smiles=CCO`; `really?` and `c1ccccc1/C=C/C?` are not paths). */
function cutQuery(word: string): string {
  const mark = word.indexOf('?');
  return mark > 0 && word.slice(0, mark).includes('/') ? word.slice(0, mark) : word;
}

const HEX_GROUP = /^[0-9A-Fa-f]{1,4}$/;

/** Whether a candidate of the IPv6 pattern is an address: eight groups, or `::` standing for one to all but one. */
function isIPv6(candidate: string): boolean {
  if (!/[0-9A-Fa-f]/.test(candidate)) return false; // "::" on its own is punctuation here, not an address
  const halves = candidate.split('::');
  if (halves.length > 2) return false;
  const groups = halves.map((half) => (half === '' ? [] : half.split(':')));
  if (!groups.every((half) => half.every((group) => HEX_GROUP.test(group)))) return false;
  const count = groups.reduce((sum, half) => sum + half.length, 0);
  return halves.length === 1 ? count === 8 : count <= 7;
}

/** One pass of the patterns, in the order that matters (a file name goes before the structure patterns look at the rest). */
function scrubPass(input: string): string {
  let text = input.replace(URL_QUERY, '$1');
  text = text.replace(WORD, cutQuery);
  for (const quoted of QUOTED_FILES) text = text.replace(quoted, FILE);
  text = text.replace(FILE_TOKEN, (_match, before: string) => before + FILE);
  text = text.replace(JOB_ID_PATTERN, JOB_ID);
  text = text.replace(IPV4, IP);
  text = text.replace(IPV6, (match, before: string, candidate: string) => {
    // `fe80::1: refused`: the colon after the address belongs to the sentence (`::` at the end is part of an address).
    const colon = candidate.endsWith(':') && !candidate.endsWith('::') ? ':' : '';
    return isIPv6(colon ? candidate.slice(0, -1) : candidate) ? before + IP + colon : match;
  });
  return text.replace(TOKEN, (token) => scrubToken(token));
}

/** A pass can leave work for the next: a token's nesting budget is spent, or a quoted span has shrunk under its limit. */
const MAX_PASSES = 4;

function scrubTextUnsafe(input: string): string {
  // (A space in front of the marker: a structure cut in two is still a token of its own, not one glued to the marker.)
  let text = input.length > MAX_TEXT_CHARS ? `${input.slice(0, MAX_TEXT_CHARS)} ${TRUNCATED}` : input;
  if (MOLFILE.test(text)) return MOLFILE_REMOVED;
  for (let pass = 0; pass < MAX_PASSES; pass++) {
    const next = scrubPass(text);
    if (next === text) break;
    text = next;
  }
  return text;
}

/**
 * `text` without molfiles, file names, job ids, IP addresses and SMILES strings (each replaced by a placeholder; a text
 * with a molfile in it is replaced whole). Never throws, and `scrubText(scrubText(x)) === scrubText(x)`.
 */
export function scrubText(text: string): string {
  try {
    return scrubTextUnsafe(text);
  } catch {
    return SCRUB_FAILED; // a pattern bug must not take the report down with it
  }
}

// --- the Sentry event -------------------------------------------------------------------------------------------

type Json = Record<string, unknown>;
const isRecord = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value);
const isString = (value: unknown): value is string => typeof value === 'string';
const isCount = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);

/** Free text of any type: a string is scrubbed as it is, other values as their string form. */
function text(value: unknown): string {
  return scrubText(isString(value) ? value : String(value));
}

/** The URL without its query string and fragment, ids and names scrubbed. */
function stripUrl(url: string): string {
  const cut = url.search(/[?#]/);
  return scrubText(cut === -1 ? url : url.slice(0, cut));
}

function frame(value: unknown): Json {
  if (!isRecord(value)) return {};
  const kept: Json = {};
  for (const key of ['filename', 'abs_path'] as const) {
    const url = value[key];
    if (isString(url)) kept[key] = stripUrl(url);
  }
  for (const key of ['function', 'module'] as const) {
    if (isString(value[key])) kept[key] = value[key];
  }
  for (const key of ['lineno', 'colno'] as const) {
    if (isCount(value[key])) kept[key] = value[key];
  }
  if (typeof value.in_app === 'boolean') kept.in_app = value.in_app;
  return kept;
}

function exceptionValue(value: unknown): Json {
  if (!isRecord(value)) return {};
  const kept: Json = {};
  if (isString(value.type)) kept.type = text(value.type);
  if (value.value !== undefined && value.value !== null) kept.value = text(value.value);
  if (isString(value.module)) kept.module = value.module;
  if (isRecord(value.mechanism)) {
    const mechanism: Json = {};
    if (isString(value.mechanism.type)) mechanism.type = value.mechanism.type;
    if (typeof value.mechanism.handled === 'boolean') mechanism.handled = value.mechanism.handled;
    kept.mechanism = mechanism;
  }
  if (isRecord(value.stacktrace) && Array.isArray(value.stacktrace.frames)) {
    kept.stacktrace = { frames: value.stacktrace.frames.map(frame) };
  }
  return kept;
}

function request(value: Json): Json {
  const kept: Json = {};
  if (isString(value.method)) kept.method = value.method;
  if (isString(value.url)) kept.url = stripUrl(value.url);
  if (isRecord(value.headers)) {
    const headers: Json = {};
    for (const [name, header] of Object.entries(value.headers)) {
      if (SAFE_HEADERS.has(name.toLowerCase()) && header !== undefined && header !== null) headers[name] = text(header);
    }
    kept.headers = headers;
  }
  return kept;
}

function scrubEventUnsafe(event: Json): Json {
  const kept: Json = {};
  for (const key of ['event_id', 'platform', 'level', 'release', 'environment', 'type'] as const) {
    if (isString(event[key])) kept[key] = event[key];
  }
  if (isCount(event.timestamp)) kept.timestamp = event.timestamp;
  if (Array.isArray(event.fingerprint)) kept.fingerprint = event.fingerprint.filter(isString);
  if (isRecord(event.sdk)) {
    const sdk: Json = {};
    for (const key of ['name', 'version'] as const) {
      if (isString(event.sdk[key])) sdk[key] = event.sdk[key];
    }
    if (isRecord(event.sdk.settings)) {
      // `infer_ip: 'never'` tells the server not to take the address from the connection.
      sdk.settings = Object.fromEntries(
        Object.entries(event.sdk.settings).filter(([, value]) => isString(value) || typeof value === 'boolean'),
      );
    }
    kept.sdk = sdk;
  }
  if (isString(event.logger)) kept.logger = event.logger;
  if (event.message !== undefined && event.message !== null) kept.message = text(event.message);
  if (isRecord(event.logentry)) {
    const formatted = event.logentry.formatted || event.logentry.message;
    kept.logentry = formatted !== undefined && formatted !== null ? { message: text(formatted) } : {};
  }
  if (event.transaction !== undefined && event.transaction !== null) kept.transaction = text(event.transaction);
  if (isRecord(event.tags)) {
    const tags: Json = {};
    for (const [key, value] of Object.entries(event.tags)) {
      if (isString(value) || typeof value === 'number' || typeof value === 'boolean') tags[key] = text(value);
    }
    kept.tags = tags;
  }
  if (isRecord(event.contexts)) {
    const contexts: Json = {};
    for (const name of CONTEXTS_KEPT) {
      const context = event.contexts[name];
      if (!isRecord(context)) continue;
      const entries: Json = {};
      for (const key of CONTEXT_ENTRIES_KEPT) {
        if (isString(context[key])) entries[key] = context[key];
      }
      contexts[name] = entries;
    }
    kept.contexts = contexts;
  }
  if (isRecord(event.exception)) {
    const values = Array.isArray(event.exception.values) ? event.exception.values : [];
    kept.exception = { values: values.map(exceptionValue) };
  }
  if (isRecord(event.request)) kept.request = request(event.request);
  return kept;
}

/**
 * The SDK's `beforeSend`: a new event with only what a crash report needs (the input is not changed). An event that
 * cannot be checked is dropped (`null`): better no report than one that could not be checked.
 */
export function scrubEvent<E extends Event>(event: E, hint?: EventHint): E | null {
  void hint;
  try {
    return scrubEventUnsafe(event as unknown as Json) as unknown as E;
  } catch {
    return null;
  }
}

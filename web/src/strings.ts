// All user-facing text lives here (ROADMAP §3: English only, one module).
import { LIMITS } from './api/contract';
import type { ApiErrorCode, StageName } from './api/contract';
import type { Status } from './state/model';

const plural = (n: number, one: string, other: string) => (n === 1 ? one : other);
const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);
const andList = new Intl.ListFormat('en', { style: 'long', type: 'conjunction' });
/** "4", "1 and 4", "1, 4 and 9": the numbers of the structures or pages a warning is about. */
const numberList = (numbers: readonly number[]) =>
  numbers.length > 1 ? `${numbers.slice(0, -1).join(', ')} and ${numbers.at(-1)}` : String(numbers[0]);
/** One sentence for one or for several: "Structure 1 could not be recognised." / "Structures 1 and 4 could not be recognised." */
const counted = (one: (n: string) => string, many: (list: string) => string) => (numbers: readonly number[]) =>
  numbers.length === 1 ? one(String(numbers[0])) : many(numberList(numbers));

/** What the working view calls each stage of the pipeline. */
const STAGE_LABELS = {
  upload: 'Uploading your files',
  render: 'Reading the pages',
  segment: 'Finding structures on each page',
  recognise: 'Recognising each structure',
  check: 'Checking the results',
} satisfies Record<StageName, string>;

/** The same stages in two or three words: the frame's label and the names under the measuring line's ticks. */
const STAGE_SHORT = {
  upload: 'Uploading',
  render: 'Reading pages',
  segment: 'Finding structures',
  recognise: 'Recognising',
  check: 'Checking',
} satisfies Record<StageName, string>;

export const strings = {
  appName: 'DECIMER.ai',
  /** Brand tagline, exact wording (PRODUCT.md, Brand Commitments). */
  tagline: 'Extract chemical structures from PDFs and images',
  editorLoading: 'Loading the structure editor…',
  startupFailed: 'DECIMER.ai could not start. Reload the page, and if this keeps happening, contact us.',

  shell: {
    skipToContent: 'Skip to main content',
    home: 'DECIMER.ai home',
    mainNav: 'Main',
    newUpload: 'New upload',
    about: 'About',
    privacy: 'Privacy',
    impressum: 'Impressum',
    github: 'GitHub',
    githubUrl: 'https://github.com/Steinbeck-Lab/DECIMER.ai',
    footerNav: 'Footer',
    /** The middle of the footer: "Built with [cup] by Kohulan.R at the Steinbeck Lab, Friedrich Schiller University Jena". */
    credit: {
      at: 'at the',
      group: 'Steinbeck Lab',
      groupUrl: 'https://cheminf.uni-jena.de/',
      university: 'Friedrich Schiller University Jena',
      universityUrl: 'https://www.uni-jena.de/',
    },
    /** The footer's first link on the right. */
    contact: 'Contact',
    contactUrl: 'mailto:kohulan.rajan@uni-jena.de,christoph.steinbeck@uni-jena.de',
    /** The legacy footer's line, as it worded it ("© 2025 DECIMER Project. All rights reserved."), with the current year. */
    copyright: (year: number) => `© ${year} DECIMER Project. All rights reserved.`,
    builtWith: 'Built with',
    builtWithIcon: 'coffee',
    builtBy: 'by',
    builtByName: 'Kohulan.R',
    builtByUrl: 'https://kohulanr.com',
    confirmNewTitle: 'Start a new upload?',
    confirm: {
      structure: 'structure',
      structures: 'structures',
      edited: (n: number, noun: string) => `You have edited ${n} ${noun}.`,
      checked: (n: number, noun: string) => `You have checked ${n} ${noun}.`,
      both: (edited: number, noun: string, checked: number) => `You have edited ${edited} ${noun} and checked ${checked}.`,
      consequence: 'A new upload clears this work. Download the results first if you want to keep it.',
    },
    confirmNewKeep: 'Keep working',
    confirmNewGo: 'Start new upload',
  },

  upload: {
    title: 'Drop a PDF or image',
    /** "or paste ⌘V" on a Mac, "or paste Ctrl+V" elsewhere; pasting an image starts the job at once. */
    pasteLead: 'or paste',
    pasteKeyMac: '⌘V',
    /** What a screen reader says for the Mac key (it reads ⌘ as a symbol, not as the Command key). */
    pasteKeyMacSpoken: 'Cmd+V',
    pasteKeyOther: 'Ctrl+V',
    browse: 'Choose files',
    dropActive: 'Drop to start',
    /** The full sentences, kept for assistive technology: the screen shows the measuring lines and one quiet line instead. */
    formats: 'PDF, PNG, JPG, WebP or HEIC.',
    limits: `Up to ${LIMITS.maxUploadBytes / 1024 / 1024} MB, ${LIMITS.maxPages} PDF pages and ${LIMITS.maxStructures} structures per upload.`,
    privacy: 'No account. Uploaded files are deleted after one hour.',
    /** The inspection frame's label: the real size of the drop zone on screen. */
    zoneLabel: 'Drop zone',
    /** The width dimension line's caption and the height dimension line's caption. */
    formatsLine: 'PDF · PNG · JPG · WebP · HEIC',
    sizeLimitLine: `≤ ${LIMITS.maxUploadBytes / 1024 / 1024} MB`,
    /** The one quiet line under the drop zone. */
    quietLine: `${LIMITS.maxPages} pages · ${LIMITS.maxStructures} structures · files deleted after one hour`,
    errorFormat: (names: string[]) =>
      names.length === 1
        ? `${names[0]} is not a supported file. Use PDF, PNG, JPG, WebP or HEIC.`
        : `These files are not supported: ${names.join(', ')}. Use PDF, PNG, JPG, WebP or HEIC.`,
    errorMixed:
      'A PDF and images cannot be uploaded together. Upload one PDF, or one or more images.',
    errorManyPdfs: 'Upload one PDF at a time.',
    errorSize: (mb: number) =>
      `These files add up to ${mb} MB. The limit is ${LIMITS.maxUploadBytes / 1024 / 1024} MB.`,
    errorNone: 'No file was selected.',
    errorTooMany: (max: number) => `Too many files. Upload at most ${max} at once.`,
    /** One sentence per code of the API's error shape (api/src/decimer_api/errors.py). */
    errors: {
      empty_upload: 'No file was uploaded. Choose a PDF or an image.',
      too_many_files: `Too many files. Upload at most ${LIMITS.maxUploadFiles} at once.`,
      unsupported: 'This file type is not supported. Use PDF, PNG, JPG, WebP or HEIC.',
      mixed_upload: 'A PDF and images cannot be uploaded together. Upload one PDF, or one or more images.',
      multiple_pdfs: 'Upload one PDF at a time.',
      corrupt_file: 'A file could not be read. Check that it opens on your computer, then upload it again.',
      encrypted_pdf: 'This PDF is password-protected. Remove the protection and upload it again.',
      too_many_pixels: 'An image or a PDF page is too large to process. Reduce its resolution and upload it again.',
      processing_limit: 'This file needs more memory or time than we can give it. Upload a smaller or simpler file.',
      too_large: `The upload is too large. The limit is ${LIMITS.maxUploadBytes / 1024 / 1024} MB.`,
      invalid_request: 'The upload could not be read. Please try again.',
      rate_limited: 'Too many jobs are running from your network. Wait for one to finish, then try again.',
      queue_full: 'The queue is full right now. Try again in a few minutes.',
      busy: 'The service is busy with other uploads. Try again in a moment.',
      unavailable: 'The service is temporarily unavailable. Try again in a minute.',
      not_found: 'The job was not found. Jobs are deleted one hour after they end.',
      no_results: 'This job has no structures to download.',
      method_not_allowed: 'The page and the service no longer match. Reload the page and try again.',
      http_error: 'The request failed. Please try again.',
      internal_error: 'Something went wrong on our side. Try again in a minute.',
      network: 'The upload failed. Check your connection and try again.',
      unknown: 'The upload failed. Check your connection and try again.',
    } satisfies Record<ApiErrorCode, string>,
    pastedName: 'pasted-image',
  },

  working: {
    queuedHeading: 'Waiting for a free worker',
    queuePosition: (n: number) =>
      n <= 1 ? 'You are next in line.' : `You are number ${n} in the queue.`,
    queueNote: 'Your job starts as soon as a worker is free. You can leave this tab open.',
    stepOf: (n: number, total: number) => `Step ${n} of ${total}`,
    next: (label: string) => `Next: ${label}.`,
    last: 'This is the last step.',
    /** The stages that run at the same time as the one in the heading: "Also running: reading the pages and finding structures on each page." */
    alongside: (stages: readonly StageName[]) =>
      `Also running: ${andList.format(stages.map((stage) => lowerFirst(STAGE_LABELS[stage])))}.`,
    /** The progress bar's value text: it says what its value, the number of stages done, says. */
    stepsDone: (done: number, total: number) => `${done} of ${total} steps done`,
    elapsed: (time: string) => `Elapsed ${time}`,
    /** What the calliper above the measuring line reads: only what the job knows (the name of the stage, otherwise). */
    readout: {
      queue: (n: number) => `queue ${n}`,
      pages: (n: number, pdf: boolean) => `${n} ${pdf ? plural(n, 'page', 'pages') : plural(n, 'image', 'images')}`,
      structures: (n: number) => `${n} ${plural(n, 'structure', 'structures')}`,
    },
    /** The count under the progress rule, as progress ("0 of 10 pages read"); images are not pages. */
    pages: (done: number, total: number, pdf: boolean) =>
      `${done} of ${total} ${pdf ? plural(total, 'page', 'pages') : plural(total, 'image', 'images')} read`,
    found: (n: number) => `${n} ${plural(n, 'structure', 'structures')} found`,
    /**
     * What to expect, from the job: the pages that will be processed (null while the upload is still being read) and
     * whether it is a PDF. A single page is quick, a few take under a minute, a full 10 pages can take a minute or more.
     */
    slowNote: (pages: number | null, pdf: boolean) => {
      if (pages === null) return 'How long this takes depends on the number of pages.';
      if (pages <= 1) return pdf ? 'A one-page PDF usually takes only a few seconds.' : 'A single image usually takes only a few seconds.';
      const noun = pdf ? 'pages' : 'images';
      return pages <= 3 ? `${pages} ${noun} usually take under a minute.` : `${pages} ${noun} can take a minute or more.`;
    },
    progressLabel: 'Progress',
    stage: STAGE_LABELS,
    stageShort: STAGE_SHORT,
    /** The frame's label while the job waits, and after it stopped. */
    queuedLabel: 'In queue',
    stoppedLabel: 'Stopped',
    sourcePreview: (name: string) => `Preview of ${name}`,
    /** The toggle under the working screen's numbers, and the document it opens below them (legacy's wording, as on the results). */
    document: {
      show: 'Display uploaded document',
      hide: 'Hide uploaded document',
      region: 'Uploaded document',
      /** A page of the document that has not been read yet, in the place of its picture. */
      waiting: (page: number) => `Page ${page} · not read yet`,
      none: 'The pages appear here as they are read.',
      /** Under each page of the open document. */
      caption: (page: number, pages: number) => `Page ${page} of ${pages}`,
    },
    moreFiles: (n: number) => `${n} more ${plural(n, 'file', 'files')}`,
    failedHeading: 'The job did not finish',
    failedGeneric: 'Something went wrong while processing your file. Please try again.',
    failedConnection:
      'The connection to the server was lost, so this page could not follow your job. Try again to pick it up where it is, or start over with your file.',
    failedExpired: 'The server no longer has this job; it may have expired. Please start over and upload your file again.',
    tryAgain: 'Try again',
    startOver: 'Start over',
  },

  results: {
    title: 'Structures',
    /** What the document tile calls an upload that has no file name (an image pasted from the clipboard). */
    pastedSource: 'Pasted image',
    moreSources: (name: string, more: number) => `${name} and ${more} more`,
    /** A PDF has a page limit; images do not (one page each, up to the upload's file limit), so there is none to show. */
    capacity: (structures: number, pages: number, pdf: boolean) =>
      `${structures} of ${LIMITS.maxStructures} structures · ${
        pdf ? `${pages} of ${LIMITS.maxPages} pages` : `${pages} ${plural(pages, 'image', 'images')}`
      }`,
    /** The words after each status count in the forest tile ("3 to review"). */
    countWords: {
      review: 'to review',
      notsure: 'not sure',
      edited: 'edited',
      checked: 'checked',
      unchecked: 'unchecked',
    } satisfies Record<Status, string>,
    pagesLabel: 'Structures per page',
    /** The same for an upload of images, where each one is a "page". */
    imagesLabel: 'Structures per image',
    /** A chip that is not a button (a page without structures), for assistive technology: "Page 2: no structures". */
    pageStructures: (page: number, n: number, pdf = true) => {
      const unit = pdf ? 'Page' : 'Image';
      return n === 0 ? `${unit} ${page}: no structures` : `${unit} ${page}: ${n} ${plural(n, 'structure', 'structures')}`;
    },
    /** The words after a chip's "p. 3" and "8", in its name: "p. 3 8 structures, open in the editor". */
    pageStructuresWords: (n: number, open: boolean) =>
      `${plural(n, 'structure', 'structures')}${open ? ', open in the editor' : ''}`,
    /** A chip's page, and what it says instead of a count for a page with no structures. The tag of the open page ("open") is drawn by CSS. */
    pageChip: (page: number, pdf = true) => `${pdf ? 'p.' : 'img.'} ${page}`,
    pageNone: 'none',
    /** The pill in the filter row while the results are narrowed to a page (it clears the page when pressed). */
    pageFilter: (page: number, pdf = true) => `${pdf ? 'Page' : 'Image'} ${page}`,
    pageFilterName: (page: number, pdf = true) =>
      pdf ? `Page ${page} only. Show every page` : `Image ${page} only. Show every image`,
    /** The selected result's inspection frame: its number and the real size of its tile on screen. */
    frameLabel: (number: number, width: number, height: number) => `Result ${number} · ${width} × ${height} px`,
    countsLabel: 'Status of the structures',
    filterLabel: 'Show structures',
    filterAll: 'All',
    filterReview: 'Needs review',
    filterEdited: 'Edited',
    filterEmpty: {
      review: 'No structure needs review.',
      edited: 'You have not edited any structure yet.',
      all: 'Nothing to show.',
    },
    /** The same on a chosen page. */
    filterEmptyOnPage: {
      review: (page: number, pdf = true) => `No structure on ${pdf ? 'page' : 'image'} ${page} needs review.`,
      edited: (page: number, pdf = true) => `You have not edited any structure on ${pdf ? 'page' : 'image'} ${page} yet.`,
      all: (page: number, pdf = true) => `No structure on ${pdf ? 'page' : 'image'} ${page}.`,
    },
    truncatedPages: (pages: number, processed: number) =>
      `This document has ${pages} pages. Only the first ${processed} were processed.`,
    truncatedStructures: (dropped: number) =>
      `${dropped} more ${plural(dropped, 'structure was', 'structures were')} found. Only the first ${LIMITS.maxStructures} are shown.`,
    truncatedGeneric: 'This upload was longer than the limits, so only part of it was processed.',
    /**
     * The job's warnings that are about one structure or page each (the codes of the worker's pipeline), put in one sentence
     * for all the structures or pages of a kind: "Structures 1, 4 and 9 could not be recognised." The API words each
     * one on its own, with the number in it; the number is what is taken from it.
     */
    countedWarnings: {
      page_render_failed: counted(
        (n) => `Page ${n} could not be rendered and was skipped.`,
        (list) => `Pages ${list} could not be rendered and were skipped.`,
      ),
      page_segmentation_failed: counted(
        (n) => `Page ${n} could not be searched for structures.`,
        (list) => `Pages ${list} could not be searched for structures.`,
      ),
      fragments_trimmed: counted(
        (n) => `Structure ${n}: the prediction had several fragments. Only the first was kept.`,
        (list) => `Structures ${list}: the predictions had several fragments. Only the first of each was kept.`,
      ),
      recognition_failed: counted(
        (n) => `Structure ${n} could not be recognised.`,
        (list) => `Structures ${list} could not be recognised.`,
      ),
      classification_failed: counted(
        (n) => `Structure ${n} could not be classified.`,
        (list) => `Structures ${list} could not be classified.`,
      ),
      /** Not a warning the API sends: made from the results the model cut off at its length limit that nobody has edited. */
      incomplete: counted(
        (n) => `Structure ${n} was cut off at the model's length limit; check it in the editor.`,
        (list) => `Structures ${list} were cut off at the model's length limit; check them in the editor.`,
      ),
    } satisfies Record<string, (numbers: readonly number[]) => string>,
    document: {
      /** Legacy's label for the same control. */
      toggle: 'Display uploaded document',
      region: (pages: number) =>
        pages === 1 ? 'Uploaded document, 1 page' : `Uploaded document, ${pages} pages. Scroll sideways to see them all.`,
      pageAlt: (page: number, pages: number) => `Page ${page} of ${pages}`,
      pageMissing: (page: number) => `Page ${page} could not be shown.`,
    },
    noneTitle: 'No structures were detected',
    noneBody:
      'Try a sharper or larger image, or a different page. Drawings that are very small or low-contrast are often missed.',
    uploadAnother: 'Upload another file',
    download: 'Download',
    downloadFormats: 'ZIP, SDF, CSV',
    downloadMenuLabel: 'Download the structures as shown',
    downloadZip: 'ZIP: crops, molfiles and a SMILES list',
    downloadSdf: 'SDF: all structures',
    downloadCsv: 'CSV: SMILES, InChIKey and names',
    downloading: 'Preparing the download…',
    downloadFailed: 'The download failed. Please try again.',
    editorNotResponding: 'The editor is not responding. Wait a moment and try the download again.',
    downloadStarted: 'Your download has started.',
    downloadNote: 'The download holds the structures as shown here, with your edits.',
    gridLabel: 'Recognised structures',
  },

  tile: {
    status: {
      checked: 'Checked',
      edited: 'Edited',
      review: 'Review',
      notsure: 'Not sure it is a structure',
      unchecked: 'Unchecked',
    },
    noName: 'No verified name',
    noDepiction: 'No depiction',
    /** Where the original crop should be and the picture is not there (an image that could not be read has none). */
    noPicture: 'No picture',
    /**
     * A structure RDKit refused and CDK read (decision D4: CDK is kept as a fallback, as legacy used it). The flag is
     * the short form on the tile; the hover has the whole sentence, and says who could not read it.
     */
    cdkOnlyFlag: 'Accepted by CDK only',
    /** The tile's flag for a structure the model cut off at its length limit (decision D25), until it is edited. */
    incompleteFlag: 'Incomplete',
    cdkOnlyHover: 'Accepted by CDK only (RDKit could not read it)',
    /**
     * States the fact, not a cause, and does not name Orthonym: names may be switched off altogether (no chemistry
     * service configured), and where they are on it also returns none on a timeout, with the engine unavailable, for
     * an invalid SMILES, or where the tier is not shipped.
     */
    noNameHover: 'No verified name',
    /** The name's kind (Orthonym's tier) and how it was checked (`verified`), decision D14. */
    tierPreferred: 'Preferred IUPAC name',
    tierPreferredUncertified: 'Name in preferred IUPAC form; its preferred status is not certified',
    tierSystematic: 'Systematic name (not the preferred IUPAC name)',
    tierUnknown: (tier: string) => `Orthonym tier: ${tier}`,
    verified: {
      opsin: 'OPSIN reads this name back to exactly this structure',
      opsin_split: 'OPSIN reads this name back to this structure drawn without dative bonds',
      opsin_constitution: 'OPSIN confirms the connectivity; stereo checked by CIP labelling',
      opsin_twin: 'OPSIN reads an equivalent spelling back to this structure',
      opsin_acids: 'OPSIN reads each component acid; together they rebuild this structure',
      identity: 'Exact match in a curated list',
    } as Record<string, string>,
    cropAlt: (n: number) => `Original crop of structure ${n}`,
    /** Read before the number, so a button is named "Structure 4 (2R)-3,3-bis… Checked". */
    structure: 'Structure',
  },

  editor: {
    region: 'Structure editor',
    idleTitle: 'Select a structure',
    idleBody:
      'Open a structure to compare it with the original, correct it and mark it as checked. Your edits are what the download contains.',
    legend: 'What the colours mean',
    legendItems: {
      unchecked: 'Recognised. Nobody has looked at it yet.',
      review: 'Low confidence, or the SMILES is not valid or only CDK could read it.',
      notsure: 'The classifier doubts this image is a chemical structure.',
      edited: 'You changed it and have not checked it again.',
      checked: 'You marked it as checked.',
    },
    loadFailed: 'This structure could not be loaded into the editor. You can still check it against the original and mark it, or report it.',
    position: (n: number, total: number) => `${n} of ${total}`,
    /** The panel's heading, as the tile's frame says it: "Result 4". */
    result: (n: number) => `Result ${n}`,
    editing: 'Editing',
    close: 'Close',
    previous: 'Previous structure',
    next: 'Next structure',
    canvasLabel: 'Structure editor canvas',
    /** Names for controls of Ketcher's own markup that have none (src/editor/labels.ts). */
    ketcher: {
      moreOptionsFor: (button: string) => `More options for ${button}`,
      moreOptions: 'More options',
      clipboard: 'Clipboard for the structure editor',
    },
    original: (page: number) => `Original, page ${page}`,
    name: 'Name',
    change: 'Change',
    changeNone: 'Same as recognised',
    changeEdited: 'Edited by you',
    updating: 'Updating name and SMILES…',
    updatingShort: 'Updating…',
    notUpdated: 'Not updated',
    waitingForUpdate: 'Available once the structure has been updated',
    invalidFlag: 'Invalid SMILES',
    loadFailedShort: 'Not loaded',
    loadFailedHeading: (n: number) => `Structure ${n}, not loaded in the editor`,
    retryLoad: 'Try again',
    updateFailed: 'Could not update the name and SMILES. Your edit is kept.',
    smiles: 'SMILES',
    noSmiles: 'No valid SMILES',
    molfile: 'Molfile',
    copy: 'Copy',
    copied: 'Copied',
    copySmiles: 'Copy SMILES',
    copyName: 'Copy name',
    copyMolfile: 'Copy molfile',
    markChecked: 'Mark as checked',
    undoChecked: 'Undo check',
    reset: 'Reset',
    resetHint: 'Back to the structure as recognised',
    pubchem: 'PubChem',
    coconut: 'COCONUT',
    pubchemStereo: 'Search PubChem with stereochemistry',
    pubchemStereoShort: 'with stereochemistry',
    report: 'Report a problem',
    reporting: 'Sending…',
    reported: 'Thank you for your report',
    reportFailed: 'The report could not be sent. Please try again.',
    reasons: {
      incomplete: 'Incomplete: the model stopped at its length limit; the structure is cut off.',
      notStructure: 'We are not sure this is a chemical structure.',
      invalid: 'This SMILES is not valid. Check the structure in the editor.',
      cdkOnly: 'RDKit could not read this SMILES, but CDK could. Check the structure in the editor.',
      rgroup: 'The structure contains an R group or another undefined atom.',
      lowConfidence: (percent: number) =>
        `The model was not confident about this structure (${percent}%).`,
    },
    external: '(opens in a new tab)',
  },

  // ---------------------------------------------------------------------------------------------
  // The static pages: About, Privacy and Impressum. Kept apart from the rest so that merges stay small.
  //
  // Carried over from the legacy views (about, privacy_policy and impressum blade templates), then updated: Orthonym
  // replaces STOUT, Google Analytics is gone, and the privacy text describes this stack (api/README.md, deploy/nginx).
  //
  // `[CONFIRM: ...]` marks a statement that the repository and the legacy site could not confirm. The page shows it
  // highlighted (pages/Rich.tsx) until someone who knows decides. A marker never contains a closing bracket. None may
  // remain at a release: `RELEASE=1 npm test` (scripts/check.sh --release) fails while one is left in the copy or the build.
  //
  // A page is a title plus sections. A section has a title, an optional `lang` (for German text), `content` blocks and
  // sub-sections. A block is a paragraph (a string, or a list of parts: plain text, `{ text, href }` for a link,
  // `{ text, route }` for another page of this site, `{ em }`, `{ strong }`), `{ lines }`, `{ list }` or `{ strong }`.
  // ---------------------------------------------------------------------------------------------
  pages: {
    back: 'Back to the upload',

    about: {
      title: 'About DECIMER.ai',
      lead: 'An open platform for automated optical chemical structure identification, segmentation and recognition in scientific publications.',
      before: [
        {
          title: 'How to use the DECIMER web app',
          content: [
            'Upload a PDF document, or one or more images that contain chemical structure depictions, or paste an image from the clipboard. If you upload a PDF, DECIMER Segmentation detects and segments every chemical structure depiction in it. The detected or uploaded depictions are read by the OCSR engine of DECIMER V2, and the DECIMER Image Classifier flags images that may not be chemical structures. The depictions and their SMILES are then shown for your review.',
            [
              'You can edit the structures as you need in the ',
              { text: 'Ketcher chemical structure editor', href: 'https://lifescience.opensource.epam.com/ketcher/' },
              ' before you download the structure images and molfiles (a ZIP), an SDF file or a CSV file.',
            ],
            [
              'IUPAC names come from ',
              { text: 'Orthonym', href: 'https://github.com/Steinbeck-Lab/Orthonym' },
              ' and are shown only when Orthonym has checked them by reading the name back with OPSIN. When it cannot give a verified name, the page says so; it never guesses. Hover over a name, or move focus to it, to see how it was built and checked. A name is worked out again after you edit a structure.',
            ],
          ],
        },
        {
          title: 'Limits',
          content: [
            `Up to ${LIMITS.maxPages} PDF pages and ${LIMITS.maxStructures} structures per upload, and ${LIMITS.maxUploadBytes / 1024 / 1024} MB per upload. When a limit applies, the results page says so.`,
          ],
        },
      ],
      /** The inspection frame round the wordmark: its chip, and the size it prints under its width dimension line. */
      frameLabel: 'DECIMER',
      frameSize: (width: number, height: number) => `${width} × ${height}`,
      /** The big numbers of the Limits tile, from the same limits the upload checks; the sentence below them says the same in words. */
      limitFigures: [
        { value: LIMITS.maxPages, unit: 'PDF pages' },
        { value: LIMITS.maxStructures, unit: 'structures' },
        { value: LIMITS.maxUploadBytes / 1024 / 1024, unit: 'MB' },
      ],
      componentsTitle: 'Project components',
      /** What goes into the first component and what comes out of the last, at the two ends of the measuring line. */
      componentsIn: 'PDF · image',
      componentsOut: 'ZIP · SDF · CSV',
      /** `ext`: not one of the three DECIMER engines (the tile is tinted egg nog). */
      components: [
        { name: 'DECIMER Segmentation', what: 'Chemical structure detection and extraction', url: 'https://github.com/Kohulan/DECIMER-Image-Segmentation' },
        { name: 'DECIMER Image Classifier', what: 'Chemical structure classification', url: 'https://github.com/Steinbeck-Lab/DECIMER-Image-Classifier' },
        { name: 'DECIMER Image Transformer', what: 'Optical chemical structure recognition', url: 'https://github.com/Kohulan/DECIMER-Image_Transformer' },
        { name: 'Orthonym', what: 'IUPAC names, each checked by an OPSIN round trip', licence: 'MIT licence', ext: true, url: 'https://github.com/Steinbeck-Lab/Orthonym' },
        { name: 'Ketcher', what: 'The chemical structure editor, by EPAM', ext: true, url: 'https://lifescience.opensource.epam.com/ketcher/' },
      ],
      citeTitle: 'Cite us',
      citeLead: 'If our toolkit helped your work, please cite our publications.',
      // The five DECIMER citations are the legacy list, word for word (journal in italics, volume in bold, as there).
      // The one fix: a missing comma after "Agea, I.A." in the first one. Dropped: the STOUT paper. Added: the Orthonym
      // preprint (title, authors, date and DOI as Crossref records them).
      citations: [
        {
          title: 'DECIMER.ai: an open platform for automated optical chemical structure identification, segmentation and recognition in scientific publications',
          authors: 'Rajan, K., Brinkhaus, H.O., Agea, I.A., Zielesny, A., Steinbeck, C.',
          where: [{ em: 'Nat Commun' }, ', ', { strong: '14' }, ', 5045 (2023).'],
          doi: 'https://doi.org/10.1038/s41467-023-40782-0',
        },
        {
          title: 'Advancements in hand-drawn chemical structure recognition through an enhanced DECIMER architecture.',
          authors: 'Rajan, K., Brinkhaus, H.O., Zielesny, A., Steinbeck, C.',
          where: [{ em: 'J Cheminform' }, ', ', { strong: '16' }, ', 78 (2024).'],
          doi: 'https://doi.org/10.1186/s13321-024-00872-7',
        },
        {
          title: 'DECIMER: towards deep learning for chemical image recognition',
          authors: 'Rajan, K., Zielesny, A., Steinbeck, C.',
          where: [{ em: 'J Cheminform' }, ', ', { strong: '12' }, ', 65 (2020).'],
          doi: 'https://doi.org/10.1186/s13321-020-00469-w',
        },
        {
          title: 'DECIMER-Segmentation: Automated extraction of chemical structure depictions from scientific literature',
          authors: 'Rajan, K., Brinkhaus, H.O., Sorokina, M. et al.',
          where: [{ em: 'J Cheminform' }, ', ', { strong: '13' }, ', 20 (2021).'],
          doi: 'https://doi.org/10.1186/s13321-021-00496-1',
        },
        {
          title: 'DECIMER 1.0: deep learning for chemical image recognition using transformers',
          authors: 'Rajan, K., Zielesny, A., Steinbeck, C.',
          where: [{ em: 'J Cheminform' }, ', ', { strong: '13' }, ', 61 (2021).'],
          doi: 'https://doi.org/10.1186/s13321-021-00538-8',
        },
        {
          title: 'Orthonym: verified IUPAC names for chemical structures in the wild',
          authors: 'Rajan, K., Zielesny, A., Steinbeck, C.',
          where: [{ em: 'ChemRxiv' }, ' (2026), preprint.'],
          doi: 'https://doi.org/10.26434/chemrxiv.15009769/v1',
        },
      ],
      moreResearch: 'Learn more about our research',
      moreResearchUrl: 'https://cheminf.uni-jena.de/research/deep-learning/',
      after: [
        {
          title: 'About DECIMER',
          content: [
            [
              'Deep Learning for Chemical Image Recognition (DECIMER) is a step towards automated chemical image segmentation and recognition. DECIMER is actively developed and maintained by the ',
              { text: 'Steinbeck group', href: 'https://cheminf.uni-jena.de/research/deep-learning/' },
              ' at the ',
              { text: 'Friedrich Schiller University Jena', href: 'https://www.uni-jena.de/' },
              '. The code is open source (MIT licence).',
            ],
            'You need to have the right, granted by the publisher of the uploaded documents and images, to use them for data mining. We do not store or use the data for anything other than automated processing and display of results in the web app. Your documents and images are deleted one hour after your job ends, unless a problem is reported. If you report a problem, we keep the reported image to analyse the error, and delete it afterwards.',
            [
              'A more detailed description of what happens with your data is in our ',
              { text: 'privacy policy', route: '/privacy' },
              '. German law requires us to provide some information about who we are: ',
              { text: 'Impressum - Legal Disclosure', route: '/impressum' },
              '.',
            ],
          ],
        },
      ],
    },

    privacy: {
      title: 'Privacy',
      intro: [
        'Personal data (usually referred to just as "data" below) will only be processed by us to the extent necessary and for the purpose of providing a functional and user-friendly website, including its contents, and the services offered there.',
        'Per Art. 4 No. 1 of Regulation (EU) 2016/679, i.e. the General Data Protection Regulation (hereinafter referred to as the "GDPR"), "processing" refers to any operation or set of operations such as collection, recording, organization, structuring, storage, adaptation, alteration, retrieval, consultation, use, disclosure by transmission, dissemination, or otherwise making available, alignment, or combination, restriction, erasure, or destruction performed on personal data, whether by automated means or not.',
        'The following privacy policy is intended to inform you in particular about the type, scope, purpose, duration, and legal basis for the processing of such data either under our own control or in conjunction with others. We also inform you below about the third-party components we use to optimize our website and improve the user experience which may result in said third parties also processing data they collect and control.',
        'Our privacy policy is structured as follows: I. Information about us as controllers of your data. II. The rights of users and data subjects. III. Information about the data processing.',
      ],
      sections: [
        {
          title: 'I. Information about us as controllers of your data',
          content: [
            'The party responsible for this website (the "controller") for purposes of data protection law is:',
            {
              lines: [
                'Cheminformatics and Computational Metabolomics group at Friedrich-Schiller-University Jena',
                'Lessingstraße 8',
                '07743 Jena',
                'Germany',
              ],
            },
            {
              lines: [
                'Telephone: +49 3641 948181',
                ['Email: ', { text: 'christoph.steinbeck@uni-jena.de', href: 'mailto:christoph.steinbeck@uni-jena.de' }],
              ],
            },
            "The controller's data protection officer is:",
            { lines: ['Prof. Dr. Christoph Steinbeck'] },
          ],
        },
        {
          title: 'II. The rights of users and data subjects',
          content: [
            'With regard to the data processing to be described in more detail below, users and data subjects have the right',
            {
              list: [
                'to confirmation of whether data concerning them is being processed, information about the data being processed, further information about the nature of the data processing, and copies of the data (cf. also Art. 15 GDPR);',
                'to correct or complete incorrect or incomplete data (cf. also Art. 16 GDPR);',
                'to the immediate deletion of data concerning them (cf. also Art. 17 GDPR), or, alternatively, if further processing is necessary as stipulated in Art. 17 Para. 3 GDPR, to restrict said processing per Art. 18 GDPR;',
                'to receive copies of the data concerning them and/or provided by them and to have the same transmitted to other providers/controllers (cf. also Art. 20 GDPR);',
                'to file complaints with the supervisory authority if they believe that data concerning them is being processed by the controller in breach of data protection provisions (see also Art. 77 GDPR).',
              ],
            },
            'In addition, the controller is obliged to inform all recipients to whom it discloses data of any such corrections, deletions, or restrictions placed on processing the same per Art. 16, 17 Para. 1, 18 GDPR. However, this obligation does not apply if such notification is impossible or involves a disproportionate effort. Nevertheless, users have a right to information about these recipients.',
            { strong: "Likewise, under Art. 21 GDPR, users and data subjects have the right to object to the controller's future processing of their data pursuant to Art. 6 Para. 1 lit. f) GDPR. In particular, an objection to data processing for the purpose of direct advertising is permissible." },
          ],
        },
        {
          title: 'III. Information about the data processing',
          content: [
            'Your data processed when using our website will be deleted or blocked as soon as the purpose for its storage ceases to apply, provided the deletion of the same is not in breach of any statutory storage obligations or unless otherwise stipulated below.',
          ],
          sections: [
            {
              title: 'Your uploads and results',
              content: [
                'There is no account and no login.',
                'When you upload a PDF or images, or paste an image, the server keeps your results (SMILES, InChIKeys, names and molfiles) in a job record in its job store (Redis), and, in a job folder on disk, the uploaded file, the page images made from it and the structure images cut out of them. They are used only to compute and show your results.',
                'They are deleted about one hour after your job ends, whether it finished or failed. The record expires by itself; a clean-up job removes the folders and runs every five minutes, so allow a few minutes more. A running job keeps its record alive with every step it completes. A job that is still waiting, or whose worker has stopped, is removed after its own time limit, about six hours at most with the current settings.',
                'When you edit a structure, the edited structure is sent to the server, which works out its SMILES, InChIKey, picture and name and sends them back; the server does not keep the edit. A download sends your edits once more to build the file, which the server does not keep either.',
                'The basis for processing your uploads, your edits and your results is Art. 6 Para. 1 lit. f) GDPR. Our legitimate interest lies in providing the free service.',
              ],
            },
            {
              title: 'Problem reports',
              content: [
                'If you choose "Report a problem" on a result, the server saves a report so that we can analyse the error. A report keeps the picture of that structure and the result as it was recognised (for example SMILES, molfile, name and confidence), the number of pages and the warnings of the job, the random identifier of the job (the job itself is deleted after about an hour), the versions of the models and the time. It keeps no IP address and none of your edits.',
                'Reports are stored on the server, in a folder that only its administrators can open. A report is deleted once we have analysed it, and at the latest after 12 months: the server deletes older reports by itself. Reports are not included in any backup.',
              ],
            },
            {
              title: 'Fair use and your network address',
              content: [
                'To give everyone a fair share, the server lets each visitor run at most two jobs at once. It counts them per network address (for IPv6, per /64 network), but the application does not store the address: it keeps only a keyed hash (an HMAC) of it, made with a secret key that stays on the server. The hash cannot be matched to an address without the key. It is kept with the job record and the counters, which expire with the job. The web server in front of the application also holds network addresses in memory to apply its own rate limits (requests per second, uploads per minute, open connections), only for as long as those limits need them, and does not write them to its access log.',
                'The basis is Art. 6 Para. 1 lit. f) GDPR. Our legitimate interest lies in the stability and fair use of the service.',
              ],
            },
            {
              title: 'Server logs',
              content: [
                'The web server of this site writes an access log that records the date and time, the request method, the page requested (job identifiers are left out), the answer code, the time taken and the size of the answer. It records no IP address, no user agent (browser type or operating system), no referring page and no query string.',
                'If the server hits an error, for example when it cannot pass a request on, when an upload is too large or when a file cannot be opened, its error log can name the IP address of the request, the request itself (the page address, which for a job includes the job identifier), the name of the site that was asked for and the page that referred to it. The error log is the only place where this site writes an IP address down. It is split into one file per day, and no line in it is kept longer than seven days.',
              ],
            },
            {
              title: 'Cookies and your browser',
              content: [
                'This site itself sets no cookies. Your browser keeps one small note in its session storage, "decimer.visited", so that the animated logo plays only the first time you open the site in a browser tab; it is gone when you close the tab. The structure editor (Ketcher) may also keep its own settings in the local storage of your browser. You can delete these in your browser settings. The visit counter described next does set cookies of its own.',
              ],
            },
            {
              title: 'Visit statistics (Matomo)',
              content: [
                'We count visits with Matomo, a web-analytics tool run by NFDI4Chem (matomo.nfdi4chem.de). This site loads the Matomo script from that server, so your browser contacts it each time you open a page of this site and sends it the data Matomo\'s tracker sends by default: the address and title of the page, the referring page, the time, and your screen size, language and browser. Your IP address reaches it with every request. The code is the one the previous version of this site used, and it does not switch Matomo\'s cookies off, so Matomo sets first-party cookies whose names start with "_pk_", such as a visitor identifier (by Matomo\'s defaults valid for up to 13 months), a session marker (30 minutes), a record of the website you came from when you arrive through a link (6 months), and a short-lived test cookie. You can delete them in your browser settings.',
                'Matomo counts each page you open on this site (the main page, About, Privacy and Impressum). The page address never contains your files or results. It may also record the address of a link to another website that you click, such as a PubChem or COCONUT search, which contains the InChIKey of the structure. No other analytics service is used, and apart from this counter this site loads no scripts, fonts or images from other websites. Links to other sites open them only when you click.',
                'The basis for counting visits is Art. 6 Para. 1 lit. f) GDPR. Our legitimate interest lies in seeing how the site is used. You can opt out at any time: in your browser, block cookies or block scripts from matomo.nfdi4chem.de, or use a tracker blocker. The site works without Matomo.',
              ],
            },
            {
              title: 'Error reports and usage counts',
              content: [
                'If the site fails, an error report goes to an error tracker (GlitchTip) that runs on our own server in Jena. It holds the technical details of the error, never your files, structures or IP addresses, and is kept for 90 days. The server counts how many jobs run per day, without IP addresses.',
              ],
            },
          ],
        },
      ],
      closing: [
        [
          'The legal parts of this text follow the ',
          { text: 'Model Data Protection Statement', href: 'https://www.ratgeberrecht.eu/leistungen/muster-datenschutzerklaerung.html' },
          ' for ',
          { text: 'Anwaltskanzlei Weiß & Partner', href: 'https://www.ratgeberrecht.eu/', lang: 'de' },
          '.',
        ],
      ],
    },

    impressum: {
      title: 'Impressum',
      lead: 'This legal disclosure (Impressum) is required by German law. It is in German.',
      sections: [
        {
          title: 'Angaben gemäß § 5 DDG',
          lang: 'de',
          content: [
            '(Friedrich-Schiller-Universität Jena- Arbeitsgruppe: Cheminformatics and Computational Metabolomics)',
            { lines: ['Lessingstr. 8', '07743 Jena'] },
          ],
        },
        {
          title: 'Repräsentiert von',
          lang: 'de',
          content: [
            {
              lines: [
                'Friedrich-Schiller-Universität Jena',
                'Institut für Anorganische und Analytische Chemie',
                'Arbeitsgruppe: Cheminformatics and Computational Metabolomics',
                'Ansprechpartner: Prof. Dr. Christoph Steinbeck',
              ],
            },
          ],
        },
        {
          title: 'Kontaktdaten',
          lang: 'de',
          content: [
            {
              lines: [
                'Telefon: +49 3641 948181',
                ['E-Mail: ', { text: 'christoph.steinbeck@uni-jena.de', href: 'mailto:christoph.steinbeck@uni-jena.de' }],
                ['Webseite: ', { text: 'https://cheminf.uni-jena.de/', href: 'https://cheminf.uni-jena.de/' }],
              ],
            },
          ],
        },
        {
          title: 'Haftungsausschluss',
          lang: 'de',
          sections: [
            {
              title: 'Verantwortlichkeit für Inhalte',
              content: [
                'Die Inhalte unserer Webseiten wurden unter größter Sorgfalt erstellt. Trotzdem können wir nicht garantieren, dass der Inhalt aktuell, zuverlässig oder komplett ist. Gemäß den gesetzlichen Vorschriften sind wir für die selbst erstellten Inhalte verantwortlich. In diesem Zusammenhang möchten wir klarstellen, dass wir nicht verantwortlich sind für Informationen, die von dritten Parteien zur Verfügung gestellt oder von diesen gesammelt werden. Wir kontrollieren weder die Informationen, die versandt werden, noch verfolgen wir mögliche illegale Aktivitäten in diesen. Werden illegale Aktivitäten festgestellt, folgen wir unserer Verpflichtung, die entsprechenden Inhalte zu blockieren oder zu löschen, entsprechend den Paragraphen 8 bis 10 des Telemedia Acts (TMG).',
              ],
            },
            {
              title: 'Verantwortlichkeit für Links',
              content: [
                'Die Verantwortung für Inhalte von Links Dritter (externe Inhalte) liegt in der Verantwortung der jeweiligen Webseitenbetreiber. Zum Zeitpunkt der Einfügung der verwendeten Links auf unseren Seiten wurden keine illegalen Aktivitäten in diesen festgestellt. Sobald uns illegale Aktivitäten oder Verstöße bekannt werden, werden wir den entsprechenden Link entfernen.',
              ],
            },
            {
              title: 'Copyright',
              content: [
                'Unsere Webseiten und deren Inhalt (Texte, Fotos, Grafiken, Design) unterliegen dem deutschen Urheberrecht. Soweit nicht anderweitig gesetzlich vereinbart unterliegt der Nutzen, die Wiedergabe, Kopie oder die Veränderung der Inhalte dem Urheberrecht. Ausnahmen müssen schriftlich von den Webseitenbetreibern oder Rechteinhabern genehmigt werden. Individuelle Kopien sind nur für den privaten Gebrauch gestattet, sie dürfen weder direkt noch indirekt für wirtschaftliche Zwecke genutzt werden. Der nicht genehmigte Nutzen von unter Urheberrecht geschütztem Material ist nach Paragraph 106 strafbar.',
              ],
            },
          ],
        },
      ],
      closing: [
        [
          'Dieses Impressum wurde mithilfe von ',
          { text: 'impressumgeneratorenglisch.de', href: 'http://impressumgeneratorenglisch.de/' },
          ' erstellt.',
        ],
      ],
    },
  },
} as const;

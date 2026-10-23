// All user-facing text lives here (ROADMAP §3: English only, one module).
import { LIMITS } from './api/contract';
import type { ApiErrorCode, StageName } from './api/contract';

const plural = (n: number, one: string, other: string) => (n === 1 ? one : other);

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
    issuesUrl: 'https://github.com/Steinbeck-Lab/DECIMER.ai/issues',
    footerNav: 'Footer',
    credit: {
      before: 'Developed by the ',
      group: 'Steinbeck group',
      groupUrl: 'https://cheminf.uni-jena.de/',
      at: ' at ',
      university: 'Friedrich Schiller University Jena',
      universityUrl: 'https://www.uni-jena.de/',
      after: '.',
    },
    contact: 'Contact us by email',
    contactUrl: 'mailto:kohulan.rajan@uni-jena.de;christoph.steinbeck@uni-jena.de',
    reportIssue: 'or open an issue on GitHub',
    builtWith: 'Built with',
    builtWithIcon: 'coffee',
    builtBy: 'by Kohulan',
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
    title: 'Drop a PDF or images here',
    orPaste: 'or paste an image with Ctrl+V (Cmd+V on a Mac). It starts at once.',
    browse: 'Choose files',
    dropActive: 'Drop to start',
    formats: 'PDF, PNG, JPG, WebP or HEIC.',
    limits: `Up to ${LIMITS.maxUploadBytes / 1024 / 1024} MB, ${LIMITS.maxPages} PDF pages and ${LIMITS.maxStructures} structures per upload.`,
    privacy: 'No account. Uploaded files are deleted after one hour.',
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
    heading: 'Working on your upload',
    queuedHeading: 'Waiting for a free worker',
    queuePosition: (n: number) =>
      n <= 1 ? 'You are next in line.' : `You are number ${n} in the queue.`,
    queueNote: 'Your job starts as soon as a worker is free. You can leave this tab open.',
    stepOf: (n: number, total: number) => `Step ${n} of ${total}`,
    next: (label: string) => `Next: ${label}.`,
    last: 'This is the last step.',
    elapsed: (time: string) => `Elapsed ${time}`,
    pages: (done: number, total: number) => `Page ${done} of ${total}`,
    found: (n: number) => `${n} ${plural(n, 'structure', 'structures')} found`,
    slowNote: 'A 10-page PDF can take a minute or more.',
    progressLabel: 'Progress',
    stage: {
      upload: 'Uploading your files',
      render: 'Reading the pages',
      segment: 'Finding structures on each page',
      recognise: 'Recognising each structure',
      check: 'Checking the results',
    } satisfies Record<StageName, string>,
    sourcePreview: (name: string) => `Preview of ${name}`,
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
    source: (name: string) => `Recognised from ${name}`,
    pastedSource: 'a pasted image',
    moreSources: (name: string, more: number) => `${name} and ${more} more`,
    capacity: (structures: number, pages: number) =>
      `${structures} of ${LIMITS.maxStructures} structures · ${pages} of ${LIMITS.maxPages} pages`,
    countReview: (n: number) => `${n} to review`,
    countNotSure: (n: number) => `${n} not sure`,
    countEdited: (n: number) => `${n} edited`,
    countChecked: (n: number) => `${n} checked`,
    countUnchecked: (n: number) => `${n} unchecked`,
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
    truncatedPages: (pages: number, processed: number) =>
      `This document has ${pages} pages. Only the first ${processed} were processed.`,
    truncatedStructures: (dropped: number) =>
      `${dropped} more ${plural(dropped, 'structure was', 'structures were')} found. Only the first ${LIMITS.maxStructures} are shown.`,
    truncatedGeneric: 'This upload was longer than the limits, so only part of it was processed.',
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
    structureAlt: (n: number) => `Recognised structure ${n}`,
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
      review: 'Low confidence, or the SMILES is not valid.',
      notsure: 'The classifier doubts this image is a chemical structure.',
      edited: 'You changed it and have not checked it again.',
      checked: 'You marked it as checked.',
    },
    loadFailed: 'This structure could not be loaded into the editor. You can still check it against the original and mark it, or report it.',
    position: (n: number, total: number) => `${n} of ${total}`,
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
    dialogName: 'Structure editor',
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
    isChecked: 'Checked',
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
      notStructure: 'We are not sure this is a chemical structure.',
      invalid: 'This SMILES is not valid. Check the structure in the editor.',
      rgroup: 'The structure contains an R group or another undefined atom.',
      lowConfidence: (percent: number) =>
        `The model was not confident about this structure (${percent}%).`,
    },
    external: '(opens in a new tab)',
  },

  pages: {
    draftNote: 'Draft text. To be finalised in TODO.',
    draftNotePrivacy: 'Draft text. To be finalised in TODO, and reviewed by the data-protection officer in P5-06.',
    back: 'Back to the upload',

    about: {
      title: 'About DECIMER.ai',
      lead: 'An open platform for automated optical chemical structure identification, segmentation and recognition in scientific publications.',
      howTitle: 'How to use it',
      how: [
        'Upload a PDF document or one or more images that contain chemical structure depictions, or paste an image from the clipboard. For a PDF, DECIMER Segmentation finds every structure depiction on each page.',
        'The DECIMER Image Transformer reads each depiction as a SMILES, and the DECIMER Image Classifier flags images that may not be chemical structures.',
        'You check the results, correct them in the Ketcher structure editor and download the structures, exactly as you left them.',
        'IUPAC names come from Orthonym and are shown only when its OPSIN round trip confirms them. Otherwise the page says there is no verified name; it never guesses.',
      ],
      limitsTitle: 'Limits',
      limits: `Up to ${LIMITS.maxPages} PDF pages and ${LIMITS.maxStructures} structures per upload, and ${LIMITS.maxUploadBytes / 1024 / 1024} MB per upload. When a limit applies, the results page says so.`,
      componentsTitle: 'Project components',
      components: [
        { name: 'DECIMER Segmentation', what: 'Chemical structure detection and extraction', url: 'https://github.com/Kohulan/DECIMER-Image-Segmentation' },
        { name: 'DECIMER Image Classifier', what: 'Chemical structure classification', url: 'https://github.com/Steinbeck-Lab/DECIMER-Image-Classifier' },
        { name: 'DECIMER Image Transformer', what: 'Optical chemical structure recognition', url: 'https://github.com/Kohulan/DECIMER-Image_Transformer' },
        { name: 'Orthonym', what: 'IUPAC names checked by an OPSIN round trip', url: 'https://github.com/Steinbeck-Lab/Orthonym' },
        { name: 'Ketcher', what: 'The structure editor, by EPAM', url: 'https://lifescience.opensource.epam.com/ketcher/' },
      ],
      citeTitle: 'Cite us',
      citeLead: 'If our toolkit helped your work, please cite our publications.',
      citations: [
        { title: 'DECIMER.ai: an open platform for automated optical chemical structure identification, segmentation and recognition in scientific publications', authors: 'Rajan, K., Brinkhaus, H.O., Agea, I.A., Zielesny, A., Steinbeck, C.', where: 'Nat Commun 14, 5045 (2023)', doi: 'https://doi.org/10.1038/s41467-023-40782-0' },
        { title: 'Advancements in hand-drawn chemical structure recognition through an enhanced DECIMER architecture', authors: 'Rajan, K., Brinkhaus, H.O., Zielesny, A., Steinbeck, C.', where: 'J Cheminform 16, 78 (2024)', doi: 'https://doi.org/10.1186/s13321-024-00872-7' },
        { title: 'DECIMER: towards deep learning for chemical image recognition', authors: 'Rajan, K., Zielesny, A., Steinbeck, C.', where: 'J Cheminform 12, 65 (2020)', doi: 'https://doi.org/10.1186/s13321-020-00469-w' },
        { title: 'DECIMER-Segmentation: Automated extraction of chemical structure depictions from scientific literature', authors: 'Rajan, K., Brinkhaus, H.O., Sorokina, M. et al.', where: 'J Cheminform 13, 20 (2021)', doi: 'https://doi.org/10.1186/s13321-021-00496-1' },
        { title: 'DECIMER 1.0: deep learning for chemical image recognition using transformers', authors: 'Rajan, K., Zielesny, A., Steinbeck, C.', where: 'J Cheminform 13, 61 (2021)', doi: 'https://doi.org/10.1186/s13321-021-00538-8' },
      ],
      moreResearch: 'Learn more about our research',
      moreResearchUrl: 'https://cheminf.uni-jena.de/research/deep-learning/',
      aboutTitle: 'About the project',
      aboutBody: [
        'Deep Learning for Chemical Image Recognition (DECIMER) is a step towards automated chemical image segmentation and recognition. It is developed and maintained by the Steinbeck group at Friedrich Schiller University Jena, and the code is open source (MIT).',
        'You need the right, granted by the publisher, to use the documents and images you upload for data mining.',
        'Your files are used only to compute and show your results. They are deleted after one hour, unless you report a problem: a reported image is kept until we have analysed the error, then deleted. See the privacy page for details.',
      ],
    },

    privacy: {
      title: 'Privacy',
      sections: [
        {
          title: 'Who is responsible',
          body: [
            'Cheminformatics and Computational Metabolomics group, Friedrich Schiller University Jena, Lessingstraße 8, 07743 Jena, Germany. Telephone +49 3641 948181, email christoph.steinbeck@uni-jena.de. The data-protection contact is Prof. Dr. Christoph Steinbeck.',
          ],
        },
        {
          title: 'Your files and results',
          body: [
            'There is no account and no login. A document or image you upload, the crops cut from it and your results are used only to compute and show your results.',
            'They are deleted one hour after your job ends. If you report a problem with a result, that image and its result are kept until we have analysed the problem, then deleted.',
          ],
        },
        {
          title: 'Server logs',
          body: [
            'For security and stability the server records the browser type and version, the operating system, the referring page, the pages visited, the time of the visit and the IP address. These logs are deleted within seven days unless an incident needs them as evidence. (Carried over from the legacy text; to be confirmed.)',
          ],
        },
        {
          title: 'Analytics',
          body: [
            'This site does not use Google Analytics. Visit statistics are counted with Matomo, run by NFDI4Chem, in a mode that sets no cookies. (To be checked against what the deployed site actually loads before launch.)',
          ],
        },
        {
          title: 'Your rights',
          body: [
            'Under the GDPR you have the right to information about, correction of and deletion of data concerning you, to restriction of its processing, to data portability, to object, and to complain to a supervisory authority. Contact the address above.',
          ],
        },
      ],
    },

    impressum: {
      title: 'Impressum',
      note: 'Legal disclosure. Carried over from the legacy site; the full German text is to be reviewed and added in TODO.',
      sections: [
        {
          title: 'Provider (Angaben gemäß § 5 TMG)',
          lines: [
            'Friedrich-Schiller-Universität Jena',
            'Institut für Anorganische und Analytische Chemie',
            'Arbeitsgruppe Cheminformatics and Computational Metabolomics',
            'Lessingstraße 8',
            '07743 Jena',
          ],
        },
        {
          title: 'Contact person',
          lines: ['Prof. Dr. Christoph Steinbeck'],
        },
        {
          title: 'Contact',
          lines: ['Telephone: +49 3641 948181', 'Email: christoph.steinbeck@uni-jena.de', 'Web: https://cheminf.uni-jena.de/'],
        },
      ],
    },
  },
} as const;

/** Build-time settings (Vite replaces `import.meta.env.*`; production builds leave them all unset). */

const requested = Number(import.meta.env.VITE_EDIT_DEBOUNCE_MS);

/** How long the canvas must be quiet before an edit is reported to the store. */
export const EDIT_DEBOUNCE_MS = Number.isFinite(requested) && requested > 0 ? requested : 250;

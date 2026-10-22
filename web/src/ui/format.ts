import { strings } from '../strings';
import type { Status } from '../state/model';

/**
 * `src` for a depiction: a URL as it is, inline markup as a data URL. Anything
 * that starts with `<` (after whitespace) is markup: RDKit's SVG begins with an
 * `<?xml` prolog, not with `<svg`. It goes into an `<img>`, never into the DOM
 * as markup, so scripts in an SVG cannot run. URLs never start with `<`.
 */
export function imageSrc(svg: string): string {
  return svg.trimStart().startsWith('<')
    ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
    : svg;
}

export function tierLabel(tier: string | null): string {
  if (!tier) return '';
  return strings.tiers[tier] ?? tier.replaceAll('_', ' ');
}

/** Why a name is shown or missing, for the hover text. */
export function tierText(name: string | null, tier: string | null): string {
  return name ? strings.tile.tier(tierLabel(tier)) : strings.tile.declined;
}

export function statusLabel(status: Status): string {
  return strings.tile.status[status];
}

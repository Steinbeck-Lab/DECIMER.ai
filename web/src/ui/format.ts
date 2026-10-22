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

/**
 * The lines of the hover text for a name (P4-15, decision D14): what kind of name it is (Orthonym's `tier`) and
 * how it was checked (`verified`). Both come from the server as ids and are put into words here, from a fixed
 * list. A verification this list does not know is left out (nothing is made up); a tier it does not know is shown
 * as Orthonym's own id. With no name, the one line states that fact and no cause.
 */
export function nameHover(name: string | null, tier: string | null, verified: string | null): string[] {
  if (name === null) return [strings.tile.noNameHover];
  const lines: string[] = [];
  if (tier !== null) {
    // Exact ids only: "the preferred IUPAC name" is claimed for a certified PIN and nothing else, so a tier that
    // merely starts with pin_ is shown as Orthonym's own id.
    if (tier === 'pin_verified') lines.push(strings.tile.tierPreferred);
    else if (tier === 'pin_unverified') lines.push(strings.tile.tierPreferredUncertified);
    else if (tier === 'systematic_verified') lines.push(strings.tile.tierSystematic);
    else lines.push(strings.tile.tierUnknown(tier));
  }
  const how = verified === null ? undefined : strings.tile.verified[verified];
  if (how) lines.push(how);
  return lines;
}

export function statusLabel(status: Status): string {
  return strings.tile.status[status];
}

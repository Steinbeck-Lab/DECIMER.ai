import { useState } from 'react';
import { coconutUrl, hasStereo, isInchiKey, pubchemUrl } from '../chem/links';
import { strings } from '../strings';
import { IconExternal } from '../ui/icons';

function External({ href, stale, children }: { href: string; stale: boolean; children: string }) {
  if (stale) {
    // The link would search for the structure from before the edit, so there is no link until the server has answered.
    return (
      <span className="dc-link dc-link--off" aria-disabled="true" title={strings.editor.waitingForUpdate}>
        {children}
        <IconExternal size={14} />
      </span>
    );
  }
  return (
    <a className="dc-link" href={href} target="_blank" rel="noreferrer">
      {children}
      <IconExternal size={14} />
      <span className="dc-sr"> {strings.editor.external}</span>
    </a>
  );
}

/**
 * PubChem (with or without stereochemistry, when the key carries it) and COCONUT,
 * both by InChIKey. While the key is stale (an edit the server has not answered yet)
 * the links are switched off rather than pointing at the old structure.
 */
export function Links({ inchikey, stale }: { inchikey: string; stale: boolean }) {
  const [withStereo, setWithStereo] = useState(true);
  if (!isInchiKey(inchikey)) return null;
  const stereo = hasStereo(inchikey);
  return (
    <div className="dc-links">
      <External href={pubchemUrl(inchikey, !stereo || withStereo)} stale={stale}>
        {strings.editor.pubchem}
      </External>
      <External href={coconutUrl(inchikey)} stale={stale}>
        {strings.editor.coconut}
      </External>
      {stereo && (
        <label className="dc-switch">
          <input
            type="checkbox"
            role="switch"
            aria-label={strings.editor.pubchemStereo}
            checked={withStereo}
            onChange={(event) => setWithStereo(event.target.checked)}
          />
          <span className="dc-switch__track" aria-hidden="true" />
          <span className="dc-switch__label">{strings.editor.pubchemStereoShort}</span>
        </label>
      )}
    </div>
  );
}

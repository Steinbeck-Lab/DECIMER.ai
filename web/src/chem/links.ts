const INCHIKEY = /^[A-Z]{14}-[A-Z]{10}-[A-Z]$/;

/** The hash of an empty second layer. Keys of molecules without stereo, isotope or similar layers all start their second block with it (the familiar `-UHFFFAOYSA-`). */
const NO_EXTRA_LAYERS = 'UHFFFAOY';

export function isInchiKey(key: string): boolean {
  return INCHIKEY.test(key);
}

/**
 * Does the key carry anything beyond the connectivity block (stereochemistry,
 * isotopes)? Then PubChem can be searched with the full key or with the first
 * 14-character block only.
 *
 * The earlier Laravel version of decimer.ai tested `substr($key, 14, 1) !== 'A'`. Offset 14 is the hyphen
 * between the blocks, so that test was always true and offered the toggle for
 * every structure. This one looks at the second block itself.
 */
export function hasStereo(key: string): boolean {
  return isInchiKey(key) && key.slice(15, 23) !== NO_EXTRA_LAYERS;
}

export function pubchemUrl(key: string, withStereo: boolean): string {
  const query = withStereo ? key : key.slice(0, 14);
  return `https://pubchem.ncbi.nlm.nih.gov/#query=${encodeURIComponent(query)}`;
}

export function coconutUrl(key: string): string {
  return `https://coconut.naturalproducts.net/search?q=${encodeURIComponent(key)}`;
}

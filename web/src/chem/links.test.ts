import { expect, test } from 'vitest';
import { coconutUrl, hasStereo, isInchiKey, pubchemUrl } from './links';

// Keys of the golden-paper compounds (mocks/fixtures/compounds.json).
const WITH_STEREO = 'GFYJLJXYTUQVEV-DKSSEZFCSA-N'; // shentonin A
const WITHOUT_STEREO = 'ZQSIJRDFPHDXIC-UHFFFAOYSA-N'; // daidzein

test('a key with a hashed stereo layer offers the stereo toggle', () => {
  expect(hasStereo(WITH_STEREO)).toBe(true);
});

test('a key without extra layers (UHFFFAOY) does not', () => {
  expect(hasStereo(WITHOUT_STEREO)).toBe(false);
});

test('an empty or malformed key never offers links', () => {
  expect(isInchiKey('')).toBe(false);
  expect(hasStereo('')).toBe(false);
  expect(hasStereo('not-a-key')).toBe(false);
});

test('earlier-version bug pinned: the character at offset 14 is the hyphen, so "not A" held for every key', () => {
  expect(WITHOUT_STEREO.charAt(14)).toBe('-');
  expect(WITH_STEREO.charAt(14)).toBe('-');
});

test('PubChem: with stereo uses the full key, without uses the first 14 characters', () => {
  expect(pubchemUrl(WITH_STEREO, true)).toBe('https://pubchem.ncbi.nlm.nih.gov/#query=GFYJLJXYTUQVEV-DKSSEZFCSA-N');
  expect(pubchemUrl(WITH_STEREO, false)).toBe('https://pubchem.ncbi.nlm.nih.gov/#query=GFYJLJXYTUQVEV');
});

test('COCONUT searches by the full InChIKey', () => {
  expect(coconutUrl(WITH_STEREO)).toBe('https://coconut.naturalproducts.net/search?q=GFYJLJXYTUQVEV-DKSSEZFCSA-N');
});

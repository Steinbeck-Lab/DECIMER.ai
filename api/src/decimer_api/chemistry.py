"""Canonical values for one structure, computed with RDKit.

Shared by the worker (`check` stage) and by `/validate`: both must give the
screen and the export the same SMILES, InChIKey, molfile and drawing.

`analyse_smiles` never raises for a bad SMILES; the answer says `valid=False`. What "valid" means:
RDKit parses and sanitizes the SMILES and the molecule has at least one atom.

* An invalid SMILES keeps its raw text; InChIKey, molfile and SVG are empty.
* A valid one gets its canonical (isomeric) SMILES, a V2000 molfile with 2D coordinates and wedge
  bonds, and an inline SVG that starts with `<svg` (RDKit's XML prolog is removed) and has no
  background rectangle.
* The InChIKey is empty for a valid structure InChI cannot represent, for example one with a
  dummy atom (`*`, an R group). The UI flags those as R groups from the SMILES.

CXSMILES extensions and a trailing name are refused on purpose: DECIMER writes plain SMILES, and
an extension block can carry atom labels that would end up in the drawing.

R groups. DECIMER writes the R groups of a scheme as `[R]`, `[R1]`, `[R2]`, which RDKit refuses
(there is no element R). Exactly `[R]` and `[R<one or two digits>]` are rewritten before parsing:
`[R1]` becomes the isotope-labelled dummy atom `[1*]`, the usual SMILES form of an attachment
point, and a bare `[R]` becomes `*`. The structure is then valid, its canonical SMILES contains a
`*` (which is what the UI looks for to show the R-group note), its InChIKey is empty (InChI has no
R groups), and the molfile carries `R#` atoms with an `M  RGP` line, which Ketcher shows as R1,
R2. `[Rb]`, `[Rh]`, `[Ra]`, `[Re]`, `[Ru]` are elements and are left alone; `[R1a]`, `[R+]` and
the like stay invalid.

A SMILES RDKit refuses may still be read by CDK; the worker asks chem for CDK's
V2000 molblock and `from_cdk` turns it into the result's values: the molfile is CDK's own, the SVG
is RDKit's drawing of it (read WITHOUT sanitizing, which is what lets it draw what it refused),
the SMILES is the one DECIMER wrote, and `valid` stays False (it is RDKit's word) with an empty
InChIKey. Whether CDK alone accepted a structure is not a field of `Chemistry`: the caller knows,
and says so in `Result.valid_by`.

`analyse_molfile` does the same for a molfile that came from the editor (`/validate`, `/export`).
A text that is not a molfile at all raises `MolfileError`; one that is a molfile but not a valid
structure (an impossible valence, no atoms) is `valid=False`, like an invalid SMILES. A molfile
RDKit reads but will not sanitize is still DRAWN, unsanitized, like what only CDK accepted (empty
SMILES, InChIKey and molfile, as for any invalid structure; the drawing is all the user needs to
see what they drew): otherwise an edit of a structure that stays hypervalent blanks its tile.
`clean_molfile` is what a client's molfile must pass before it is stored, written to a file or
parsed. It also refuses, from the text alone and before RDKit sees a byte, a molfile that is cheap
to write and ruinous to read, or too ambiguous to count the way RDKit will (see `molfile.py`).

The functions are synchronous and CPU-bound (milliseconds); async callers use `asyncio.to_thread`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from rdkit import Chem, RDLogger
from rdkit.Chem import rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D

from .molfile import MolfileError, clean_molfile

log = logging.getLogger("decimer_api.chemistry")

# RDKit writes every parse error to stderr; a bad prediction is a normal outcome here.
RDLogger.DisableLog("rdApp.*")  # type: ignore[attr-defined]  # missing from the RDKit stubs

DRAWING_SIZE = (300, 300)

# `[R]`, `[R1]`, `[R12]`; the number, if any, is the group's label.
R_GROUP_ATOM = re.compile(r"\[R(\d{0,2})\]")


@dataclass(frozen=True)
class Chemistry:
    """The derived values of one structure. `smiles` is the input text when `valid` is False."""

    smiles: str
    inchikey: str
    valid: bool
    molfile: str
    svg: str


def _parser_params() -> Chem.SmilesParserParams:
    params = Chem.SmilesParserParams()
    # RDKit's stubs type these properties as the class itself, not as bool.
    params.allowCXSMILES = False  # type: ignore[assignment]
    params.parseName = False  # type: ignore[assignment]
    params.sanitize = True  # type: ignore[assignment]
    return params


def _invalid(smiles: str) -> Chemistry:
    return Chemistry(smiles=smiles, inchikey="", valid=False, molfile="", svg="")


def analyse_smiles(smiles: str) -> Chemistry:
    """Parse `smiles` and describe it; see the module docstring."""
    text = smiles.strip()
    if not text:
        return _invalid(smiles)
    mol = Chem.MolFromSmiles(_rewrite_r_groups(text), _parser_params())
    if mol is None:
        return _invalid(smiles)
    return describe(mol)


def describe(mol: Chem.Mol) -> Chemistry:
    """Describe a sanitized molecule. The molecule is not modified. One with no atoms (an empty
    canvas, a molfile with nothing in it) is not a structure."""
    if mol.GetNumAtoms() == 0:
        return _invalid("")
    smiles: str = Chem.MolToSmiles(mol)
    return Chemistry(
        smiles=smiles,
        inchikey=_inchikey(mol),
        valid=True,
        molfile=_molfile(mol),
        svg=_svg(mol),
    )


def _rewrite_r_groups(text: str) -> str:
    """`[R1]` -> `[1*]`, `[R]` and `[R0]` -> `[*]`. Nothing else is touched."""

    def dummy(match: re.Match[str]) -> str:
        label = int(match.group(1) or 0)
        return f"[{label}*]" if label > 0 else "[*]"

    return R_GROUP_ATOM.sub(dummy, text)


def _inchikey(mol: Chem.Mol) -> str:
    try:
        key: str = Chem.MolToInchiKey(mol)  # type: ignore[no-untyped-call]
    except Exception:
        log.warning("InChIKey failed", exc_info=True)
        return ""
    return key or ""


def _laid_out(mol: Chem.Mol) -> Chem.Mol:
    """A copy with 2D coordinates: the ones it has (a molfile from the editor keeps its layout),
    else a computed layout. A dummy atom with an isotope number (`[1*]`) is marked as the R
    group of that number, so that the molfile writes `R#` and the drawing says R1."""
    copy = Chem.Mol(mol)
    for atom in copy.GetAtoms():
        label = atom.GetIsotope()
        if atom.GetAtomicNum() == 0 and 1 <= label <= 99 and not atom.HasProp("_MolFileRLabel"):
            atom.SetIntProp("_MolFileRLabel", label)
            atom.SetProp("dummyLabel", f"R{label}")
    if copy.GetNumConformers() == 0:
        rdDepictor.Compute2DCoords(copy)
    return copy


def _molfile(mol: Chem.Mol) -> str:
    try:
        block: str = Chem.MolToMolBlock(_laid_out(mol))
    except Exception:
        log.warning("molfile failed", exc_info=True)
        return ""
    return block


def _svg(mol: Chem.Mol, *, expected_to_fail: bool = False) -> str:
    """The drawing of `mol`, or "" when RDKit cannot draw it. A failure is logged as a warning (a
    structure RDKit accepted should always draw), or at debug level when `expected_to_fail`: one
    RDKit refused, which a client's molfile can make undrawable (every atom at one point) as often
    as it likes."""
    try:
        drawer = rdMolDraw2D.MolDraw2DSVG(*DRAWING_SIZE)
        drawer.drawOptions().clearBackground = False  # type: ignore[assignment]
        drawer.DrawMolecule(_laid_out(mol))
        drawer.FinishDrawing()
        text: str = drawer.GetDrawingText()
    except Exception:
        log.log(
            logging.DEBUG if expected_to_fail else logging.WARNING, "drawing failed", exc_info=True
        )
        return ""
    # RDKit starts with an XML declaration, which is not wanted when the SVG is inlined in HTML.
    start = text.find("<svg")
    return text[start:].strip() if start >= 0 else ""


def _svg_unsanitized(mol: Chem.Mol) -> str:
    """The drawing of a molecule RDKit has read but not sanitized (it refused it, or never tried):
    drawn as it is, from its own coordinates. Valences are worked out without the checks. A drawing
    that fails is an empty SVG."""
    try:
        mol.UpdatePropertyCache(strict=False)
    except Exception:
        log.debug("valences of an unsanitized molecule failed", exc_info=True)
        return ""
    return _svg(mol, expected_to_fail=True)


def _read_unsanitized(molfile: str) -> Chem.Mol | None:
    """RDKit's reading of a cleaned molfile without sanitizing; None when it cannot read it."""
    try:
        return Chem.MolFromMolBlock(molfile, sanitize=False, removeHs=False, strictParsing=False)
    except Exception:  # RDKit raises ValueError and RuntimeError for some inputs
        return None


def from_cdk(smiles: str, molblock: str) -> Chemistry:
    """What a structure that RDKit refused and CDK laid out is, as a result: `smiles` (DECIMER's
    own text, as the invalid result has it), no InChIKey, `valid=False`, the cleaned molblock as the
    molfile and RDKit's drawing of it. Raises `MolfileTooBig` for a molblock that is well formed
    but more than the molfile gate lets through (the same gate every stored molfile passes, so
    that the editor's validate can read it again: the structure stays invalid, as if CDK had
    declined), and `MolfileError` for text that is no usable molfile (see `clean_molfile`), that
    RDKit cannot read at all, or that has no atoms: chem broke its contract. A drawing that fails
    is an empty SVG, not a failure: the molfile is what the user edits and exports."""
    molfile = clean_molfile(molblock)
    mol = _read_unsanitized(molfile)
    if mol is None:
        raise MolfileError("RDKit cannot read it as a molfile")
    if mol.GetNumAtoms() == 0:
        raise MolfileError("it has no atoms")
    return Chemistry(
        smiles=smiles, inchikey="", valid=False, molfile=molfile, svg=_svg_unsanitized(mol)
    )


def analyse_molfile(molfile: str) -> Chemistry:
    """Parse a molfile and describe it. It goes through `clean_molfile` again (cheap, and a no-op
    on a molfile that was cleaned), so that no caller can hand RDKit what the cost gate would have
    refused. Raises `MolfileError` when RDKit cannot read it as a molfile; a structure it reads but
    cannot sanitize comes back with `valid=False`, empty values and its drawing as it is; one with
    no atoms, `valid=False` and empty values."""
    molfile = clean_molfile(molfile)
    mol = _read_unsanitized(molfile)
    if mol is None:
        raise MolfileError("it cannot be read as a molfile")
    drawn = Chem.Mol(mol)  # sanitizing changes `mol`, also when it fails half way
    try:
        Chem.SanitizeMol(mol)
        mol = Chem.RemoveHs(mol)
    except Exception:
        # RDKit's word is `valid=False`, but the drawing is the molfile as it is.
        return Chemistry(
            smiles="", inchikey="", valid=False, molfile="", svg=_svg_unsanitized(drawn)
        )
    return describe(mol)

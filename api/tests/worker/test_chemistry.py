"""The RDKit step of the pipeline (and, later, of /validate): SMILES in, canonical values out."""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405  (our own RDKit output, not untrusted input)

import pytest
from rdkit import Chem

from decimer_api.chemistry import analyse_smiles, describe

ETHANOL_KEY = "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
L_ALANINE_KEY = "QNAYBMKLOCPYGJ-REOHCLBHSA-N"


def test_a_valid_smiles_gets_canonical_values() -> None:
    result = analyse_smiles("OCC")
    assert result.valid is True
    assert result.smiles == "CCO"  # canonical, not the input
    assert result.inchikey == ETHANOL_KEY
    assert result.molfile and result.svg


def test_the_inchikey_keeps_stereochemistry() -> None:
    assert analyse_smiles("N[C@@H](C)C(=O)O").inchikey == L_ALANINE_KEY
    assert analyse_smiles("N[C@H](C)C(=O)O").inchikey != L_ALANINE_KEY  # the D enantiomer


def test_the_molfile_is_v2000_with_2d_coordinates() -> None:
    lines = analyse_smiles("c1ccccc1O").molfile.splitlines()
    assert lines[3].rstrip().endswith("V2000")
    assert lines[1].strip().endswith("2D")
    atom_count = int(lines[3][0:3])
    coordinates = [
        (float(row[0:10]), float(row[10:20]), float(row[20:30]))
        for row in lines[4 : 4 + atom_count]
    ]
    assert all(z == 0.0 for _, _, z in coordinates)
    assert len({(x, y) for x, y, _ in coordinates}) == atom_count  # laid out, not stacked at 0


@pytest.mark.parametrize(
    "smiles", ["N[C@@H](C)C(=O)O", "C/C=C/C", "C[C@H]1CC[C@@H](C)CC1", "c1ccccc1"]
)
def test_the_molfile_round_trips_to_the_same_molecule(smiles: str) -> None:
    """Invariant: reading our molfile back (what Ketcher does) gives the same canonical SMILES,
    stereochemistry included. A molfile without wedge bonds would lose the centre."""
    result = analyse_smiles(smiles)
    back = Chem.MolFromMolBlock(result.molfile)
    assert back is not None
    assert Chem.MolToSmiles(back) == result.smiles


def test_the_svg_is_an_inline_svg_without_a_prolog() -> None:
    svg = analyse_smiles("CCO").svg
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert "<?xml" not in svg
    assert "<script" not in svg.lower()
    root = ET.fromstring(svg)  # noqa: S314
    assert root.tag.endswith("svg")


def test_the_svg_has_no_background_rectangle() -> None:
    """The card behind the drawing supplies the background."""
    assert "fill:#FFFFFF" not in analyse_smiles("CCO").svg


@pytest.mark.parametrize("smiles", ["c1ccccc", "C(C", "not a smiles", "[RX]CC", "CCO foo"])
def test_an_invalid_smiles_keeps_the_raw_text_and_nothing_else(smiles: str) -> None:
    result = analyse_smiles(smiles)
    assert result.valid is False
    assert result.smiles == smiles
    assert (result.inchikey, result.molfile, result.svg) == ("", "", "")


@pytest.mark.parametrize("smiles", ["", "   ", "\n"])
def test_an_empty_prediction_is_invalid(smiles: str) -> None:
    """RDKit parses "" as a molecule with no atoms; that is not a structure."""
    result = analyse_smiles(smiles)
    assert result.valid is False
    assert (result.inchikey, result.molfile, result.svg) == ("", "", "")


def test_cxsmiles_extensions_are_not_honoured() -> None:
    """DECIMER writes plain SMILES. An extension block could put text into the drawing."""
    assert analyse_smiles("CCO |$a;b;c$|").valid is False


def test_a_structure_with_a_dummy_atom_is_valid_but_has_no_inchikey() -> None:
    """R groups: RDKit accepts them, InChI cannot represent them. The UI flags them as R-groups."""
    result = analyse_smiles("*CC(=O)O")
    assert result.valid is True
    assert result.inchikey == ""
    assert result.molfile and result.svg


def test_several_fragments_are_one_valid_structure() -> None:
    result = analyse_smiles("[Na+].[Cl-]")
    assert result.valid is True
    assert result.inchikey.endswith("-M")


def test_describe_takes_a_molecule_for_the_validate_endpoint() -> None:
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    assert describe(mol) == analyse_smiles("CCO")


def test_analysing_does_not_modify_the_input_molecule() -> None:
    mol = Chem.MolFromSmiles("CCO")
    assert mol is not None
    before = Chem.MolToMolBlock(mol)
    describe(mol)
    assert Chem.MolToMolBlock(mol) == before


def test_many_threads_get_the_same_answers_as_one() -> None:
    """The pipeline analyses several structures at once, each in its own thread."""
    from concurrent.futures import ThreadPoolExecutor

    smiles = ["C" * n + "O" for n in range(1, 25)] + ["N[C@@H](C)C(=O)O", "c1ccccc1O", "*CC"]
    serial = [analyse_smiles(s) for s in smiles]
    with ThreadPoolExecutor(max_workers=8) as pool:
        threaded = list(pool.map(analyse_smiles, smiles * 4))
    assert threaded == serial * 4


def test_a_molecule_with_no_atoms_is_not_a_structure() -> None:
    """An empty canvas gives a molfile with no atoms; RDKit reads it as a valid, empty molecule."""
    empty = Chem.MolFromSmiles("")
    assert empty is not None and empty.GetNumAtoms() == 0
    result = describe(empty)
    assert result.valid is False
    assert (result.inchikey, result.molfile, result.svg) == ("", "", "")


def test_a_layout_that_exists_is_kept() -> None:
    """The editor's coordinates are the user's picture; only a molecule without any is laid out."""
    molfile = (
        "\n  test\n\n  2  1  0  0  0  0  0  0  0  0999 V2000\n"
        "   12.5000   -7.2500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "   14.0000   -7.2500    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0\n"
        "  1  2  1  0\nM  END\n"
    )
    mol = Chem.MolFromMolBlock(molfile)
    assert mol is not None
    assert "12.5000   -7.2500" in describe(mol).molfile


# --- R groups (what DECIMER writes: [R], [R1], [R2]) ---------------------------------------------


def test_a_numbered_r_group_becomes_a_labelled_attachment_point() -> None:
    """RDKit does not know the element "R" and refuses `[R1]`. DECIMER writes it for the R groups
    of a scheme, and the UI flags such a structure from the `*` in the SMILES."""
    result = analyse_smiles("[R1]CC(=O)O[R2]")
    assert result.valid is True
    assert result.smiles == "[1*]CC(=O)O[2*]"
    assert result.inchikey == ""  # InChI has no R groups
    assert result.molfile and result.svg


def test_an_unnumbered_r_group_is_a_plain_dummy_atom() -> None:
    result = analyse_smiles("[R]CC(=O)O")
    assert result.valid is True
    assert result.smiles == "*CC(=O)O"
    assert "*" in result.smiles  # what the UI's R-group test looks for


def test_the_molfile_of_a_numbered_r_group_has_r_atoms_and_an_rgp_line() -> None:
    lines = analyse_smiles("[R1]CC(=O)O[R2]").molfile.splitlines()
    atom_symbols = [row[31:34].strip() for row in lines[4:10]]
    assert atom_symbols.count("R#") == 2
    assert "M  RGP  2   1   1   6   2" in lines


@pytest.mark.parametrize("smiles", ["[R1]CC(=O)O[R2]", "[R3]C1CC1", "[R1]C([R1])C"])
def test_the_molfile_of_an_r_group_structure_round_trips(smiles: str) -> None:
    """What Ketcher reads back from our molfile is the structure we showed."""
    result = analyse_smiles(smiles)
    back = Chem.MolFromMolBlock(result.molfile)
    assert back is not None
    assert Chem.MolToSmiles(back) == result.smiles


def test_the_r_group_labels_survive_an_edit_in_the_editor() -> None:
    """`describe` of a molecule read from an editor molfile with R# atoms keeps them as R#."""
    first = analyse_smiles("[R1]CC(=O)O[R2]")
    mol = Chem.MolFromMolBlock(first.molfile)
    assert mol is not None
    again = describe(mol)
    assert again.smiles == first.smiles
    assert "M  RGP  2   1   1   6   2" in again.molfile.splitlines()


@pytest.mark.parametrize("smiles", ["[Rb+].[Cl-]", "[Rh]", "[Ra+2]", "C[Re]", "[Ru]"])
def test_real_elements_that_start_with_r_are_left_alone(smiles: str) -> None:
    """Only `[R]` and `[R<digits>]` are R groups."""
    result = analyse_smiles(smiles)
    assert result.valid is True
    assert "*" not in result.smiles


@pytest.mark.parametrize("smiles", ["[RX]CC", "[R1a]CC", "[R-]CC", "[R+]CC", "[R:1]CC", "[R123]CC"])
def test_anything_else_that_looks_like_an_r_group_is_invalid(smiles: str) -> None:
    assert analyse_smiles(smiles).valid is False


def test_a_dummy_atom_in_the_input_is_not_given_a_label() -> None:
    result = analyse_smiles("*CC(=O)O[R1]")
    assert result.smiles in ("*CC(=O)O[1*]", "[1*]OC(=O)C*")
    atom_symbols = [row[31:34].strip() for row in result.molfile.splitlines()[4:10]]
    assert atom_symbols.count("R#") == 1  # only the one that was written as R1

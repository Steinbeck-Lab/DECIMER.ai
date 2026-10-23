"""`clean_molfile` and `analyse_molfile`: what a client's molfile must be, and what it means."""

from __future__ import annotations

import math
import time

import pytest
from rdkit import Chem
from rdkit.Chem import rdDepictor

from decimer_api.chemistry import analyse_molfile, analyse_smiles
from decimer_api.molfile import (
    MAX_ATOMS,
    MAX_BONDS,
    MAX_NEIGHBOURS,
    MAX_RING_RANK,
    MolfileError,
    clean_molfile,
)
from tests import molfiles
from tests.molfiles import RdkitWasAsked

ETHANOL = analyse_smiles("CCO").molfile
EMPTY = "\n  Ketcher\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n"


def test_a_clean_molfile_is_unchanged() -> None:
    assert clean_molfile(ETHANOL) == ETHANOL


def test_line_breaks_become_line_feeds_and_the_text_ends_at_m_end() -> None:
    messy = ETHANOL.replace("\n", "\r\n").replace("M  END\r\n", "M  END   \r\ntrailing junk\r\n")
    assert clean_molfile(messy) == ETHANOL


def test_a_lone_carriage_return_is_a_line_break() -> None:
    assert clean_molfile(ETHANOL.replace("\n", "\r")) == ETHANOL


def test_unicode_line_separators_are_not_line_breaks() -> None:
    """`str.splitlines` would cut at these; a molfile's lines end at CR and LF only."""
    titled = ETHANOL.replace("RDKit", "a b\x85c")
    assert clean_molfile(titled) == titled


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("", "no M  END"),
        ("title\n\n\n", "no M  END"),
        ("x\nM END\n", "no M  END"),  # one space short
        ("x\n m  end\n", "no M  END"),
        ("a\x00b\nM  END\n", "control"),
        ("a\x1b[31m\nM  END\n", "control"),
        ("a\x7f\nM  END\n", "control"),
        ("\ud800\nM  END\n", "valid text"),
        ("x\n$$$$\nM  END\n", "separator"),
        ("x\n  $$$$ junk\nM  END\n", "separator"),
        ("$$$$\nM  END\n", "separator"),
    ],
)
def test_text_that_cannot_be_stored_or_written_is_refused(text: str, why: str) -> None:
    with pytest.raises(MolfileError, match=why):
        clean_molfile(text)


def test_a_separator_after_m_end_is_dropped_not_refused() -> None:
    assert clean_molfile(ETHANOL + "$$$$\nnext record\n") == ETHANOL


def test_tabs_are_allowed() -> None:
    assert "\t" in clean_molfile(ETHANOL.replace("RDKit", "a\tb"))


def test_a_valid_molfile_is_described_like_a_smiles() -> None:
    chem = analyse_molfile(clean_molfile(ETHANOL))
    assert (chem.smiles, chem.valid, chem.inchikey) == (
        "CCO",
        True,
        "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
    )
    assert chem.svg.startswith("<svg") and chem.molfile.endswith("M  END\n")


def test_the_drawing_keeps_the_layout_the_user_made() -> None:
    """The editor's coordinates are the structure's drawing: they are not computed again."""
    flipped = ETHANOL.replace("-1.2990", " 9.0000")
    kept = Chem.MolFromMolBlock(analyse_molfile(clean_molfile(flipped)).molfile)
    assert kept.GetConformer().GetAtomPosition(0).x == pytest.approx(9.0)


def test_stereo_survives_the_trip() -> None:
    chem = analyse_molfile(clean_molfile(analyse_smiles("C[C@H](N)C(=O)O").molfile))
    assert chem.smiles == "C[C@H](N)C(=O)O"


def test_explicit_hydrogens_are_removed_like_the_worker_does() -> None:
    mol = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    assert analyse_molfile(clean_molfile(Chem.MolToMolBlock(mol))).smiles == "CCO"


def test_no_atoms_is_not_a_structure() -> None:
    chem = analyse_molfile(clean_molfile(EMPTY))
    assert (chem.smiles, chem.inchikey, chem.valid, chem.molfile, chem.svg) == (
        "",
        "",
        False,
        "",
        "",
    )


def test_a_valence_error_is_not_a_structure_but_not_an_error_either() -> None:
    pentavalent = (
        "\n  RDKit          2D\n\n  6  5  0  0  0  0  0  0  0  0999 V2000\n"
        + "".join(
            f"    {x:.4f}    {y:.4f}    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
            for x, y in [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (0.5, 0.5)]
        )
        + "  1  2  1  0\n  1  3  1  0\n  1  4  1  0\n  1  5  1  0\n  1  6  1  0\nM  END\n"
    )
    assert analyse_molfile(clean_molfile(pentavalent)).valid is False


@pytest.mark.parametrize(
    "text",
    [
        "hello\nworld\nM  END\n",
        "M  END\n",
        "\n\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n",  # says 1 atom, has none
        "\n\n\n  x  y  0  0  0  0  0  0  0  0999 V2000\nM  END\n",
    ],
)
def test_text_that_rdkit_cannot_read_as_a_molfile_raises(text: str) -> None:
    with pytest.raises(MolfileError):
        analyse_molfile(clean_molfile(text))


def test_an_r_group_is_a_dummy_atom_in_the_smiles_and_has_no_inchikey() -> None:
    chem = analyse_molfile(clean_molfile(analyse_smiles("[R2]CC").molfile))
    assert chem.valid and chem.smiles == "[2*]CC" and chem.inchikey == ""


def test_a_v3000_molfile_is_read() -> None:
    v3000 = Chem.MolToV3KMolBlock(Chem.MolFromSmiles("c1ccccc1O"))
    assert analyse_molfile(clean_molfile(v3000)).smiles == "Oc1ccccc1"


# --- the cost gate: what is refused from the text alone (review finding 1) -----------------------


def refused(text: str) -> str:
    """The reason `clean_molfile` gives, after checking that it answers within 100 ms: RDKit would
    have needed seconds and gigabytes for what is refused here."""
    started = time.perf_counter()
    with pytest.raises(MolfileError) as caught:
        clean_molfile(text)
    assert time.perf_counter() - started < 0.1
    return str(caught.value)


@pytest.mark.parametrize("count", [44, 60])
def test_the_reviewers_graphs_are_refused_before_rdkit_is_asked(
    count: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Complete graphs of R# atoms: 15 KB and 36 KB, 5.7 s / 484 MB and 46 s / 2.6 GB to parse."""

    def rdkit_must_not_be_asked(*_args: object, **_kwargs: object) -> object:
        # A BaseException, so that no `except Exception` on the way can turn it into a refusal.
        raise RdkitWasAsked("RDKit was asked to read a molfile the gate should have refused")

    monkeypatch.setattr(Chem, "MolFromMolBlock", rdkit_must_not_be_asked)
    text = molfiles.complete_graph_of_dummies(count)
    assert len(text.encode()) < 65536  # under the byte cap: the size alone did not stop them
    assert "too large" in refused(text)
    started = time.perf_counter()
    with pytest.raises(MolfileError):
        analyse_molfile(text)  # `analyse_molfile` has the gate in it, whoever calls it
    assert time.perf_counter() - started < 0.1


def test_a_normal_drug_sized_structure_passes() -> None:
    text = molfiles.drug_like(100)
    assert clean_molfile(text) == text
    assert analyse_molfile(text).valid


def test_a_real_100_atom_molfile_with_stereo_rings_and_charges_passes() -> None:
    smiles = "CC(C)C[C@H](NC(=O)[C@@H](Cc1ccccc1)NC(=O)c1cnccn1)B(O)O.Cl" * 1
    mol = Chem.MolFromSmiles(smiles)
    rdDepictor.Compute2DCoords(mol)
    text = Chem.MolToMolBlock(mol)
    assert clean_molfile(text) == text


def test_the_largest_structures_the_gate_admits_are_still_read_quickly() -> None:
    """The limits are where reading is still cheap (measured: well under 0.1 s each)."""
    for text in (
        molfiles.chain(MAX_ATOMS),
        molfiles.molfile(*molfiles.ring(MAX_ATOMS)),
        molfiles.molfile(*_with_chords(100, MAX_RING_RANK - 1), symbol=lambda _i: "R#"),
    ):
        started = time.perf_counter()
        analyse_molfile(text)
        assert time.perf_counter() - started < 1


def _with_chords(
    atoms: int, chords: int
) -> tuple[list[tuple[float, float]], list[tuple[int, int, int]]]:
    """A ring of `atoms` with `chords` bonds across it: ring rank `chords + 1`, no atom in two."""
    positions, bonds = molfiles.ring(atoms)
    bonds += [(i + 1, i + 1 + atoms // 2, 1) for i in range(chords)]
    return positions, bonds


def test_atoms_up_to_the_limit_pass_and_one_more_does_not() -> None:
    assert clean_molfile(molfiles.chain(MAX_ATOMS))
    assert "301 atoms" in refused(molfiles.chain(MAX_ATOMS + 1))


def test_the_declared_bond_count_is_checked_before_a_line_is_read() -> None:
    declared = molfiles.chain(10).replace(
        "  10  9  0", " 10351  0"
    )  # counts line: 10 atoms, 351 bonds
    declared = declared.split("\n")
    declared[3] = f"{10:3d}{MAX_BONDS + 1:3d}  0  0  0  0  0  0  0  0999 V2000"
    assert "351 bonds" in refused("\n".join(declared))


def test_the_ring_rank_limit_is_inclusive() -> None:
    assert clean_molfile(molfiles.molfile(*_with_chords(100, MAX_RING_RANK - 1)))  # rank 40
    assert "independent rings" in refused(molfiles.molfile(*_with_chords(100, MAX_RING_RANK)))


def test_the_ring_rank_counts_pieces_so_many_small_rings_in_pieces_are_still_rings() -> None:
    """Rank is bonds - atoms + pieces: 41 separate triangles are 41 rings, not zero."""
    atoms, bonds = [], []
    for piece in range(MAX_RING_RANK + 1):
        base = piece * 3
        atoms += [(piece * 5.0, 0.0), (piece * 5.0 + 1, 0.0), (piece * 5.0 + 0.5, 1.0)]
        bonds += [(base + 1, base + 2, 1), (base + 2, base + 3, 1), (base + 3, base + 1, 1)]
    assert "independent rings" in refused(molfiles.molfile(atoms, bonds))
    assert clean_molfile(molfiles.molfile(atoms[:-3], bonds[:-3]))  # 40 of them


def _star(leaves: int) -> str:
    atoms = [(0.0, 0.0)] + [(math.cos(i), math.sin(i)) for i in range(leaves)]
    return molfiles.molfile(atoms, [(1, i + 2, 1) for i in range(leaves)])


def test_no_atom_may_have_more_than_eight_bonds() -> None:
    assert clean_molfile(_star(MAX_NEIGHBOURS))
    assert "bonds at one atom" in refused(_star(MAX_NEIGHBOURS + 1))


def test_the_limits_apply_to_v3000_too() -> None:
    big = molfiles.v3000(
        [(math.cos(i), math.sin(i)) for i in range(MAX_ATOMS + 1)],
        [(i + 1, i + 2, 1) for i in range(MAX_ATOMS)],
    )
    assert "atoms" in refused(big)
    assert "bonds at one atom" in refused(
        molfiles.v3000(
            [(0.0, 0.0)] + [(1.0, float(i)) for i in range(9)], [(1, i + 2, 1) for i in range(9)]
        )
    )
    assert clean_molfile(molfiles.v3000(*molfiles.ring(60)))
    assert "independent rings" in refused(molfiles.v3000(*_with_chords(100, MAX_RING_RANK)))


def test_a_v3000_with_a_continued_line_is_counted_by_its_logical_lines() -> None:
    text = (
        "t\n  RDKit\n\n  0  0  0     0  0            999 V3000\n"
        "M  V30 BEGIN CTAB\nM  V30 COUNTS 2 1 0 0 0\nM  V30 BEGIN ATOM\n"
        "M  V30 1 C 0 0 0 0 -\nM  V30 CHG=1\nM  V30 2 C 1 0 0 0\nM  V30 END ATOM\n"
        "M  V30 BEGIN BOND\nM  V30 1 1 1 2\nM  V30 END BOND\nM  V30 END CTAB\nM  END\n"
    )
    assert clean_molfile(text) == text


@pytest.mark.parametrize(
    ("text", "why"),
    [
        # V2000: the counts line promises more lines than there are
        ("t\n\n\n  5  4  0  0  0  0  0  0  0  0999 V2000\nM  END\n", "fewer atom and bond lines"),
        # a bond to an atom that is not there
        (
            "t\n\n\n  1  1  0  0  0  0  0  0  0  0999 V2000\n"
            "    0.0000    0.0000    0.0000 C   0  0\n  1  9  1  0\nM  END\n",
            "atoms that are not there",
        ),
        # a bond from an atom to itself
        (
            "t\n\n\n  1  1  0  0  0  0  0  0  0  0999 V2000\n"
            "    0.0000    0.0000    0.0000 C   0  0\n  1  1  1  0\nM  END\n",
            "atoms that are not there",
        ),
        # a counts line that is not numbers
        ("t\n\n\n  a  b  0  0  0  0  0  0  0  0999 V2000\nM  END\n", "counts line"),
        ("t\n\n\n -1  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n", "counts line"),
        # a molfile that says V3000 and has a V2000 body, and the other way round
        (molfiles.v2000([(0.0, 0.0)], []).replace("V2000", "V3000"), "not V3000 lines"),
        (molfiles.v3000([(0.0, 0.0)], []).replace("V3000", "V2000"), "V3000 lines in it"),
        # V3000: counts off, two tables, no counts, a bad bond, a duplicate atom
        (
            molfiles.v3000([(0.0, 0.0)] * 3, [(1, 2, 1)]).replace("COUNTS 3 1", "COUNTS 2 1"),
            "do not match",
        ),
        (
            molfiles.v3000([(0.0, 0.0)], []).replace(
                "M  V30 BEGIN CTAB", "M  V30 BEGIN CTAB\nM  V30 BEGIN CTAB"
            ),
            "COUNTS line must follow",
        ),
        (molfiles.v3000([(0.0, 0.0)], []).replace("M  V30 COUNTS 1 0 0 0 0\n", ""), "COUNTS"),
        (molfiles.v3000([(0.0, 0.0)], []).replace("COUNTS 1 0", "COUNTS x y"), "COUNTS"),
        (molfiles.v3000([(0.0, 0.0)] * 2, [(1, 2, 1)]).replace("1 1 1 2", "1 1 1 7"), "not there"),
        (molfiles.v3000([(0.0, 0.0)] * 2, []).replace("M  V30 2 C", "M  V30 1 C"), "do not match"),
    ],
)
def test_a_molfile_that_does_not_say_what_it_holds_is_refused(text: str, why: str) -> None:
    assert why in refused(text)


def test_a_molfile_this_service_wrote_is_not_gated_because_it_is_only_written_to_a_file() -> None:
    big = molfiles.complete_graph_of_dummies(44)
    assert clean_molfile(big, bounded=False) == big
    with pytest.raises(MolfileError):
        clean_molfile(big)  # bounded by default


# --- the version is read the way RDKit reads it (re-review: the V2000 V3000 bypass) ---------------


def test_the_re_reviews_file_is_refused_in_microseconds_and_rdkit_is_not_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A counts line ending `V2000 V3000`, a complete graph of 44 R# atoms in V2000 lines, and a
    one-atom V3000 block after the bonds: RDKit reads the 44-atom graph (5.5 s, 669 MB)."""

    def rdkit_must_not_be_asked(*_args: object, **_kwargs: object) -> object:
        raise RdkitWasAsked("RDKit was asked to read the attack")

    monkeypatch.setattr(Chem, "MolFromMolBlock", rdkit_must_not_be_asked)
    text = molfiles.version_confusion(44)
    assert len(text.encode()) < 65536
    assert "V3000 lines in it" in refused(text)
    with pytest.raises(MolfileError):
        analyse_molfile(text)


def _small_v2000(tail: str) -> str:
    return molfiles.v2000([(0.0, 0.0), (1.0, 0.0)], [(1, 2, 1)], counts_tail=tail)


@pytest.mark.parametrize(
    "tail", [" V2000", "", " ", "   ", " V2000   ", " V2000 junk", " V2000 V3000"]
)
def test_a_counts_line_with_no_version_field_or_with_v2000_there_is_v2000(tail: str) -> None:
    """Columns 35-39: blank or missing is V2000, `V2000` is itself, whatever follows is ignored.
    (` V2000 V3000` is V2000 for RDKit too: only the field counts. It is refused below, as a V2000
    file with V3000 lines, once those are there.)"""
    text = _small_v2000(tail)
    assert clean_molfile(text) == text


@pytest.mark.parametrize(
    "tail",
    [" V2001", " V1000", " v2000", " v3000", " XXXXX", " V300", "  V2000", "  V3000", " V 2000"],
)
def test_a_version_field_that_is_anything_else_is_refused(tail: str) -> None:
    """RDKit reads any other text there as V2000 and ignores it; two readers may then disagree
    about what the file is, so this service refuses it."""
    assert "version" in refused(_small_v2000(tail))


def test_v3000_means_the_field_at_columns_35_to_39_and_not_the_word_anywhere_else() -> None:
    v3 = molfiles.v3000([(0.0, 0.0), (1.0, 0.0)], [(1, 2, 1)])
    assert clean_molfile(v3) == v3
    moved = v3.replace("999 V3000", "999  V3000")  # one column to the right: RDKit reads V2000
    assert "version" in refused(moved)
    word_elsewhere = "title\n\n\n  1  0  0  0  0  0  0  0  0  0999 V2000 V3000\n"
    word_elsewhere += "    0.0000    0.0000    0.0000 C   0  0\nM  END\n"
    assert clean_molfile(word_elsewhere) == word_elsewhere  # a V2000 molfile, as RDKit reads it
    assert rdkit_atoms(word_elsewhere) == 1


def rdkit_atoms(text: str) -> int:
    mol = Chem.MolFromMolBlock(text, sanitize=False, strictParsing=False)
    return int(mol.GetNumAtoms())


def test_v3000_lines_in_a_v2000_file_are_refused_wherever_they_are() -> None:
    block = molfiles.SMALL_V3000_BLOCK
    for extra, title in [(block, None), (block[:1], None), (("M  V30",), None), ((), "M  V30 x")]:
        text = molfiles.v2000([(0.0, 0.0)], [], extra=extra)
        if title:
            text = text.replace("probe", title, 1)
        assert "V3000 lines in it" in refused(text)


def test_a_v3000_file_may_have_nothing_but_v3000_lines_after_its_counts_line() -> None:
    v3 = molfiles.v3000([(0.0, 0.0), (1.0, 0.0)], [(1, 2, 1)])
    lines = v3.split("\n")
    atom_line = "    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0"
    for position in (4, 6, len(lines) - 2):
        mixed = [*lines[:position], atom_line, *lines[position:]]
        assert "not V3000 lines" in refused("\n".join(mixed))
    for odd in ("", "M  CHG  1   1   1", "M  V30", "M  V300 x", "M  V30BEGIN CTAB"):
        mixed = [*lines[:-2], odd, *lines[-2:]]
        assert refused("\n".join(mixed))
    title = v3.replace("probe", "M  V30 BEGIN CTAB", 1)
    assert "title lines" in refused(title)


def test_a_counts_line_with_a_multibyte_character_is_refused() -> None:
    """RDKit reads the counts line's columns by byte, the gate by character."""
    for char in ("é", "中", "\U0001f600"):
        counts = "  1  0  0  0  0  0  0  0  0  0999 V2000"
        atom = "    0.0000    0.0000    0.0000 C   0  0"
        text = f"t\n\n\n{counts[:10]}{char}{counts[10:]}\n{atom}\nM  END\n"
        assert "not ASCII" in refused(text)
    titled = "café\n\n\n" + _small_v2000(" V2000").split("\n", 3)[3]
    assert clean_molfile(titled) == titled  # a title may have any characters


@pytest.mark.parametrize("field", ["+1 ", "1_0", "١  ", "1.0", " -1", "0x1", "1 1", "   "])
def test_a_number_is_ascii_digits_and_spaces_not_what_int_accepts(field: str) -> None:
    text = "t\n\n\n" + field + "  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n"
    assert refused(text)
    bond = molfiles.v2000([(0.0, 0.0), (1.0, 0.0)], [(1, 2, 1)]).replace(
        "  1  2  1  0", f"{field}  2  1  0"
    )
    assert refused(bond)


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (
            lambda t: t.replace(
                "M  V30 END ATOM", "M  V30 END ATOM\nM  V30 BEGIN ATOM\nM  V30 END ATOM"
            ),
            "more than one ATOM",
        ),
        (
            lambda t: t.replace("M  V30 BEGIN BOND", "M  V30 BEGIN BOND\nM  V30 BEGIN BOND"),
            "does not read",
        ),
        (
            lambda t: t.replace(
                "M  V30 END CTAB", "M  V30 BEGIN RGROUP\nM  V30 END RGROUP\nM  V30 END CTAB"
            ),
            "does not read",
        ),
        (lambda t: t.replace("M  V30 END BOND", "M  V30 END ATOM"), "nested"),
        (lambda t: t.replace("M  V30 END BOND\n", ""), "not closed"),
        (lambda t: t.replace("M  V30 END CTAB\n", ""), "must be one connection table"),
        (
            lambda t: t.replace(
                "M  V30 BEGIN CTAB\n", "M  V30 BEGIN CTAB\nM  V30 END CTAB\nM  V30 BEGIN CTAB\n"
            ),
            "COUNTS line must follow",
        ),
    ],
)
def test_a_v3000_table_is_one_table_with_properly_nested_blocks(change: object, why: str) -> None:
    text = change(molfiles.v3000([(0.0, 0.0), (1.0, 0.0)], [(1, 2, 1)]))  # type: ignore[operator]
    assert why in refused(text)


def test_the_blocks_that_carry_no_atoms_are_allowed_in_v3000() -> None:
    v3 = molfiles.v3000([(0.0, 0.0), (1.0, 0.0)], [(1, 2, 1)])
    extra = (
        "M  V30 BEGIN COLLECTION\nM  V30 MDLV30/STEABS ATOMS=(1 1)\n"
        "M  V30 END COLLECTION\nM  V30 END CTAB"
    )
    text = v3.replace("M  V30 END CTAB", extra)
    assert clean_molfile(text) == text


@pytest.mark.parametrize(
    ("ending", "next_start", "ok"),
    [
        (" -", "", True),  # ` -` at the end, the next line starts with a word
        ("-", "", False),  # no space before the dash
        (" - ", "", False),  # a space after it
        ("  -", "", True),
        (" -", " ", False),  # the continuation starts with a space
    ],
)
def test_a_continued_v3000_line_is_only_taken_in_the_plain_form(
    ending: str, next_start: str, ok: bool
) -> None:
    text = (
        "t\n\n\n  0  0  0     0  0            999 V3000\nM  V30 BEGIN CTAB\n"
        "M  V30 COUNTS 1 0 0 0 0\nM  V30 BEGIN ATOM\n"
        f"M  V30 1 C 0 0 0 0{ending}\nM  V30 {next_start}CHG=1\n"
        "M  V30 END ATOM\nM  V30 END CTAB\nM  END\n"
    )
    if ok:
        assert clean_molfile(text) == text
        assert rdkit_atoms(text) == 1
    else:
        assert "continued" in refused(text)


def test_a_continuation_that_never_ends_is_refused() -> None:
    text = molfiles.v3000([(0.0, 0.0)], []).replace("M  V30 END CTAB", "M  V30 END CTAB -")
    assert "continued" in refused(text)


# --- non-ASCII whitespace (third review): Python splits where RDKit does not --------------------


@pytest.mark.parametrize("space", molfiles.UNICODE_SPACES)
@pytest.mark.parametrize("count", [6, 9, 12])
def test_the_whitespace_attack_is_refused_whatever_space_it_uses(
    count: int, space: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RDKit reads a bond `k 1<space>p q r` as (q, r); `str.split` reads (p, q). The gate once
    admitted a 289-atom file of this kind where RDKit saw 16 bonds at one atom and a ring rank
    of 253."""

    def rdkit_must_not_be_asked(*_args: object, **_kwargs: object) -> object:
        raise RdkitWasAsked("RDKit was asked to read the attack")

    monkeypatch.setattr(Chem, "MolFromMolBlock", rdkit_must_not_be_asked)
    text = molfiles.whitespace_confusion(count, space)
    assert "not ASCII" in refused(text)
    with pytest.raises(MolfileError):
        analyse_molfile(text)


@pytest.mark.parametrize("space", ["\u00a0", "\u2003", "\u3000", "\u0085", "\u200b", "\ufeff"])
def test_any_non_ascii_character_in_the_connection_table_is_refused(space: str) -> None:
    """From the counts line to M  END; only the three title lines may hold other text."""
    v3 = molfiles.v3000([(0.0, 0.0), (1.0, 0.0)], [(1, 2, 1)])
    for position in (4, 6, 8, 11):
        lines = v3.split("\n")
        lines[position] = lines[position].replace(" ", space, 1)
        assert "not ASCII" in refused("\n".join(lines))
    v2 = molfiles.v2000([(0.0, 0.0), (1.0, 0.0)], [(1, 2, 1)])
    lines = v2.split("\n")
    lines[4] = lines[4].replace("C ", f"C{space}", 1)
    assert "not ASCII" in refused("\n".join(lines))
    lines = v2.split("\n")
    lines[1] = f"gen{space}erator"  # the title lines are free text
    assert clean_molfile("\n".join(lines)) == "\n".join(lines)


def test_a_tab_in_a_v3000_record_is_refused_and_fields_are_split_at_spaces_only() -> None:
    v3 = molfiles.v3000([(0.0, 0.0), (1.0, 0.0)], [(1, 2, 1)])
    lines = v3.split("\n")
    lines[11] = "M  V30 " + lines[11][7:].replace(" ", "\t", 1)  # a bond line, after its prefix
    assert "tab" in refused("\n".join(lines))

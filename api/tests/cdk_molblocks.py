"""Molblocks as CDK 2.8 writes them, for the tests of the CDK fallback.

Generated with `cdk-2.8.jar` (the jar the earlier Laravel version of decimer.ai ships, sha256
5dff6e36...) through JPype, with the same `SmilesParser`, `StructureDiagramGenerator` and
`MDLV2000Writer` calls as the chem service's `POST /cdk`: the text is CDK's, unchanged, including
its blank title line and the timestamp line (`  CDK     10232608012D`). RDKit refuses the SMILES
of the first (a carbon with five bonds fails sanitization); CDK lays it out as drawn.
"""

from __future__ import annotations

# RDKit refuses C(C)(C)(C)(C)C (valence 5); CDK accepts it.
PENTAVALENT_SMILES = "C(C)(C)(C)(C)C"
PENTAVALENT_MOLBLOCK = (
    "\n"
    "  CDK     10232608012D\n"
    "\n"
    "  6  5  0  0  0  0  0  0  0  0999 V2000\n"
    "    0.0000    1.5000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "   -1.2990    0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    0.0000    3.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "   -0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    1.2990    0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    1.2990    2.2500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "  1  2  1  0  0  0  0\n"
    "  1  3  1  0  0  0  0\n"
    "  1  4  1  0  0  0  0\n"
    "  1  5  1  0  0  0  0\n"
    "  1  6  1  0  0  0  0\n"
    "M  END\n"
)

# L-alanine (RDKit accepts it too): a stereocentre as a wedge bond.
ALANINE_SMILES = "C[C@H](N)C(=O)O"
ALANINE_MOLBLOCK = (
    "\n"
    "  CDK     10232608012D\n"
    "\n"
    "  6  5  0  0  1  0  0  0  0  0999 V2000\n"
    "   -0.0000   -0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "   -0.0000    1.5000    0.0000 C   0  0  2  0  0  0  0  0  0  0  0  0\n"
    "    1.2990    2.2500    0.0000 N   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "   -1.2990    2.2500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "   -2.5981    1.5000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "   -1.2990    3.7500    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "  1  2  1  0  0  0  0\n"
    "  2  3  1  1  0  0  0\n"
    "  2  4  1  0  0  0  0\n"
    "  4  5  2  0  0  0  0\n"
    "  4  6  1  0  0  0  0\n"
    "M  END\n"
)

# CDK accepts a sulfur with nine bonds; RDKit refuses it, and so does our own molfile gate (more
# than 8 bonds at one atom, `MAX_NEIGHBOURS`), which is not a fault of the molblock.
NINE_BONDS_SMILES = "C[S](C)(C)(C)(C)(C)(C)(C)C"
NINE_BONDS_MOLBLOCK = (
    "\n"
    "  CDK     10232608242D\n"
    "\n"
    " 10  9  0  0  0  0  0  0  0  0999 V2000\n"
    "   -1.2488    0.8370    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    0.0502    1.5870    0.0000 S   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    0.0502    3.0870    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "   -0.6998    0.2880    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    0.0502    0.0870    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    0.8002    0.2880    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    1.3493    0.8370    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    1.5502    1.5870    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    1.3493    2.3370    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "    0.8002    2.8861    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    "  1  2  1  0  0  0  0\n"
    "  2  3  1  0  0  0  0\n"
    "  2  4  1  0  0  0  0\n"
    "  2  5  1  0  0  0  0\n"
    "  2  6  1  0  0  0  0\n"
    "  2  7  1  0  0  0  0\n"
    "  2  8  1  0  0  0  0\n"
    "  2  9  1  0  0  0  0\n"
    "  2 10  1  0  0  0  0\n"
    "M  END\n"
)

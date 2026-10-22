"""A str subclass that only the naming process ever imports.

Orthonym's names can be `str` subclasses (`PrefixName` carries a derivation object). If this module
is ever in the web process's `sys.modules`, the web process unpickled something it should not have:
that imports the class's whole module tree (for Orthonym, RDKit and the engine: 42 MB to 104 MB).
"""

from __future__ import annotations


class Derived(str):
    derivation: object = object()

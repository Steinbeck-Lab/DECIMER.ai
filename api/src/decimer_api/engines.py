"""The engines behind the results, for `GET /api/engines`.

The model services run in their own images, so the api cannot ask a package what it is: their
versions are listed here, next to the pins in `services/*/uv.lock` that they mirror, and a test
compares the two (`tests/test_engines.py`) so that a bump of a pin cannot leave this list behind.
What the api itself runs (RDKit, which makes every SMILES, InChIKey and drawing) is read from the
installed package. Nothing here calls a service, so the endpoint is as cheap as a dictionary.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import metadata


@dataclass(frozen=True)
class Engine:
    id: str
    name: str
    role: str
    version: str
    revision: str | None = None


# The model services' pins. `revision` is the git commit of a version that has no release.
MODEL_ENGINES = (
    Engine("decimer", "DECIMER Image Transformer", "ocsr", "2.8.1"),
    Engine("decimer-segmentation", "DECIMER Segmentation", "segmentation", "1.6.0"),
    Engine(
        "decimer-image-classifier",
        "DECIMER Image Classifier",
        "classification",
        "1.0.1",
        "146b00be2fe6f8fa6670a4255969d4747502b7f2",
    ),
    Engine("orthonym", "Orthonym", "naming", "1.0.8", "110ed8153b365973ad90c518a3ea96fbebb13c8b"),
    Engine("opsin", "OPSIN", "name verification", "2.9.0"),
)


@cache
def engines() -> tuple[Engine, ...]:
    """Every engine, the same for the life of the process."""
    return (*MODEL_ENGINES, Engine("rdkit", "RDKit", "chemistry", metadata.version("rdkit")))

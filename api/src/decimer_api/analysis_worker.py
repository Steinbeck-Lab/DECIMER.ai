"""The molfile-reading child: `python -I -m decimer_api.analysis_worker`.

RDKit's cost for a molfile is not bounded by what the text gate (`molfile.py`) can see, and a
gate is a copy of a parser. So the api never reads a client's molfile itself: it has a few of
these processes (`analysis_pool.AnalysisPool`), each reading one molfile at a time, with a hard
limit on its address space and a wall-clock timeout set by the parent, which kills and replaces a
child that is slow, out of memory or dead. The api process, and every other request, never notice.
Reading in the api's own thread would hold its GIL (RDKit does not let go of it) and stall every
request for as long as the molfile takes.

Protocol (`analysis_protocol`): the parent sends `{"memory_mb": N}`; the child loads RDKit, reads
a small structure to warm up, lowers its limits for good, and answers `{"ready": true}`. Then, for
each `{"molfile": "..."}` it answers one of

    {"ok": {"smiles", "inchikey", "valid", "molfile", "svg"}}
    {"error": "molfile", "message": "..."}        RDKit cannot read it as a molfile
    {"error": "too_complex"}                      MemoryError: the limit was reached
    {"error": "internal", "message": "..."}       a bug; the parent replaces this child

At the end of its stdin it exits. Answers go to a private copy of fd 1 (the real stdout is sent to
/dev/null), so nothing a library prints can break the framing. It dies with its parent
(PR_SET_PDEATHSIG).
"""

from __future__ import annotations

import ctypes
import os
import resource
import signal
import sys
import traceback
from typing import Any, BinaryIO

from . import analysis_protocol as protocol
from .chemistry import analyse_molfile, analyse_smiles
from .hardening import current_address_space
from .molfile import MolfileError

MIB = 1024 * 1024
OPEN_FILES = 16
PR_SET_PDEATHSIG = 1


def apply_limits(*, memory_mb: int) -> None:
    """Lower this process's limits for good (soft and hard). `memory_mb` is what the process may
    allocate beyond what it uses now, so that the interpreter and RDKit do not eat into it."""

    def lower(which: int, value: int) -> None:
        resource.setrlimit(which, (value, value))

    lower(resource.RLIMIT_CORE, 0)
    lower(resource.RLIMIT_FSIZE, 0)  # never writes a file
    lower(resource.RLIMIT_NOFILE, OPEN_FILES)
    lower(resource.RLIMIT_AS, current_address_space() + memory_mb * MIB)


def die_with_the_parent() -> None:
    try:
        ctypes.CDLL(None).prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    except (OSError, AttributeError):
        pass  # the end of stdin does the same, a moment later


def answer_for(molfile: str) -> dict[str, Any]:
    try:
        chemistry = analyse_molfile(molfile)
    except MolfileError as error:
        return {"error": "molfile", "message": str(error)}
    except MemoryError:
        return {"error": "too_complex"}
    except Exception as error:
        traceback.print_exc()
        return {"error": "internal", "message": type(error).__name__}
    return {
        "ok": {
            "smiles": chemistry.smiles,
            "inchikey": chemistry.inchikey,
            "valid": chemistry.valid,
            "molfile": chemistry.molfile,
            "svg": chemistry.svg,
        }
    }


def serve(requests: BinaryIO, answers: BinaryIO) -> int:
    hello = protocol.read_blocking(requests)
    if hello is None:
        return 0
    analyse_molfile(analyse_smiles("CCO").molfile)  # warm up: what it costs is in the baseline
    apply_limits(memory_mb=int(hello["memory_mb"]))
    answers.write(protocol.encode({"ready": True}))
    while (request := protocol.read_blocking(requests)) is not None:
        answers.write(protocol.encode(answer_for(str(request["molfile"]))))
    return 0


def main() -> int:
    die_with_the_parent()
    answers = os.fdopen(os.dup(1), "wb", buffering=0)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)  # a library that prints goes nowhere, not into the framing
    os.close(devnull)
    return serve(sys.stdin.buffer, answers)


if __name__ == "__main__":
    raise SystemExit(main())

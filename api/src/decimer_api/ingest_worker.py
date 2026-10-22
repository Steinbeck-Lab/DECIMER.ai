"""The ingest child process: `python -I -m decimer_api.ingest_worker`.

The API never parses an upload itself. It starts this process for every upload, which

1. reads one JSON request on stdin (paths of the staged files, the limits),
2. puts hard limits on itself before touching any file (CPU time, address space, file size,
   open files, no core dumps),
3. runs `ingest`, and
4. prints one JSON answer on stdout: `{"ok": {...}}` or `{"error": {"code": ..., "message": ...}}`.

If the file defeats the parser (a crash, an endless loop, a decompression bomb), this process
dies or is killed by its parent and the API answers with a clean error; the API process and
every other upload are unaffected.
"""

from __future__ import annotations

import json
import os
import resource
import sys
import traceback
from pathlib import Path

from .errors import ApiError
from .ingest import run_guarded
from .uploads import Ingested, Limits, Upload, processing_limit

MIB = 1024 * 1024
OPEN_FILES = 64


def current_address_space() -> int:
    """Bytes of address space in use now (Linux), or 0 when unknown."""
    try:
        with open("/proc/self/statm") as statm:
            return int(statm.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


def apply_limits(
    *, memory_mb: int, cpu_seconds: int, max_file_bytes: int, open_files: int = OPEN_FILES
) -> None:
    """Lower this process's limits for good (soft and hard, so they cannot be raised again).

    `memory_mb` is what the process may allocate beyond what it already uses, so that the cost
    of the interpreter and the libraries does not eat into the budget of the file.
    """

    def lower(which: int, value: int) -> None:
        resource.setrlimit(which, (value, value))

    lower(resource.RLIMIT_CORE, 0)
    lower(resource.RLIMIT_CPU, cpu_seconds)
    lower(resource.RLIMIT_FSIZE, max_file_bytes)
    lower(resource.RLIMIT_NOFILE, open_files)
    lower(resource.RLIMIT_AS, current_address_space() + memory_mb * MIB)


def _answer(result: Ingested) -> dict[str, object]:
    return {
        "ok": {
            "pages": result.pages,
            "pages_processed": result.pages_processed,
            "truncated": result.truncated,
            "warnings": [{"code": w.code, "message": w.message} for w in result.warnings],
        }
    }


def _error(error: ApiError) -> dict[str, object]:
    return {"error": {"code": error.code, "message": error.message}}


def main() -> int:
    request = json.load(sys.stdin)
    # Built before the limits apply, so that reporting a memory failure cannot itself fail.
    out_of_memory = json.dumps(_error(processing_limit()))
    apply_limits(
        memory_mb=request["memory_mb"],
        cpu_seconds=request["cpu_seconds"],
        max_file_bytes=request["max_file_bytes"],
        open_files=OPEN_FILES + len(request["files"]),  # every upload is open at once
    )
    files = []
    try:
        for item in request["files"]:
            files.append(open(item["path"], "rb"))  # noqa: SIM115  (closed in finally)
        uploads = [
            Upload(name=item["name"], data=handle)
            for item, handle in zip(request["files"], files, strict=True)
        ]
        answer = json.dumps(
            _answer(run_guarded(uploads, Path(request["input_dir"]), Limits(**request["limits"])))
        )
    except ApiError as error:
        answer = json.dumps(_error(error))
    except MemoryError:
        answer = out_of_memory
    except Exception:
        traceback.print_exc()
        answer = json.dumps({"error": {"code": "internal_error", "message": "ingest failed"}})
    finally:
        for handle in files:
            handle.close()
    sys.stdout.write(answer)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

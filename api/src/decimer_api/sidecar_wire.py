"""The wire between the api and the ingest sidecar: length-prefixed JSON frames
(`analysis_protocol`: a 4-byte big-endian length, then UTF-8 JSON) and raw file bodies of a length
the frame announced, over one TCP connection per request. No HTTP and no framework: whoever reads
this from the untrusted side (the api reads what a possibly compromised parser's container wrote)
has a few lines to check.

An ingest request is

    api -> sidecar   {"op": "ingest", "files": [{"name", "size"}, ...]}  then each file's bytes
    sidecar -> api   {"ok": {"pages", "pages_processed", "truncated", "warnings", "files": [{"name",
                     "size"}, ...]}} then each normalised file's bytes,
                     or {"error": {"status", "code", "message", "headers"}}

and an analysis session (several molfiles on one connection, one place in the sidecar's pool)

    api -> sidecar   {"op": "analysis"}               sidecar -> api   {"ready": true} or {"error":
    ...} api -> sidecar   {"molfile": "..."}               sidecar -> api   {"ok": {...}} |
    {"error": ...} (repeated; the api closes the connection to end the session)

and `{"op": "ping"}` answers `{"ok": true}`.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, BinaryIO

from . import analysis_protocol as protocol

CHUNK_BYTES = 1024 * 1024
FrameError = protocol.FrameError


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    """One frame; `IncompleteReadError` at the end of the stream, `FrameError` for nonsense."""
    (length,) = protocol.HEADER.unpack(await reader.readexactly(protocol.HEADER.size))
    if length > protocol.MAX_FRAME_BYTES:
        raise FrameError("the frame is too large")
    return protocol.decode(await reader.readexactly(length))


async def write_frame(writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
    writer.write(protocol.encode(message))
    await writer.drain()


async def send_file(writer: asyncio.StreamWriter, source: BinaryIO, size: int) -> None:
    """Exactly `size` bytes of `source` (from its current position), without blocking the loop."""
    remaining = size
    while remaining > 0:
        chunk = await asyncio.to_thread(source.read, min(CHUNK_BYTES, remaining))
        if not chunk:
            raise FrameError("the file is shorter than announced")
        writer.write(chunk)
        await writer.drain()
        remaining -= len(chunk)


async def receive_file(reader: asyncio.StreamReader, target: BinaryIO, size: int) -> None:
    """Exactly `size` bytes from the stream into `target`."""
    remaining = size
    while remaining > 0:
        chunk = await reader.readexactly(min(CHUNK_BYTES, remaining))
        await asyncio.to_thread(target.write, chunk)
        remaining -= len(chunk)


async def ping(host: str, port: int, timeout: float = 3.0) -> bool:
    """Whether a sidecar answers at `host:port` (its health check, and the api's readiness)."""
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(host, port)
            try:
                await write_frame(writer, {"op": "ping"})
                return (await read_frame(reader)).get("ok") is True
            finally:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
    except (TimeoutError, OSError, FrameError, asyncio.IncompleteReadError):
        return False

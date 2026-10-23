"""The framing between the api and its molfile-reading children: a 4-byte big-endian length, then
that many bytes of UTF-8 JSON. Imports nothing that is not in the standard library, so the child
can read its first request before it loads RDKit."""

from __future__ import annotations

import json
import struct
from typing import Any, BinaryIO

HEADER = struct.Struct(">I")
MAX_FRAME_BYTES = 16 * 1024 * 1024  # an SVG of a 300-atom structure is a few hundred KB


class FrameError(Exception):
    """The other side sent something that is not a frame."""


def encode(message: dict[str, Any]) -> bytes:
    body = json.dumps(message, ensure_ascii=False).encode("utf-8", "surrogatepass")
    if len(body) > MAX_FRAME_BYTES:
        raise FrameError("the message is too large")
    return HEADER.pack(len(body)) + body


def decode(body: bytes) -> dict[str, Any]:
    try:
        message = json.loads(body.decode("utf-8", "surrogatepass"))
    except ValueError as error:
        raise FrameError("the message is not JSON") from error
    if not isinstance(message, dict):
        raise FrameError("the message is not an object")
    return message


def read_blocking(stream: BinaryIO) -> dict[str, Any] | None:
    """One message from a blocking stream; None at the end of the stream (the parent went away)."""
    header = stream.read(HEADER.size)
    if len(header) < HEADER.size:
        return None
    (length,) = HEADER.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise FrameError("the message is too large")
    body = stream.read(length)
    if len(body) < length:
        return None
    return decode(body)

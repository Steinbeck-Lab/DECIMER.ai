"""Fake seg, cls, ocsr and chem services behind `httpx.MockTransport`. No network, no models.

The fake seg builds crops whose pixel size says which crop they are: the crop `k` of page `p` is
(40 + k) x (30 + p) pixels. The fake ocsr and cls read that back, so a test can tell which crop
produced which result, and ordering mistakes cannot hide behind identical answers. The SMILES
of crop (p, k) is an alkane chain, distinct for every (p, k) up to page 9 and crop 9.
"""

from __future__ import annotations

import asyncio
import base64
import io
import struct
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser

import httpx
from PIL import Image

Hook = Callable[[httpx.Request, dict[str, bytes]], httpx.Response | None]


def parse_multipart(request: httpx.Request) -> dict[str, bytes]:
    """The parts of a multipart/form-data request, by field name."""
    header = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
    message = BytesParser(policy=policy.HTTP).parsebytes(header + request.read())
    parts: dict[str, bytes] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        assert isinstance(name, str) and isinstance(payload, bytes)
        parts[name] = payload
    return parts


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def make_crop(page: int, index: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (40 + index, 30 + page), (255, 255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def crop_id(data: bytes) -> tuple[int, int]:
    """(page, index) of a crop made by `make_crop`."""
    width, height = png_size(data)
    return height - 30, width - 40


def smiles_for(page: int, index: int) -> str:
    return "C" * (2 + page * 10 + index)


def bbox_for(page: int, index: int) -> tuple[int, int, int, int]:
    """Where the fake seg puts crop `index` of `page`: a grid of five columns, so that up to
    thirty crops fit on a 600 x 600 page, and every box is the size of its crop."""
    x0, y0 = 10 + (index % 5) * 100, 20 + (index // 5) * 60
    return (x0, y0, x0 + 40 + index, y0 + 30 + page)


def seg_json(
    page_index: int, width: int, height: int, boxes: list[tuple[int, int, int, int]]
) -> dict[str, object]:
    """A seg answer with one segment per box; each crop is a white PNG of its box's size."""
    segments = []
    for index, (x0, y0, x1, y1) in enumerate(boxes):
        buffer = io.BytesIO()
        Image.new("RGBA", (x1 - x0, y1 - y0), (255, 255, 255, 255)).save(buffer, format="PNG")
        segments.append(
            {
                "index": index,
                "bbox": [x0, y0, x1, y1],
                "png": base64.b64encode(buffer.getvalue()).decode(),
            }
        )
    return {"page_index": page_index, "width": width, "height": height, "segments": segments}


def json_response(payload: object, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers)


@dataclass
class FakeBackend:
    """Set `layout` (page index -> number of segments), then pass `transport` to httpx.

    `hooks["ocsr"]` (or seg, cls, chem) is called first with the request and its parts; a
    response it returns replaces the normal answer, and an exception it raises reaches httpx
    (use that for connection errors).
    """

    layout: dict[int, int] = field(default_factory=dict)
    names: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)  # chem decline reasons by SMILES
    verifieds: dict[str, str] = field(default_factory=dict)  # how a name was checked, by SMILES
    delay: float = 0.0
    service_delays: dict[str, float] = field(default_factory=dict)  # on top of `delay`
    page_delays: dict[int, float] = field(default_factory=dict)  # seg only, by page index
    hooks: dict[str, Hook] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, bytes]]] = field(default_factory=list)
    active: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    peak: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def count(self, service: str) -> int:
        return sum(1 for name, _ in self.calls if name == service)

    def parts_of(self, service: str) -> list[dict[str, bytes]]:
        return [parts for name, parts in self.calls if name == service]

    async def handle(self, request: httpx.Request) -> httpx.Response:
        service = request.url.host.split(".")[0]  # seg.test, ocsr.test, ...
        if request.headers.get("content-type", "").startswith("multipart/"):
            parts = parse_multipart(request)
        else:
            parts = {"json": request.content}
        self.calls.append((service, parts))
        self.active[service] += 1
        self.peak[service] = max(self.peak[service], self.active[service])
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if pause := self.service_delays.get(service, 0):
                await asyncio.sleep(pause)
            if service == "seg" and (pause := self.page_delays.get(int(parts["page_index"]), 0)):
                await asyncio.sleep(pause)
            hook = self.hooks.get(service)
            if hook is not None and (response := hook(request, parts)) is not None:
                return response
            return self.answer(service, request, parts)
        finally:
            self.active[service] -= 1

    def answer(
        self, service: str, request: httpx.Request, parts: dict[str, bytes]
    ) -> httpx.Response:
        if service == "seg":
            return self._seg(parts)
        if service == "ocsr":
            page, index = crop_id(parts["image"])
            # Anything that is not one of the crops made by `make_crop` (a whole image, a
            # downscaled copy) is ethanol.
            smiles = smiles_for(page, index) if 0 <= page < 10 and 0 <= index < 10 else "CCO"
            return json_response({"smiles": smiles, "token_confidence": [], "mean_confidence": 0.9})
        if service == "cls":
            return json_response({"is_structure": True, "score": 1e-9})
        if service == "chem":
            import json

            assert request.url.path == "/names"
            results = []
            for smiles in json.loads(parts["json"])["smiles"]:
                name, tier = self.names.get(smiles, (None, None))
                reason = None if name else self.reasons.get(smiles, "no_verified_name")
                results.append(
                    {
                        "name": name,
                        "tier": tier,
                        "verified": self.verifieds.get(smiles, "opsin") if name else None,
                        "reason": reason,
                    }
                )
            return json_response({"results": results})
        raise AssertionError(f"unexpected service {service}")

    def _seg(self, parts: dict[str, bytes]) -> httpx.Response:
        page = int(parts["page_index"])
        width, height = png_size(parts["image"])
        segments = []
        for index in range(self.layout.get(page, 0)):
            crop = make_crop(page, index)
            segments.append(
                {
                    "index": index,
                    "bbox": list(bbox_for(page, index)),
                    "png": base64.b64encode(crop).decode(),
                }
            )
        return json_response(
            {"page_index": page, "width": width, "height": height, "segments": segments}
        )


class FakeSleep:
    """Replaces asyncio.sleep in the retry loop: records the delays and returns at once."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        await asyncio.sleep(0)

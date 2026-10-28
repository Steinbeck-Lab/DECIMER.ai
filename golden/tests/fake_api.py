"""A scripted stand-in for the new stack's job API, shaped like JobView in
web/src/api/openapi.json (camelCase), for httpx.MockTransport."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from decimer_golden.score import to_inchikey

JOB_ID = "0123456789abcdef0123456789abcdef"
PNG = b"\x89PNG\r\n\x1a\n" + b"not really an image"
STAGE_NAMES = ["upload", "render", "segment", "recognise", "check"]
CAFFEINE = "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"
BENZENE = "c1ccccc1"
RUNAWAY = 500  # requests after which a loop that should have ended is reported, not waited for


def stages(done: int, *, active: bool = True) -> list[dict[str, str]]:
    """`done` stages after upload are done, the next one is active."""
    out = []
    for position, name in enumerate(STAGE_NAMES):
        if position <= done:
            state = "done"
        elif position == done + 1 and active:
            state = "active"
        else:
            state = "pending"
        out.append({"name": name, "state": state})
    return out


def result_json(
    index: int,
    smiles: str,
    *,
    page_index: int = 2,
    valid: bool | None = None,
    is_structure: bool | None = True,
    confidence: float = 0.9,
    name: str | None = None,
    name_tier: str | None = None,
    inchikey: str | None = None,
    crop_url: str | None = None,
    incomplete: bool | None = None,  # None: the field is left out, as an API without the flag does
) -> dict[str, Any]:
    key = to_inchikey(smiles)
    result: dict[str, Any] = {
        "index": index,
        "number": index + 1,
        "pageIndex": page_index,
        "bbox": [1, 2, 3, 4],
        "smiles": smiles,
        "inchikey": (key or "") if inchikey is None else inchikey,
        "valid": (key is not None) if valid is None else valid,
        "isStructure": is_structure,
        "confidence": confidence,
        "name": name,
        "nameTier": name_tier,
        "nameVerified": None,
        "svg": "<svg/>",
        "molfile": "",
        "cropUrl": crop_url or f"/api/jobs/{JOB_ID}/media/crop-{index}.png",
    }
    if incomplete is not None:
        result["incomplete"] = incomplete
    return result


def job_json(
    status: str = "done",
    *,
    done: int = 4,
    active: bool | None = None,  # a running job has an active stage
    results: list[dict[str, Any]] | None = None,
    found: int | None = None,
    dropped: int = 0,
    warnings: list[tuple[str, str]] | None = None,
    error: str | None = None,
    queue_position: int | None = None,
    pages: int = 10,
) -> dict[str, Any]:
    results = results or []
    return {
        "id": JOB_ID,
        "status": status,
        "stages": stages(done, active=status == "running" if active is None else active),
        "pages": pages,
        "pagesProcessed": pages,
        "truncated": bool(dropped),
        "structuresFound": len(results) + dropped if found is None else found,
        "structuresDropped": dropped,
        "warnings": [{"code": code, "message": message} for code, message in warnings or []],
        "error": error,
        "createdAt": "2026-10-22T12:00:00Z",
        "expiresAt": "2026-10-22T13:00:00Z",
        "queuePosition": queue_position,
        "results": results,
    }


def lifecycle(final: dict[str, Any]) -> list[dict[str, Any]]:
    """What POST and the next polls answer: queued, then each stage in turn, then `final`."""
    return [
        job_json("queued", done=0, active=False, queue_position=1),
        job_json("running", done=0),
        job_json("running", done=1),
        job_json("running", done=2),
        final,
    ]


def error_json(code: str, message: str = "nope") -> dict[str, Any]:
    return {"detail": {"code": code, "message": message}}


class FakeApi:
    """`states[0]` is the answer to POST /api/jobs, `states[1:]` those of the polls in turn (the
    last one repeats). `post_replies` and `get_replies` are served first, one per request: a
    Response, or an exception to raise (a network error)."""

    def __init__(
        self,
        states: list[dict[str, Any]],
        *,
        post_replies: Sequence[httpx.Response | Exception] | None = None,
        get_replies: Sequence[httpx.Response | Exception] | None = None,
        media: httpx.Response | None = None,
    ) -> None:
        self.states = states
        self.post_replies: list[httpx.Response | Exception] = list(post_replies or [])
        self.get_replies: list[httpx.Response | Exception] = list(get_replies or [])
        self.media = media
        self.requests: list[httpx.Request] = []
        self.polled = 0

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self), base_url="http://api.test")

    def paths(self, method: str) -> list[str]:
        return [str(r.url) for r in self.requests if r.method == method]

    @staticmethod
    def _reply(reply: httpx.Response | Exception) -> httpx.Response:
        if isinstance(reply, Exception):
            raise reply
        return reply

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if len(self.requests) > RUNAWAY:
            raise RuntimeError("runaway: the client kept sending requests")
        path = request.url.path
        if request.method == "POST" and path == "/api/jobs":
            if self.post_replies:
                return self._reply(self.post_replies.pop(0))
            return httpx.Response(
                202, json=self.states[0], headers={"Location": f"/api/jobs/{JOB_ID}"}
            )
        if request.method == "GET" and path == f"/api/jobs/{JOB_ID}":
            if self.get_replies:
                return self._reply(self.get_replies.pop(0))
            self.polled += 1
            return httpx.Response(200, json=self.states[min(self.polled, len(self.states) - 1)])
        if request.method == "GET" and path.startswith(f"/api/jobs/{JOB_ID}/media/crop-"):
            return self.media or httpx.Response(
                200, content=PNG, headers={"content-type": "image/png"}
            )
        return httpx.Response(404, json=error_json("not_found"))


class Clock:
    """A clock that only moves when the code sleeps, so timing is exact and nothing waits."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

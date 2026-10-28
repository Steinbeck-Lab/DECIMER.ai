"""The production wiring: arq enqueue, the app built from the environment, the uvicorn config."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from decimer_api import app as app_module
from decimer_api import queue as queue_module
from decimer_api.__main__ import uvicorn_config
from decimer_api.app import create_app_from_env
from decimer_api.queue import PROCESS_JOB, ArqEnqueuer
from decimer_api.settings import Settings


class StubPool:
    def __init__(self, result: object = object()) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.result = result
        self.closed = False

    async def enqueue_job(self, *args: Any, **kwargs: Any) -> object:
        self.calls.append((args, kwargs))
        return self.result

    async def aclose(self) -> None:
        self.closed = True


async def test_arq_enqueue_passes_the_job_id_and_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = StubPool()
    created: list[Any] = []

    async def create_pool(redis_settings: Any) -> StubPool:
        created.append(redis_settings)
        return pool

    monkeypatch.setattr(queue_module, "create_pool", create_pool)
    enqueue = ArqEnqueuer("redis://redis:6379/3")
    await enqueue("a" * 32)
    await enqueue("b" * 32)
    assert len(created) == 1  # one pool, reused
    assert created[0].host == "redis" and created[0].database == 3
    # The queue is named in the call (the default lane, `arq:queue`).
    assert pool.calls[0] == (
        (PROCESS_JOB, "a" * 32),
        {"_job_id": "a" * 32, "_queue_name": "arq:queue"},
    )
    await enqueue.aclose()
    assert pool.closed


async def test_arq_enqueue_fails_loudly_when_arq_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    async def create_pool(_settings: Any) -> StubPool:
        return StubPool(result=None)

    monkeypatch.setattr(queue_module, "create_pool", create_pool)
    with pytest.raises(RuntimeError, match="already queued"):
        await ArqEnqueuer("redis://redis:6379/0")("c" * 32)


async def test_app_from_env_starts_without_redis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens here
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(app_module, "make_undumpable", lambda: True)
    app = create_app_from_env()
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test")
    assert (await client.get("/healthz")).status_code == 200
    assert (await client.get("/readyz")).status_code == 503  # Redis is unreachable


def test_uvicorn_never_rewrites_the_peer_address() -> None:
    config = uvicorn_config(Settings(host="127.0.0.1", port=8123))
    assert config.proxy_headers is False
    assert config.access_log is False
    assert config.factory is True
    assert (config.host, config.port) == ("127.0.0.1", 8123)


def test_the_production_app_makes_the_process_non_dumpable_before_it_serves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """Otherwise the ingest children, which run as the same user, can read this process's
    environment (IP_HASH_SECRET, REDIS_URL) from /proc."""
    calls: list[int] = []
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "jobs"))
    monkeypatch.setattr(app_module, "make_undumpable", lambda: calls.append(1) or True)
    create_app_from_env()
    assert calls == [1]

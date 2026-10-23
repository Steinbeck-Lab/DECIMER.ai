"""The whole stack for the end-to-end test: the API, a real arq worker, real Redis (or fakeredis),
and the four model services as fakes that speak HTTP.

Everything the product runs is the product's own code: `create_app` with its production wiring
(the subprocess ingest, the molfile pool, the chem namer), `ArqEnqueuer`, arq's `Worker` with
`arq_app.make_startup` (the production deps: the shared HTTP client, the replica pools, a child
process per rendered page). What is not real is what cannot be in CI: the models.

* The model services are `tests/worker/fakes.py::FakeBackend` (the same fake the worker tests use,
  so the JSON contracts are in one place) behind four uvicorn servers on 127.0.0.1, in one thread
  of their own: the worker's HTTP client, its multipart bodies, its timeouts and its replica
  discovery all run for real. A `Gate` holds calls to a service until a test lets them go.
* Redis is the one in `E2E_REDIS_URL` when that is set (CI: a service container; scripts/check.sh: a
  throwaway container), and fakeredis otherwise, so that `pytest` works anywhere. The same test
  code runs on both. On a real Redis the test owns the keys under `decimer:` and `arq:` (the job
  store and arq): it refuses to start when there are any, and deletes only those at the end, so
  whatever else lives in that database is never touched.
* The API is called in process (`httpx.ASGITransport`), like every other test of the API.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import arq
import arq.worker
import fakeredis
import httpx
import pytest
import uvicorn
from arq.connections import ArqRedis
from arq.worker import Worker
from fakeredis.aioredis import FakeAsyncRedisConnection
from fastapi import FastAPI
from redis.asyncio import ConnectionPool, Redis

from decimer_api import queue as queue_module
from decimer_api.app import create_app
from decimer_api.queue import ArqEnqueuer, health_key
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from decimer_api.worker import arq_app
from decimer_api.worker.settings import WorkerSettings
from tests.worker.fakes import FakeBackend

REDIS_URL_ENV = "E2E_REDIS_URL"
SERVICES = ("seg", "ocsr", "cls", "chem")
CLIENT = ("203.0.113.9", 50000)  # the one client of the tests: its address is its slot
# Nothing in a healthy run waits this long (a PDF takes 10-20 s all told). A wait is shorter than
# the test's own timeout (110 s), so a hang fails with what was being waited for, not with a bare
# timeout; a gate is held longer than a wait, so the wait is what reports it.
WAIT_SECONDS = 45.0
GATE_LIMIT_SECONDS = 60.0
# The keys of a run: the job store (`decimer:`) and arq (`arq:`, and its queues).
OWNED_KEY_PATTERNS = ("decimer:*", "arq:*")


# --- waiting ---------------------------------------------------------------------------------


async def eventually(
    condition: Callable[[], Any], what: str, *, timeout: float = WAIT_SECONDS, every: float = 0.02
) -> None:
    """Wait until `condition()` (a plain function, or a coroutine function) is truthy."""
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if asyncio.iscoroutine(value):
            value = await value
        if value:
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout:g} s waiting for {what}")
        await asyncio.sleep(every)


# --- Redis -----------------------------------------------------------------------------------


@dataclass
class RedisBackend:
    """Real Redis (`url` from `E2E_REDIS_URL`) or fakeredis; `install` points the production code
    at the fake one, which it cannot reach by URL."""

    url: str
    real: bool
    server: fakeredis.FakeServer | None = None

    @classmethod
    def from_environment(cls) -> RedisBackend:
        url = os.environ.get(REDIS_URL_ENV, "").strip()
        if url:
            return cls(url=url, real=True)
        return cls(url="redis://unused:6379/0", real=False, server=fakeredis.FakeServer())

    def decoded(self) -> Redis:
        """The store's client (strings, not bytes)."""
        if self.server is None:
            return Redis.from_url(self.url, decode_responses=True)
        return fakeredis.FakeAsyncRedis(server=self.server, decode_responses=True)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        if self.server is None:
            return
        server = self.server

        class FakeRedisFactory:  # what `arq_app.make_startup` calls: Redis.from_url(...)
            @staticmethod
            def from_url(_url: str, *, decode_responses: bool = False) -> fakeredis.FakeAsyncRedis:
                return fakeredis.FakeAsyncRedis(server=server, decode_responses=decode_responses)

        async def create_pool(_settings: object = None, **options: Any) -> ArqRedis:
            pool = ConnectionPool(connection_class=FakeAsyncRedisConnection, server=server)
            return ArqRedis(pool, **options)

        async def no_info(*_args: object, **_kwargs: object) -> None:
            """fakeredis has no INFO, which arq reads at start."""

        monkeypatch.setattr(arq_app, "Redis", FakeRedisFactory)
        monkeypatch.setattr(queue_module, "create_pool", create_pool)
        monkeypatch.setattr(arq.worker, "create_pool", create_pool)
        monkeypatch.setattr(arq.worker, "log_redis_info", no_info)

    async def _owned_keys(self, redis: Redis) -> list[str]:
        return [
            key for pattern in OWNED_KEY_PATTERNS async for key in redis.scan_iter(match=pattern)
        ]

    async def claim(self, redis: Redis) -> None:
        if self.real and await self._owned_keys(redis):
            pytest.fail(
                f"{REDIS_URL_ENV} points at a Redis that already holds keys under "
                f"{' or '.join(OWNED_KEY_PATTERNS)}: a live installation, or an earlier run. The "
                "end-to-end test deletes those keys when it is done, so it only runs where there "
                "are none."
            )

    async def release(self, redis: Redis) -> None:
        """Delete the keys of the run, and only those (not the whole database)."""
        if self.real and (keys := await self._owned_keys(redis)):
            await redis.delete(*keys)

    async def version(self, redis: Redis) -> str | None:
        """The server's version, for a real Redis; None for the fake."""
        if not self.real:
            return None
        return str((await redis.info("server"))["redis_version"])


# --- the model services ----------------------------------------------------------------------


class Gate:
    """Holds the calls to a service (`backend.pauses[service] = gate`) until `open()`. The fakes
    run in their own thread, so this is a `threading.Event`, polled. A subclass can let a call
    go earlier by its own rule (`wait_until`)."""

    def __init__(self) -> None:
        self._open = threading.Event()
        self.waiting = 0

    async def wait_until(self, condition: Callable[[], bool]) -> None:
        """Hold the call until the gate is opened or `condition()` is true."""
        self.waiting += 1
        try:
            deadline = time.monotonic() + GATE_LIMIT_SECONDS
            while not (self._open.is_set() or condition()):
                if time.monotonic() > deadline:
                    raise TimeoutError("a gate was never opened")
                await asyncio.sleep(0.01)
        finally:
            self.waiting -= 1

    async def __call__(self, _parts: dict[str, bytes]) -> None:
        await self.wait_until(lambda: False)

    def open(self) -> None:
        self._open.set()


def _service_app(backend: FakeBackend, service: str) -> Callable[..., Awaitable[None]]:
    """An ASGI app that hands a request to `FakeBackend.handle` as `http://<service>.test/...`,
    which is how the fake tells the services apart, and sends its answer back."""

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        body = bytearray()
        more = True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)
        headers = [
            (name.decode("latin-1"), value.decode("latin-1"))
            for name, value in scope["headers"]
            if name != b"host"
        ]
        request = httpx.Request(
            scope["method"],
            f"http://{service}.test{scope['path']}",
            headers=headers,
            content=bytes(body),
        )
        response = await backend.handle(request)
        answer = [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in response.headers.multi_items()
            if name.lower() != "content-length"
        ]
        answer.append((b"content-length", str(len(response.content)).encode()))
        await send(
            {"type": "http.response.start", "status": response.status_code, "headers": answer}
        )
        await send({"type": "http.response.body", "body": response.content})

    return app


class FakeServices:
    """seg, ocsr, cls and chem as four HTTP servers on 127.0.0.1, ports chosen by the system."""

    def __init__(self, backend: FakeBackend) -> None:
        self.backend = backend
        self._sockets: dict[str, socket.socket] = {}
        self._servers: dict[str, uvicorn.Server] = {}
        self._thread: threading.Thread | None = None

    def url(self, service: str) -> str:
        return f"http://127.0.0.1:{self._sockets[service].getsockname()[1]}"

    def start(self) -> None:
        for service in SERVICES:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            sock.listen(128)
            self._sockets[service] = sock
            config = uvicorn.Config(
                _service_app(self.backend, service),
                interface="asgi3",
                lifespan="off",
                log_level="warning",
                access_log=False,
                timeout_graceful_shutdown=2,
            )
            self._servers[service] = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run, name="fake-services", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15
        while not all(server.started for server in self._servers.values()):
            if not self._thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("the fake model services did not start")
            time.sleep(0.01)

    def _run(self) -> None:
        async def serve() -> None:
            await asyncio.gather(
                *(
                    self._servers[service].serve(sockets=[self._sockets[service]])
                    for service in SERVICES
                )
            )

        asyncio.run(serve())

    def stop(self) -> None:
        for gate in self.backend.pauses.values():
            if isinstance(gate, Gate):
                gate.open()  # a held call would hold the shutdown
        for server in self._servers.values():
            server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=15)
        for sock in self._sockets.values():
            sock.close()


# --- the workers -----------------------------------------------------------------------------


@dataclass
class RunningWorker:
    """One arq worker (one queue), started as `python -m decimer_api.worker` starts it, but in
    this event loop. `worker.jobs_complete` counts the jobs it has run."""

    worker: Worker
    task: asyncio.Task[None]
    queue: str

    async def stop(self) -> None:
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self.worker.close()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self.task


# --- the stack -------------------------------------------------------------------------------


@dataclass
class Stack:
    settings: Settings
    worker_settings: WorkerSettings
    redis_backend: RedisBackend
    redis: Redis
    store: JobStore
    backend: FakeBackend
    services: FakeServices
    app: FastAPI
    api: httpx.AsyncClient
    workers: list[RunningWorker] = field(default_factory=list)
    _lifespan: Any = None

    @classmethod
    async def start(
        cls,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        backend: FakeBackend,
        *,
        api: dict[str, Any] | None = None,
        worker: dict[str, Any] | None = None,
    ) -> Stack:
        redis_backend = RedisBackend.from_environment()
        redis_backend.install(monkeypatch)
        redis = redis_backend.decoded()
        services = FakeServices(backend)
        try:
            await redis_backend.claim(redis)
            services.start()
            return await cls._assemble(
                tmp_path, redis_backend, redis, services, backend, api, worker
            )
        except BaseException:
            services.stop()
            await redis.aclose()
            raise

    @classmethod
    async def _assemble(
        cls,
        tmp_path: Path,
        redis_backend: RedisBackend,
        redis: Redis,
        services: FakeServices,
        backend: FakeBackend,
        api: dict[str, Any] | None,
        worker: dict[str, Any] | None,
    ) -> Stack:
        settings = Settings(
            **{
                "redis_url": redis_backend.url,
                "data_dir": tmp_path / "jobs",
                "reports_dir": tmp_path / "reports",
                "chem_url": services.url("chem"),
                "max_upload_mb": 10,  # the golden paper is 1.2 MB
                "max_active_jobs_per_ip": 2,
                "ip_hash_secret": "e2e-secret",
                "trusted_proxy_cidr": [],
                "media_stream_direct": True,
                "fast_lane_check_seconds": 0.05,
                **(api or {}),
            }
        )
        worker_settings = WorkerSettings(
            **{
                "seg_url": services.url("seg"),
                "ocsr_url": services.url("ocsr"),
                "cls_url": services.url("cls"),
                "chem_url": services.url("chem"),
                "cancel_poll_seconds": 0.2,
                **(worker or {}),
            }
        )
        store = JobStore(redis, settings)
        enqueuer = ArqEnqueuer(
            settings.redis_url,
            queue_name=settings.worker_queue_name,
            fast_queue_name=settings.worker_fast_queue_name,
            fast_lane_check_seconds=settings.fast_lane_check_seconds,
        )
        app = create_app(settings, store, enqueuer, on_shutdown=enqueuer.aclose)
        lifespan = app.router.lifespan_context(app)
        await lifespan.__aenter__()  # the molfile pool starts, as under uvicorn
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=CLIENT), base_url="http://api.test"
        )
        return cls(
            settings=settings,
            worker_settings=worker_settings,
            redis_backend=redis_backend,
            redis=redis,
            store=store,
            backend=backend,
            services=services,
            app=app,
            api=client,
            _lifespan=lifespan,
        )

    async def start_worker(self, queue: str) -> RunningWorker:
        """A worker for `queue`, ready when it has built its deps and set its health key (the
        key the API looks for before it sends a small job to the fast lane)."""
        config = arq_app.build_arq_settings(
            self.settings.model_copy(update={"worker_queue_name": queue}), self.worker_settings
        )
        worker = Worker(**config, poll_delay=0.05, handle_signals=False)
        running = RunningWorker(worker, asyncio.create_task(worker.async_run()), queue)
        self.workers.append(running)

        async def ready() -> bool:
            if running.task.done():
                running.task.result()  # a worker that failed to start says why, at once
                raise AssertionError(f"the worker of {queue} stopped")
            return bool(await self.redis.exists(health_key(queue)))

        await eventually(ready, f"the worker of {queue}")
        return running

    async def stop(self) -> None:
        for running in reversed(self.workers):
            await running.stop()
        await self.api.aclose()
        with contextlib.suppress(Exception):
            await self._lifespan.__aexit__(None, None, None)
        self.services.stop()
        try:
            await self.redis_backend.release(self.redis)
        finally:
            await self.redis.aclose()

"""The molfile-reading children (`analysis_pool`, `analysis_worker`): real processes. Test hooks
are scripts given to the pool as its `argv`: they patch `analysis_worker.analyse_molfile` in the
child and then run its real `main()`, so everything else is the production code. A hook acts on
a marker in the molfile (`SLOWSLOW`, `HUNGRY`, ...), so the warm-up read at start-up is normal."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from rdkit import Chem

from decimer_api.analysis_pool import AnalysisPool
from decimer_api.app import create_app
from decimer_api.chemistry import analyse_molfile, analyse_smiles
from decimer_api.errors import ApiError
from decimer_api.models import Job
from decimer_api.molfile import MolfileError, TooComplex
from decimer_api.settings import Settings
from decimer_api.store import JobStore
from tests.conftest import FakeClock
from tests.molfiles import RdkitWasAsked
from tests.results import FakeNamer, make_done_job, molfile_of

PREAMBLE = "import os, sys, time, decimer_api.analysis_worker as w\nreal = w.analyse_molfile\n"
HOOKS = {
    # slow on a marker: far longer than any timeout
    "slow": "def hook(m):\n    if 'SLOWSLOW' in m: time.sleep(60)\n    return real(m)\n",
    "nap": "def hook(m):\n    if 'NAP' in m: time.sleep(0.3)\n    return real(m)\n",
    # asks for 4 GiB when marked: more than the address-space limit allows
    "hungry": "def hook(m):\n    if 'HUNGRY' in m: bytearray(4 * 1024**3)\n    return real(m)\n",
    "crash": "def hook(m):\n    if 'CRASH' in m: os._exit(3)\n    return real(m)\n",
    "bug": "def hook(m):\n    if 'BUG' in m: raise RuntimeError('a bug')\n    return real(m)\n",
    "chatty": (
        "def hook(m):\n    print('noise on stdout')\n    os.write(1, b'more noise\\n')\n"
        "    return real(m)\n"
    ),
    "env": (
        "import json\nfrom decimer_api.chemistry import Chemistry\n"
        "def hook(m):\n"
        "    return Chemistry('x', '', True, '', json.dumps(dict(os.environ)))\n"
    ),
    "limits": (
        "from decimer_api.chemistry import Chemistry\n"
        "def hook(m):\n"
        "    return Chemistry('x', '', True, '', open('/proc/self/limits').read())\n"
    ),
}


def hooked(name: str) -> list[str]:
    script = PREAMBLE + HOOKS[name] + "w.analyse_molfile = hook\nraise SystemExit(w.main())\n"
    return [sys.executable, "-I", "-c", script]


def garbage_answers() -> list[str]:
    script = (
        PREAMBLE
        + "enc = w.protocol.encode\n"
        + "bad = b'\\xff\\xff\\xff\\xff'\n"
        + "w.protocol.encode = lambda msg: enc(msg) if 'ready' in msg else bad\n"
        + "raise SystemExit(w.main())\n"
    )
    return [sys.executable, "-I", "-c", script]


MARKED = "\n  RDKit          2D\n\n  0  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n"


def marked(marker: str) -> str:
    """A small valid molfile with `marker` in its title line (the hooks look for it)."""
    return molfile_of("CCO").replace("RDKit", marker, 1)


@pytest.fixture
async def pools(settings: Settings) -> AsyncIterator[Callable[..., AnalysisPool]]:
    made: list[AnalysisPool] = []

    def make(argv: list[str] | None = None, **settings_over: object) -> AnalysisPool:
        configured = settings.model_copy(update=settings_over)
        pool = AnalysisPool(configured, argv=argv)
        made.append(pool)
        return pool

    yield make
    for pool in made:
        await pool.aclose()


def pids(pool: AnalysisPool) -> list[int | None]:
    return [child.process.pid if child.process else None for child in pool._children]  # noqa: SLF001


async def ready(pool: AnalysisPool) -> None:
    """Wait until every child has a running process (they start in the background)."""
    for _ in range(400):
        if all(pids(pool)):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the children did not start")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return (
        Path(f"/proc/{pid}/stat").exists()
        and "Z" not in Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    )


# --- reading ------------------------------------------------------------------------------------


async def test_a_molfile_is_read_in_another_process_with_the_same_answer(
    pools: Callable[..., AnalysisPool], monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = pools()
    expected = analyse_molfile(analyse_smiles("CC(=O)Nc1ccc(O)cc1").molfile)

    def rdkit_must_not_be_asked(*_args: object, **_kwargs: object) -> object:
        raise RdkitWasAsked("this process was asked to read a molfile")

    monkeypatch.setattr(Chem, "MolFromMolBlock", rdkit_must_not_be_asked)
    got = await pool.analyse(analyse_smiles("CC(=O)Nc1ccc(O)cc1").molfile)
    assert got == expected
    (pid,) = [p for p in pids(pool) if p]
    assert pid != os.getpid()  # the api process never ran RDKit's parser
    assert pool.capacity.in_use == 0


async def test_a_text_rdkit_cannot_read_is_a_molfile_error_and_the_child_goes_on(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools()
    with pytest.raises(MolfileError) as caught:
        await pool.analyse("hello\nworld\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\nM  END\n")
    assert not isinstance(caught.value, TooComplex)
    (first,) = [p for p in pids(pool) if p]
    for _ in range(3):
        assert (await pool.analyse(molfile_of("CCO"))).smiles == "CCO"
    assert alive(first) and first in pids(pool)  # an ordinary refusal does not cost a process
    assert pool.started == 1


async def test_stray_output_in_the_child_cannot_break_the_framing(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools(hooked("chatty"))
    for _ in range(3):
        assert (await pool.analyse(molfile_of("CCO"))).smiles == "CCO"


async def test_children_start_ahead_of_the_first_request_when_asked(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools()
    await pool.start()
    await ready(pool)
    assert pool.started == 2 and all(pids(pool))
    await pool.analyse(molfile_of("CCO"))
    assert pool.started == 2  # the request found a child ready


# --- the limits ---------------------------------------------------------------------------------


async def test_a_slow_molfile_is_stopped_at_the_timeout_and_the_pool_recovers(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools(hooked("slow"), analysis_timeout_seconds=0.5)
    await pool.analyse(molfile_of("CCO"))  # starts the child
    (slow_pid,) = [p for p in pids(pool) if p]
    started = time.monotonic()
    with pytest.raises(TooComplex, match="too complex"):
        await pool.analyse(marked("SLOWSLOW"))
    assert 0.4 < time.monotonic() - started < 3
    assert not alive(slow_pid)  # killed, not left running for a minute
    assert pool.capacity.in_use == 0
    assert (await pool.analyse(molfile_of("CCO"))).smiles == "CCO"  # a new child answers
    assert slow_pid not in pids(pool)
    assert pool.started == 2


async def test_the_event_loop_is_not_stalled_while_a_child_takes_its_time(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools(hooked("slow"), analysis_timeout_seconds=1.0)
    await pool.analyse(molfile_of("CCO"))
    worst = 0.0

    async def tick() -> None:
        nonlocal worst
        while True:
            before = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, time.perf_counter() - before - 0.01)

    ticker = asyncio.create_task(tick())
    with pytest.raises(TooComplex):
        await pool.analyse(marked("SLOWSLOW"))
    ticker.cancel()
    assert worst < 0.1  # a second of reading, and the loop never waited for it


async def test_a_memory_hungry_molfile_dies_on_the_address_space_limit_not_the_api(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools(hooked("hungry"))
    await pool.analyse(molfile_of("CCO"))
    (pid,) = [p for p in pids(pool) if p]
    with pytest.raises(TooComplex):
        await pool.analyse(marked("HUNGRY"))  # asks for 4 GiB
    assert not alive(pid)  # a child that has been through that is not reused
    assert (await pool.analyse(molfile_of("CCO"))).smiles == "CCO"
    # and the api is where it was: it did not allocate anything of the kind itself
    status = Path("/proc/self/status").read_text()
    resident = int(re.search(r"VmRSS:\s+(\d+) kB", status).group(1))  # type: ignore[union-attr]
    assert resident < 2 * 1024 * 1024  # kB: well under 2 GB


async def test_the_child_has_an_address_space_limit_and_no_files_to_write(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools(hooked("limits"), analysis_memory_mb=300)
    limits = (await pool.analyse(molfile_of("CCO"))).svg
    space = re.search(r"Max address space\s+(\d+)\s+(\d+)", limits)
    assert space is not None and space.group(1) == space.group(2)
    # baseline of an RDKit process (about 520 MB) + 300 MB: not unlimited, not a gigabyte over
    assert 600 * 1024**2 < int(space.group(1)) < 1100 * 1024**2
    fsize = re.search(r"Max file size\s+(\d+)\s+(\d+)", limits)
    assert fsize is not None and fsize.group(1) == fsize.group(2) == "0"
    core = re.search(r"Max core file size\s+(\d+)", limits)
    assert core is not None and core.group(1) == "0"
    files = re.search(r"Max open files\s+(\d+)", limits)
    assert files is not None and int(files.group(1)) <= 16


async def test_a_child_that_crashes_is_replaced_and_the_request_is_too_complex(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools(hooked("crash"))
    await pool.analyse(molfile_of("CCO"))
    (pid,) = [p for p in pids(pool) if p]
    with pytest.raises(TooComplex):
        await pool.analyse(marked("CRASH"))
    assert not alive(pid)
    assert (await pool.analyse(molfile_of("CCN"))).smiles == "CCN"
    assert pool.capacity.in_use == 0


@pytest.mark.parametrize("spins", range(0, 16))
async def test_a_request_cancelled_at_any_point_never_leaves_its_answer_for_the_next_one(
    pools: Callable[..., AnalysisPool], spins: int
) -> None:
    """The caller goes away (the client hangs up, the server stops) while the molfile is on its
    way to the child, being read, or coming back. The answer is still on its way: the next
    request on that child must not take it for its own (it was: 6 of 12 cancel points)."""
    pool = pools(max_concurrent_analyses=1)  # one child: the next request meets the leftovers
    await pool.start()
    ethanol = molfile_of("CCO")
    await pool.analyse(ethanol)  # the child is up
    other = analyse_smiles("c1ccc2cc3ccccc3cc2c1").molfile
    task = asyncio.ensure_future(pool.analyse(other))
    for _ in range(spins):
        await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    for _ in range(3):  # whatever happened, every request now gets its own answer
        answer = await pool.analyse(ethanol)
        assert answer.smiles == "CCO", f"cancelled at {spins}: got the answer to another molfile"
    assert pool.capacity.in_use == 0


async def test_a_cancelled_request_has_its_child_replaced_not_reused(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools(hooked("slow"), max_concurrent_analyses=1)
    await pool.analyse(molfile_of("CCO"))
    (pid,) = [p for p in pids(pool) if p]
    task = asyncio.ensure_future(pool.analyse(marked("SLOWSLOW")))
    await asyncio.sleep(0.3)  # the molfile is in the child
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):
        if not alive(pid):
            break
        await asyncio.sleep(0.02)
    assert not alive(pid)  # not left to finish a minute of work nobody wants
    assert (await pool.analyse(molfile_of("CCN"))).smiles == "CCN"


async def test_a_cancelled_request_cancelled_again_still_has_its_child_replaced_and_started_anew(
    pools: Callable[..., AnalysisPool],
) -> None:
    """The killing is shielded: a second cancel while it is under way does not stop it half way."""
    pool = pools(hooked("slow"), max_concurrent_analyses=1)
    await pool.analyse(molfile_of("CCO"))
    (pid,) = [p for p in pids(pool) if p]
    task = asyncio.ensure_future(pool.analyse(marked("SLOWSLOW")))
    await asyncio.sleep(0.3)
    task.cancel()
    await asyncio.sleep(0)  # the replacement is under way
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(300):  # a new child is started ahead of the next request, nobody asked for it
        fresh = [p for p in pids(pool) if p and p != pid]
        if fresh:
            break
        await asyncio.sleep(0.05)
    assert fresh and not alive(pid)


async def test_a_request_right_after_a_cancelled_one_does_not_meet_the_dying_child(
    pools: Callable[..., AnalysisPool],
) -> None:
    """The process is taken away from its slot at the moment of the cancel, before it is killed:
    the next request (the lock is free the moment the cancelled one ends) must not pick up the
    process that is being killed and take its death for its own failure."""
    pool = pools(hooked("slow"), max_concurrent_analyses=1)
    await pool.analyse(molfile_of("CCO"))
    task = asyncio.ensure_future(pool.analyse(marked("SLOWSLOW")))
    await asyncio.sleep(0.3)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()  # the second one ends the cancelled request at once, lock released
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await pool.analyse(molfile_of("CCN"))).smiles == "CCN"


async def test_a_child_that_died_between_requests_is_replaced_before_it_is_used(
    pools: Callable[..., AnalysisPool],
) -> None:
    """The kernel's out-of-memory killer, or anyone: the next request is not failed for it."""
    import signal

    pool = pools()
    await pool.analyse(molfile_of("CCO"))
    (pid,) = [p for p in pids(pool) if p]
    os.kill(pid, signal.SIGKILL)
    for _ in range(100):  # until the event loop has seen it end
        if not alive(pid):
            await asyncio.sleep(0.05)
            break
        await asyncio.sleep(0.02)
    assert (await pool.analyse(molfile_of("CCN"))).smiles == "CCN"
    assert pool.started == 2


async def test_a_child_that_answers_nonsense_is_replaced(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools(garbage_answers())
    seen: list[int] = []
    for _ in range(2):
        with pytest.raises(TooComplex):
            await pool.analyse(molfile_of("CCO"))
        seen.append(pool.started)
    assert seen[1] > seen[0]  # a new child for the second: the first one was not trusted again


async def test_a_bug_in_the_child_is_logged_and_the_child_replaced(
    pools: Callable[..., AnalysisPool], caplog: pytest.LogCaptureFixture
) -> None:
    pool = pools(hooked("bug"))
    await pool.analyse(molfile_of("CCO"))
    (pid,) = [p for p in pids(pool) if p]
    with caplog.at_level("ERROR", logger="decimer_api.analysis"):
        with pytest.raises(TooComplex):
            await pool.analyse(marked("BUG"))
    assert "RuntimeError" in caplog.text
    assert not alive(pid)


async def test_a_child_is_replaced_after_a_number_of_molfiles(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools(analysis_recycle_after=2)
    await pool.analyse(molfile_of("CCO"))
    first = [p for p in pids(pool) if p]
    await pool.analyse(molfile_of("CCO"))  # the second: it is retired after this one
    for _ in range(100):
        if first[0] not in pids(pool):
            break
        await asyncio.sleep(0.02)
    assert first[0] not in pids(pool)
    assert not alive(first[0])
    assert (await pool.analyse(molfile_of("CCN"))).smiles == "CCN"


async def test_an_export_gives_all_its_molfiles_a_total_time_not_each_a_new_one(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools(hooked("nap"), analysis_timeout_seconds=5.0, analysis_batch_seconds=0.5)
    async with pool.session() as session:
        await session.analyse(marked("NAP"))  # 0.3 s
        with pytest.raises(TooComplex):
            for _ in range(3):
                await session.analyse(marked("NAP"))  # the second of these is past 0.5 s
    assert pool.capacity.in_use == 0


# --- the children ---------------------------------------------------------------------------------


async def test_a_child_gets_a_minimal_environment_not_the_apis(
    pools: Callable[..., AnalysisPool], monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    monkeypatch.setenv("IP_HASH_SECRET", "the-api-secret")
    monkeypatch.setenv("REDIS_URL", "redis://:password@redis/0")
    pool = pools(hooked("env"))
    seen = json.loads((await pool.analyse(molfile_of("CCO"))).svg)
    # What we pass (PATH, LC_ALL) and what the interpreter and RDKit add for themselves.
    assert set(seen) <= {"PATH", "LC_ALL", "LC_CTYPE", "PYTHONIOENCODING", "PYTHONUTF8", "RDBASE"}
    assert seen["PATH"] == "/usr/bin:/bin" and seen["LC_ALL"] == "C.UTF-8"
    assert "the-api-secret" not in json.dumps(seen)


async def test_close_kills_the_children_and_reaps_them(
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools()
    await pool.start()
    await ready(pool)
    running = [p for p in pids(pool) if p]
    assert len(running) == 2
    await pool.aclose()
    assert not any(alive(pid) for pid in running)
    assert pids(pool) == [None, None]


# --- no child: 503 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        [sys.executable, "-I", "-c", "raise SystemExit(1)"],  # starts and dies
        [sys.executable, "-I", "-c", "import time; time.sleep(60)"],  # never says it is ready
        ["/nonexistent/python"],  # cannot be started at all
        [sys.executable, "-I", "-c", "import sys; sys.stdout.buffer.write(b'\\0\\0\\0\\x02{}')"],
    ],
    ids=["dies", "never-ready", "no-executable", "wrong-hello"],
)
async def test_a_pool_whose_children_cannot_start_is_503_not_a_hang(
    pools: Callable[..., AnalysisPool], argv: list[str]
) -> None:
    pool = pools(argv, analysis_startup_seconds=1.0)
    started = time.monotonic()
    with pytest.raises(ApiError) as caught:
        await pool.analyse(molfile_of("CCO"))
    assert (caught.value.status, caught.value.code.value) == (503, "unavailable")
    assert time.monotonic() - started < 5
    assert pool.capacity.in_use == 0


# --- through the routes ---------------------------------------------------------------------------


@pytest.fixture
async def app_with_children(
    settings: Settings,
    store: JobStore,
    enqueue: object,
    namer: FakeNamer,
    pools: Callable[..., AnalysisPool],
) -> FastAPI:
    pool = pools(hooked("slow"), analysis_timeout_seconds=1.0)
    return create_app(settings, store, enqueue, namer=namer, analysis=pool)  # type: ignore[arg-type]


async def test_validate_reads_in_a_child_and_a_slow_molfile_is_422_too_complex(
    app_with_children: FastAPI,
    store: JobStore,
    settings: Settings,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await make_done_job(store, settings, clock)
    transport = httpx.ASGITransport(app=app_with_children, client=("203.0.113.5", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        ok = await client.post(
            f"/api/jobs/{job.id}/results/0/validate", json={"molfile": molfile_of("CCCl")}
        )
        assert ok.status_code == 200 and ok.json()["smiles"] == "CCCl"
        started = time.monotonic()
        slow = await client.post(
            f"/api/jobs/{job.id}/results/0/validate", json={"molfile": marked("SLOWSLOW")}
        )
        assert time.monotonic() - started < 4
        assert slow.status_code == 422
        assert slow.json()["detail"]["code"] == "invalid_request"
        assert "too complex" in slow.json()["detail"]["message"]
        # the pool is back: the next one is read at once
        again = await client.post(
            f"/api/jobs/{job.id}/results/0/validate", json={"molfile": molfile_of("CCN")}
        )
        assert again.status_code == 200 and again.json()["smiles"] == "CCN"


async def test_export_reads_its_edits_in_a_child_too(
    app_with_children: FastAPI,
    store: JobStore,
    settings: Settings,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = await make_done_job(store, settings, clock)

    def rdkit_must_not_be_asked(*_args: object, **_kwargs: object) -> object:
        raise RdkitWasAsked("the api process was asked to read a molfile")

    monkeypatch.setattr(Chem, "MolFromMolBlock", rdkit_must_not_be_asked)
    transport = httpx.ASGITransport(app=app_with_children, client=("203.0.113.5", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        good = await client.post(
            f"/api/jobs/{job.id}/export",
            json={"format": "csv", "molfiles": {"1": molfile_of("CCCl")}},
        )
        assert good.status_code == 200
        assert good.text.splitlines()[2].split(",")[1] == "CCCl"  # read by the child
        slow = await client.post(
            f"/api/jobs/{job.id}/export",
            json={"format": "zip", "molfiles": {"2": marked("SLOWSLOW")}},
        )
        assert slow.status_code == 422
        assert "structure 3" in slow.json()["detail"]["message"]
        assert "too complex" in slow.json()["detail"]["message"]


async def test_a_third_request_while_both_children_are_busy_is_503_busy_at_once(
    app_with_children: FastAPI,
    store: JobStore,
    settings: Settings,
    clock: FakeClock,
) -> None:
    job = await make_done_job(store, settings, clock)
    transport = httpx.ASGITransport(app=app_with_children, client=("203.0.113.5", 1))

    async def validate(client: httpx.AsyncClient, molfile: str) -> httpx.Response:
        return await client.post(
            f"/api/jobs/{job.id}/results/0/validate", json={"molfile": molfile}
        )

    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        busy = [asyncio.create_task(validate(client, marked("SLOWSLOW"))) for _ in range(2)]
        await asyncio.sleep(0.2)
        refused_at = time.monotonic()
        refused = await validate(client, molfile_of("CCO"))
        assert refused.status_code == 503 and refused.json()["detail"]["code"] == "busy"
        assert time.monotonic() - refused_at < 0.5
        assert [r.status_code for r in await asyncio.gather(*busy)] == [422, 422]
        assert (await validate(client, molfile_of("CCO"))).status_code == 200


async def test_when_no_child_can_start_validate_is_503_unavailable(
    settings: Settings,
    store: JobStore,
    enqueue: object,
    namer: FakeNamer,
    clock: FakeClock,
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools(["/nonexistent/python"])
    app = create_app(settings, store, enqueue, namer=namer, analysis=pool)  # type: ignore[arg-type]
    job: Job = await make_done_job(store, settings, clock)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.5", 1))
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
        response = await client.post(
            f"/api/jobs/{job.id}/results/0/validate", json={"molfile": molfile_of("CCO")}
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "unavailable"


async def test_the_api_starts_and_stops_its_children_with_its_lifespan(
    settings: Settings,
    store: JobStore,
    enqueue: object,
    namer: FakeNamer,
    pools: Callable[..., AnalysisPool],
) -> None:
    pool = pools()
    app = create_app(settings, store, enqueue, namer=namer, analysis=pool)  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        await ready(pool)
        running = [p for p in pids(pool) if p]
        assert len(running) == 2
    assert not any(alive(pid) for pid in running)

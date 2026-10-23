"""The usage counter: per day, how many jobs, of what kind and size, how many pages and
structures, how many failures. It replaces the legacy site's TSV files
(`decimer_segmentation_log.tsv`: time, pages, structures) and, like them, answers "how much is the
service used" for a report or a grant.

What is counted, and what is never stored:

* **No client address, no job id, no file name, no time of day.** Only counts, per UTC day: one hash
  per day in Redis, `decimer:usage:<YYYY-MM-DD>`, with these fields: `jobs.<kind>.<size>`   jobs
  accepted: kind `pdf`, `image` (one or more files) or `paste` (one image named like the app names a
  pasted one), size `small` or `large` (the fast-lane rule, `Settings.is_small_job`), counted by the
  api when it queues the job `done`, `failed`       jobs that finished, counted by the worker
  `pages`, `structures`  pages processed and structures returned by the jobs that finished `done`
  `refused.<code>`       uploads that got an error instead of a job, by the API's error code
  (`queue_full` and `rate_limited` are the capacity signals) A job that crosses midnight is counted
  as created on one day and finished on the next.
* **Never in the way.** Every call is best effort with a short timeout and swallows its failures (a
  warning in the log): a Redis hiccup must not fail an upload or a finished job.
* **Long-lived.** The day's hash gets `USAGE_TTL_DAYS` (800) after its last count. The jobs' own
  Redis keeps nothing for more than six hours and restarts without persistence, so compose points
  `USAGE_REDIS_URL` at a small Redis with an append-only file on a volume; without that variable the
  counts live in the main Redis and are lost with it.

Read it: `docker compose exec api python -m decimer_api.usage [--days 30] [--json]`. Nothing is
exposed over HTTP, so there is no route to protect.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from redis.asyncio import Redis

from .settings import Settings

log = logging.getLogger("decimer_api.usage")

PREFIX = "decimer:usage:"
KINDS = ("pdf", "image", "paste")
SIZES = ("small", "large")
# The app names a pasted image `pasted-image.png` (`strings.upload.pastedName` in
# web/src/strings.ts; several pasted images get `-1`, `-2`). The API sees only file names, so this
# prefix is the whole signal; a test reads the web copy and fails if the two drift apart.
PASTE_NAME_PREFIX = "pasted-image"
CALL_TIMEOUT_SECONDS = 1.0

Clock = Callable[[], datetime]


def kind_of(*, is_pdf: bool, names: list[str]) -> str:
    """`pdf`, `paste` (a single image the app named as a pasted one) or `image`."""
    if is_pdf:
        return "pdf"
    if len(names) == 1 and names[0].lower().startswith(PASTE_NAME_PREFIX):
        return "paste"
    return "image"


def day_key(day: date) -> str:
    return f"{PREFIX}{day.isoformat()}"


class Usage(Protocol):
    async def job_created(self, kind: str, *, small: bool) -> None: ...

    async def job_finished(self, *, failed: bool, pages: int, structures: int) -> None: ...

    async def refused(self, code: str) -> None: ...


class NullUsage:
    """Counts nothing (tests, and the in-process development mode)."""

    async def job_created(self, kind: str, *, small: bool) -> None:
        return None

    async def job_finished(self, *, failed: bool, pages: int, structures: int) -> None:
        return None

    async def refused(self, code: str) -> None:
        return None


class RedisUsage:
    def __init__(
        self,
        redis: Redis,
        *,
        ttl_days: int = 800,
        clock: Clock = lambda: datetime.now(UTC),
        timeout_seconds: float = CALL_TIMEOUT_SECONDS,
    ) -> None:
        self._redis = redis
        self._ttl = ttl_days * 24 * 3600
        self._clock = clock
        self._timeout = timeout_seconds

    async def job_created(self, kind: str, *, small: bool) -> None:
        size = "small" if small else "large"
        await self._add({f"jobs.{kind}.{size}": 1})

    async def job_finished(self, *, failed: bool, pages: int, structures: int) -> None:
        if failed:
            await self._add({"failed": 1})
        else:
            await self._add({"done": 1, "pages": max(pages, 0), "structures": max(structures, 0)})

    async def refused(self, code: str) -> None:
        await self._add({f"refused.{code}": 1})

    async def _add(self, counts: Mapping[str, int]) -> None:
        key = day_key(self._clock().date())
        try:
            async with asyncio.timeout(self._timeout):
                async with self._redis.pipeline(transaction=False) as pipe:
                    for name, amount in counts.items():
                        pipe.hincrby(key, name, amount)
                    pipe.expire(key, self._ttl)
                    await pipe.execute()
        except Exception as error:
            # A lost count is not worth a failed upload (CancelledError is no Exception: it passes).
            log.warning("usage counter: %s", type(error).__name__)

    async def aclose(self) -> None:
        await self._redis.aclose()


def build_usage(settings: Settings) -> RedisUsage:
    redis: Redis = Redis.from_url(
        settings.usage_redis_url or settings.redis_url, decode_responses=True
    )
    return RedisUsage(redis, ttl_days=settings.usage_ttl_days)


# --- reading it ----------------------------------------------------------------------------------

Day = dict[str, int | str]


async def read_days(redis: Redis, days: int, *, today: date | None = None) -> list[Day]:
    """The last `days` days, oldest first, as rows of `date` and every field counted that day."""
    last = today or datetime.now(UTC).date()
    rows: list[Day] = []
    for back in range(days - 1, -1, -1):
        day = last - timedelta(days=back)
        counts: dict[str, str] = await redis.hgetall(day_key(day))  # type: ignore[misc]
        row: Day = {"date": day.isoformat()}
        row.update({name: int(value) for name, value in sorted(counts.items())})
        rows.append(row)
    return rows


def _count(row: Mapping[str, int | str], name: str) -> int:
    value = row.get(name, 0)
    return value if isinstance(value, int) else 0


def _jobs(row: Mapping[str, int | str], kind: str) -> int:
    return sum(_count(row, f"jobs.{kind}.{size}") for size in SIZES)


def _refused(row: Mapping[str, int | str]) -> int:
    return sum(_count(row, name) for name in row if name.startswith("refused."))


COLUMNS: tuple[tuple[str, Callable[[Mapping[str, int | str]], int]], ...] = (
    ("jobs", lambda r: sum(_jobs(r, kind) for kind in KINDS)),
    ("pdf", lambda r: _jobs(r, "pdf")),
    ("image", lambda r: _jobs(r, "image")),
    ("paste", lambda r: _jobs(r, "paste")),
    ("small", lambda r: sum(_count(r, f"jobs.{kind}.small") for kind in KINDS)),
    ("large", lambda r: sum(_count(r, f"jobs.{kind}.large") for kind in KINDS)),
    ("done", lambda r: _count(r, "done")),
    ("failed", lambda r: _count(r, "failed")),
    ("refused", _refused),
    ("pages", lambda r: _count(r, "pages")),
    ("structures", lambda r: _count(r, "structures")),
)


def table(rows: list[Day]) -> str:
    """A fixed-width table, one row per day and a total; the refusals by code below it."""
    header = ["date", *(name for name, _ in COLUMNS)]
    body = [[str(row["date"]), *(str(get(row)) for _, get in COLUMNS)] for row in rows]
    totals = [sum(get(row) for row in rows) for _, get in COLUMNS]
    body.append(["total", *map(str, totals)])
    widths = [max(len(line[i]) for line in [header, *body]) for i in range(len(header))]

    def line(cells: list[str]) -> str:
        return "  ".join(
            cell.rjust(width) if i else cell.ljust(width)
            for i, (cell, width) in enumerate(zip(cells, widths, strict=True))
        )

    out = [
        line(header),
        *(line(cells) for cells in body[:-1]),
        line(["-" * w for w in widths]),
        line(body[-1]),
    ]
    codes: dict[str, int] = {}
    for row in rows:
        for name, value in row.items():
            if name.startswith("refused.") and isinstance(value, int):
                code = name.removeprefix("refused.")
                codes[code] = codes.get(code, 0) + value
    if codes:
        out.append("")
        out.append(
            "refused by code: "
            + ", ".join(f"{code} {count}" for code, count in sorted(codes.items()))
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m decimer_api.usage",
        description="Print the per-day usage counts (jobs by kind and size, pages, structures).",
    )
    parser.add_argument(
        "--days", type=int, default=30, help="how many days back, today included (default 30)"
    )
    parser.add_argument(
        "--json", action="store_true", help="print the days as JSON instead of a table"
    )
    arguments = parser.parse_args(argv)
    if arguments.days < 1:
        parser.error("--days must be at least 1")
    settings = Settings()

    async def go() -> list[Day]:
        redis: Redis = Redis.from_url(
            settings.usage_redis_url or settings.redis_url, decode_responses=True
        )
        try:
            return await read_days(redis, arguments.days)
        finally:
            await redis.aclose()

    try:
        rows = asyncio.run(go())
    except Exception as error:
        print(f"could not read the usage counts: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(rows, indent=2) if arguments.json else table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())

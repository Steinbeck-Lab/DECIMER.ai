# decimer-api

FastAPI job API for decimer.ai (Python 3.12). This project holds the HTTP API, the job store, the
upload hardening and the TTL sweep. The arq worker joins it later; the places where it
plugs in are marked below.

```
POST /api/jobs ──► take the client's slot ► read the body ► classify (magic bytes)
                   ► ingest in a child process (limits, deadline) ► files in DATA_DIR/<id>/input/
                   ► Redis record ► arq queue
GET  /api/jobs/<id> ──► status, progress, results
GET  /api/jobs/<id>/media/<name> ──► X-Accel-Redirect to nginx (job-scoped)
```

## Run

```bash
cd api
uv sync
DATA_DIR=/tmp/decimer-jobs REDIS_URL=redis://localhost:6379/0 \
  MEDIA_STREAM_DIRECT=true uv run python -m decimer_api        # http://127.0.0.1:8000

uv run python -m decimer_api.sweep                 # delete expired job folders, once (for cron)
uv run python -m decimer_api.sweep --every 300     # or as a long-running sidecar
```

In a container set `HOST=0.0.0.0`. Without `MEDIA_STREAM_DIRECT=true` the media endpoint returns an
empty body with an `X-Accel-Redirect` header, which only nginx can turn into a file (see below).
`python -m decimer_api` runs uvicorn with `proxy_headers=False` and no access log, on purpose (see
*Fairness*). The ingest child processes need a Linux host (they use `resource` limits).

## Settings

All come from environment variables of the same name (case-insensitive); `.env.example` lists them.

| Variable | Default | Meaning |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Job records, per-client slots, the waiting queue, and arq. |
| `DATA_DIR` | `/data/jobs` | Job files live in `DATA_DIR/<job id>/`. |
| `JOB_TTL_SECONDS` | `3600` | A finished (done or failed) job is deleted this long after it ends. |
| `QUEUED_JOB_TTL_SECONDS` | `21600` | Record life of a **queued** job. Must outlast the longest wait in the queue. |
| `RUNNING_JOB_TTL_SECONDS` | `1200` | Record life of a **running** job after its last `save()`. A dead worker frees its slot after this. |
| `RESERVATION_TTL_SECONDS` | `600` | A client's slot is held from the start of its upload; a request that dies without giving it back loses it after this. |
| `SWEEP_GRACE_SECONDS` | `900` | The sweep leaves folders younger than this alone when they have no record yet. |
| `MAX_PDF_PAGES` | `10` | Pages kept from a PDF; more sets `truncated` and a warning. |
| `MAX_STRUCTURES` | `20` | Not used by the API; the worker applies it. |
| `MAX_UPLOAD_MB` | `100` | Total size of the files in one upload (MiB). 413 above it. |
| `MAX_UPLOAD_FILES` | `50` | Files in one upload. |
| `MAX_IMAGE_PIXELS` | `50000000` | Largest image, and largest PDF page at 300 DPI (a B2 page is 49 MP, A2 is 34.8 MP). The seg service refuses above 150 MP. |
| `MAX_JOB_PIXELS` | `500000000` | All images, or all kept PDF pages, of one job together: the page limit (10) at the largest page size. Also bounds the disk one job can use, because images are stored as PNG. |
| `INGEST_CONCURRENCY` | `2` | Uploads being parsed at the same time in one API process. At least 2 (enforced): one client may use only one slot at a time, so with a single slot it would block everyone else. |
| `INGEST_WAIT_SECONDS` | `20` | How long a request waits for an ingest turn (its client's turn, then a free slot) before it gets 503 `busy`. |
| `INGEST_TIMEOUT_SECONDS` | `30` | Deadline of one ingest child; it is killed after this. A child that used a lot of CPU gives 413 `processing_limit` (the file is too hard); one that used little (starved, or stuck) gives 503 `busy`. |
| `PROXY_READ_TIMEOUT_SECONDS` | `60` | Your reverse proxy's read timeout (nginx default). The settings refuse to start unless `INGEST_WAIT_SECONDS + INGEST_TIMEOUT_SECONDS + 5` is below it, or nginx would answer 504 while the API still works. |
| `INGEST_MEMORY_MB` | `1536` | Address space a child may allocate beyond what it uses after start-up. |
| `MAX_ACTIVE_JOBS_PER_IP` | `2` | Queued plus running jobs (plus uploads in progress) per client. A further job gets 429. |
| `QUEUE_MAX` | `50` | Jobs allowed to wait. At or above it, 503 with `Retry-After`. |
| `QUEUE_RETRY_AFTER_SECONDS` | `30` | `Retry-After` of the 503 for a full queue or a busy service. |
| `TRUSTED_PROXY_CIDR` | empty | Proxies whose `X-Forwarded-For` is believed. Comma or space separated; IPv6 allowed. Empty trusts nobody. `/0` is refused. |
| `IP_HASH_SECRET` | empty | Key for hashing client addresses. Set it in production. When empty a random key is made at start, and per-IP counters reset on restart. |
| `MEDIA_STREAM_DIRECT` | `false` | `true`: stream media files from the API (development). `false`: `X-Accel-Redirect`. |
| `MEDIA_ACCEL_PREFIX` | `/_protected/jobs` | The internal nginx location, see below. |
| `HOST`, `PORT` | `127.0.0.1`, `8000` | `python -m decimer_api` only. |

## Endpoints

| Method and path | Success | Errors |
|---|---|---|
| `POST /api/jobs` multipart, one or more `file` parts | `202` job JSON, `Location: /api/jobs/<id>` | `400` mixed PDF and images, several PDFs, too many files, corrupt or encrypted file, no file; `413` too large, too many pixels, or a file the parser could not handle within its limits (`processing_limit`); `415` not a supported type, or not multipart; `429` client has `MAX_ACTIVE_JOBS_PER_IP` active jobs; `503` queue full or service busy (`Retry-After`), Redis or enqueue down |
| `GET /api/jobs/{id}` | `200` job JSON (`queuePosition` while queued), `Cache-Control: no-store` | `404` unknown, expired or malformed id |
| `GET /api/jobs/{id}/media/{name}` | `200`, empty body, `X-Accel-Redirect: /_protected/jobs/<id>/<name>` (or the file itself with `MEDIA_STREAM_DIRECT`) | `404` unknown job, name not `crop-<n>.png` or `page-<n>.png`, file missing, symlink, or traversal |
| `GET /healthz` | `200` | |
| `GET /readyz` | `200` when Redis answers and `DATA_DIR` is writable | `503` |
| `GET /api/openapi.json` | the OpenAPI schema (the source for the generated TypeScript types, P3-10) | |

Every error body has one shape, whatever produced it (our checks, an unknown path, a wrong method, a
validation failure, a bug): `{"detail": {"code": "...", "message": "..."}}`. Codes: `rate_limited`,
`queue_full`, `busy`, `too_large`, `too_many_pixels`, `processing_limit`, `unsupported`,
`mixed_upload`, `multiple_pdfs`, `too_many_files`, `corrupt_file`, `encrypted_pdf`, `empty_upload`,
`invalid_request`, `unavailable`, `not_found`, `method_not_allowed`, `internal_error`. A rejected
upload never has a job id or a queue position.

Reserved for P3-06: `POST /api/jobs/{id}/results/{n}/validate`. Nothing in this project routes there.

JSON is camelCase. A job:

```json
{"id": "<32 hex>", "status": "queued", "queuePosition": 1,
 "stages": [{"name": "upload", "state": "done"}, {"name": "render", "state": "pending"}],
 "pages": 12, "pagesProcessed": 10, "truncated": true,
 "structuresFound": 0, "structuresDropped": 0, "results": [],
 "warnings": [{"code": "pages_truncated", "message": "..."}], "error": null,
 "createdAt": "...Z", "expiresAt": "...Z"}
```

`Result` (filled by the worker): `index`, `number`, `pageIndex`, `bbox` `[x0,y0,x1,y1]`, `smiles`,
`inchikey`, `valid`, `isStructure`, `confidence`, `name`, `nameTier`, `svg`, `molfile`, `cropUrl`.
The stored record (`Job`, `Result`) holds none of the derived fields (`queuePosition`, `number`,
`cropUrl`); the API composes them when it returns the job (`JobView`, `ResultView`).

## What the upload does

* **The slot comes first.** Before a byte of the body is read, the client's slot is taken
  (`MAX_ACTIVE_JOBS_PER_IP` counts running and queued jobs *and* uploads in progress). A client over its
  limit gets 429 for the price of one Redis call, however many uploads it starts at once.
* The type comes from the **first bytes** (PDF, PNG, JPEG, WebP, HEIC/HEIF), never from the file name or
  the client's content type. A `.png` that is really a PDF is a PDF. Anything else is 415. These cheap
  checks (count, type, mix) run in the API process, before any parser is started.
* **Parsing happens in a child process**, never in the API: `python -I -m decimer_api.ingest_worker`
  per upload, with a minimal environment (none of the API's secrets), no core dumps, 64 open files, a
  CPU limit, a file-size limit, an address-space limit (`INGEST_MEMORY_MB` beyond its start-up size) and
  a wall-clock deadline (`INGEST_TIMEOUT_SECONDS`; the child is killed, with its whole process group,
  grandchildren included). A file that crashes the parser gives 400 `corrupt_file`; one that hits a limit
  gives 413 `processing_limit`; neither can hold a slot or take down the API. The child's stderr is
  captured and logged, never forwarded. The API process does not even import Pillow or pdfium (a test
  checks it).
* **What the child can and cannot reach.** It is given a minimal environment, and the API process marks
  itself non-dumpable at start (`prctl(PR_SET_DUMPABLE, 0)`, Linux), so the child cannot read the API's
  `/proc/<pid>/environ` (`IP_HASH_SECRET`, `REDIS_URL`) or memory. That is all it closes: the child still
  runs as the same user, so it can read any file that user can read (a `.env`) and open network
  connections (Redis). The cost of non-dumpable: py-spy and gdb cannot attach to the API unless run as
  root. **For P3-11:** run the ingest in a sidecar container with no network route to Redis, only
  `DATA_DIR` mounted, and ideally another uid; the API then talks to it instead of spawning a child.
* At most `INGEST_CONCURRENCY` children run at once, and **at most one per client**: a client's second
  upload waits for its own first (not for a free slot), so one address cannot hold every slot while
  `MAX_ACTIVE_JOBS_PER_IP` equals `INGEST_CONCURRENCY`. A request that cannot get its turn within
  `INGEST_WAIT_SECONDS` gets 503 `busy` with `Retry-After`, and leaves nothing behind. The gate is per
  API process; with several API processes a client could use one slot per process, and a Redis counter
  per client hash (INCR with an expiry, released in a `finally`) would make it global. One process is
  the plan, so that is not built.
* PDF (pypdfium2): the page count is read **before** any rendering. More than `MAX_PDF_PAGES` keeps the
  first pages (the stored `input/document.pdf` has only those, and the others are never inspected) and
  sets `truncated` and a `pages_truncated` warning. Encrypted is 400 `encrypted_pdf`; anything pdfium or
  pypdfium2 raises after opening is 400 `corrupt_file`. Every kept page must render below
  `MAX_IMAGE_PIXELS` at 300 DPI, and together below `MAX_JOB_PIXELS`.
* Images become `input/0001.png`, `0002.png`, ... in upload order: 8-bit RGB, EXIF rotation applied,
  **every metadata chunk dropped** (EXIF, ICC profile, text), **transparency flattened onto white** (the
  seg service reads with OpenCV `IMREAD_COLOR`, which turns transparent pixels black). 16-bit gray is
  scaled to 8 bits first. Pixel counts of all files are checked from the headers before any is
  decoded. Each file may only reach the Pillow decoder of its own detected type (HEIC never reaches
  Pillow's AVIF plugin, which also accepts `ftyp mif1`).
* The upload is cut off with 413 as soon as the body passes the limit, with or without `Content-Length`.
* Client file names are never used in a path. Folders are named by the random 128-bit job id.

## Fairness

The client is the rightmost `X-Forwarded-For` entry that is **not** inside `TRUSTED_PROXY_CIDR`, and
the header is read only when the TCP peer itself is inside it. Otherwise the peer is the client.
IPv6 clients are counted per /64. The address is stored only as an HMAC hash (the record and the Redis
keys never hold it).

**List proxy addresses, not client networks.** Every host inside a trusted network can set
`X-Forwarded-For` and so choose the address its limits are counted against. `/0` is refused at start;
anything wider than `/24` (IPv4) or `/64` (IPv6) logs a warning. Behind docker, the peer the API sees
is nginx, so `TRUSTED_PROXY_CIDR` must contain the nginx container's network **and** the campus proxy
(rightmost-untrusted skips both). If a peer on a private address sends `X-Forwarded-For` but is not
trusted, the API logs a warning once per peer: that is the signature of a proxy missing from the list.

A job holds a slot from the start of its upload until `finish()`. Slots and queue entries are Redis
structures checked against the job records: a record that expired, a finished job, an unreadable
record and an expired upload reservation all stop counting by themselves, so a crashed worker or a dead
request cannot lock a client out for longer than the matching TTL. The limit is checked when the slot
is taken and again, atomically (WATCH/MULTI), when the job is created.

## nginx

```nginx
location /_protected/jobs/ {
    internal;
    alias /data/jobs/;
    disable_symlinks on;   # never follow a link out of the job folder
}
```

`X-Accel-Redirect` is `/_protected/jobs/<id>/<name>`; nginx serves `/data/jobs/<id>/<name>`. Media files
sit directly in the job folder, next to `input/`. The API checks names and resolved paths itself;
`disable_symlinks on;` is the second lock, in case a link ever appears in a job folder.

For the `/api/` location:

* Keep `proxy_request_buffering on;` (nginx's default). The API then sees a request only after nginx
  has received the whole upload, so a client's slot is held for the ingest, not for a slow upload, and
  a slow client cannot tie up an ingest turn.
* `client_max_body_size` is `MAX_UPLOAD_MB` plus a little.
* `proxy_read_timeout` is `PROXY_READ_TIMEOUT_SECONDS` (default 60 s), which must stay above
  `INGEST_WAIT_SECONDS + INGEST_TIMEOUT_SECONDS + 5`; the API refuses to start otherwise.

## Worker contract

The worker registers an arq function named `process_job(ctx, job_id)` (`decimer_api.queue.PROCESS_JOB`)
and uses `JobStore` (`decimer_api.store`):

1. `mark_running(job_id)` when it starts (takes the job off the waiting queue). If the record is gone
   (it expired while waiting), skip the job.
2. `save(job)` after every stage. A running job's record lives `RUNNING_JOB_TTL_SECONDS` (20 min) after
   its **last save**, so a stage that can take longer than that must save in between.
3. Write `crop-<index>.png` and `page-<index>.png` into `DATA_DIR/<job id>/`.
4. `finish(job)` exactly once, with status `done` or `failed`, **in a `finally`**: it frees the client's
   slot and starts the `JOB_TTL_SECONDS` countdown. A worker that dies without it frees the slot only
   when the record expires.
5. `process_job` returns `None` (arq keeps a function's return value as a result in Redis; the job
   record is the result here).

## Retention

Redis expires records by itself. `python -m decimer_api.sweep` deletes the folders: those whose record
is gone (only when the folder is older than `SWEEP_GRACE_SECONDS`, because the API creates the folder
before the record), those whose `expiresAt` has passed (at the instant it is reached), and those whose
record cannot be read (once nothing in the folder has changed for `JOB_TTL_SECONDS`). It only looks at
directories named like a job id and never follows symlinks. A folder it cannot judge or remove is logged
and counted as failed, and the sweep goes on; with `--every` a failed run does not stop the loop.

## Tests and checks

```bash
uv run pytest                 # no Redis, no network: fakeredis, test files built in code
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Most API tests run the ingest code in a thread; `tests/test_isolation.py` runs the real child process
(deadline, memory limit, crash, signals, environment, stderr).

## Dependencies

Exact pins in `pyproject.toml`. PDFs are read with `pypdfium2` (Apache-2.0 / BSD-3-Clause), images with
Pillow and `pillow-heif`.

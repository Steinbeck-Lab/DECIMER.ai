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
| `WORKER_QUEUE_NAME` | `arq:queue` | The arq queue jobs go on (arq's own default name). **A worker serves the queue this names**, so the fast worker is started with the fast queue's name here (compose does it). |
| `WORKER_FAST_QUEUE_NAME` | `decimer:fast` | The queue small jobs go on, while a fast worker is alive (see "Replicas, fair dispatching and the fast lane"). The same name as `WORKER_QUEUE_NAME` means one queue. |
| `SMALL_JOB_MAX_PAGES` | `1` | A job of at most this many processed pages is small (an image counts as a page: a pasted image, one image, a one-page PDF). `0`: no job is small. Used by the api (queue) and the worker (who goes first). |
| `FAST_LANE_CHECK_SECONDS` | `5` | How long the api keeps its answer to "is a fast worker alive?". |
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
2. `save(job)` whenever a stage changes state (stages overlap, see "What one job does"). A running job's
   record lives `RUNNING_JOB_TTL_SECONDS` (20 min) after its **last save**, so a stage that can take longer
   than that must save in between.
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

## Worker

The arq worker is the other half of the contract above: it takes `process_job(job_id)` from the queue
and turns the files under `DATA_DIR/<id>/input/` into `results`. Code: `src/decimer_api/worker/`;
the RDKit step is `src/decimer_api/chemistry.py` (shared with `/validate`, P3-06).

```bash
cd api
# production: an arq worker, next to the API, Redis and the model services
DATA_DIR=/data/jobs REDIS_URL=redis://redis:6379/0 \
  SEG_URL=http://seg:8001 OCSR_URL=http://ocsr:8002 CLS_URL=http://cls:8003 CHEM_URL=http://chem:8004 \
  uv run python -m decimer_api.worker

# development: the API and the pipeline in one process (no arq worker; jobs do not survive a restart)
DATA_DIR=/tmp/decimer-jobs REDIS_URL=redis://localhost:6379/0 MEDIA_STREAM_DIRECT=true \
  uv run python -m decimer_api.worker.inprocess
```

`DATA_DIR` must be the same directory the API writes to. The worker needs `python -I -m
decimer_api.worker.render` to run (a child process per PDF page), so it must run where `decimer_api`
is installed. Several worker processes may run against one Redis; each has its own per-replica limits
and its own fair dispatcher (the worker of the fast lane is one more process, see below). At start the worker checks that its settings agree with the API's (see `PIPELINE_TIMEOUT_SECONDS`
below) and refuses to start otherwise.

### What one job does

The four stages **overlap**: a page is sent to seg as soon as it is rendered, and a crop goes
to ocsr and cls, and then to the RDKit check, as soon as its place in the job is known; the results are
exactly those of running the stages one after the other. A crop's number is the number of structures on all
earlier pages plus its place on its own page, so it is known only once every earlier page has been segmented,
failed or skipped (the **prefix rule**, `worker/overlap.py`): a later page that seg answers first waits for
the pages before it, and nothing is ever renumbered. Every page is still segmented, also those beyond the
cap, because `structuresFound` counts them. The **stage states** in the record are each stage's own: a stage
is `active` from the moment its first piece of work starts and `done` when its last piece ends **and every
stage before it is done**, so several stages can be active at once and a stage is never done before an
earlier one. The record is saved when a stage changes state (and by the heartbeat), not per page or crop.
`results` are filled in at once, when the check is done. The `warnings` list keeps the order of the
stages (render, segment, recognise, check), not the order in which things happened.

| Stage (its state is saved when it changes) | What happens |
|---|---|
| `render` | PDF: page `i` rendered with pypdfium2 at **exactly 300 DPI** to `page-<i>.png`, one child process per page (deadline, memory and CPU limits, no secrets in its environment; the worker never imports pdfium). Images: `input/000N.png` becomes `page-<N-1>.png` (hard link, else copy), in upload order. A page that cannot be rendered is skipped with a warning; if none can, the job fails. |
| `segment` | seg is called once per page. A page above 25 MP or 36 MiB (seg takes 40 MP and 40 MiB; a B2 page is 49 MP) is sent as a smaller copy; the boxes seg returns are scaled back to the page and the crops are **cut from the full-resolution page** (not from seg's answer: those are the copy's pixels, white outside seg's mask). The crops of a page are written to part files as its answer arrives, only the first `MAX_STRUCTURES` of a page, and read back later, so no crop is held in memory. They are put in reading order (page by page, seg's order within a page) and cut at `MAX_STRUCTURES` (20): `structuresFound` is the total, the rest is `structuresDropped`, `truncated` is set and the warning `structures_truncated` added. **Single-image fallback:** only for a job made of exactly one uploaded image (not a PDF, not several images), and only when seg answered and found nothing: the whole image is the one structure (bbox = the page, `crop-0.png` a hard link to `page-0.png`). seg's answer is checked against what was sent: the echoed page index and image size, every box inside the image. |
| `recognise` | Each kept crop, as soon as its number is known, is read, cut down for the models, and sent to ocsr and cls (together), at most as many crops at once as the replicas of the two services take. The models look at 512 px (ocsr) and 224 px (cls), so a crop above 4 MP or 4096 px on a side is downscaled (and re-encoded until under 19 MiB); the crop file keeps its size. ocsr's SMILES goes through **legacy's fragment filter**: more than four `.`-separated fragments, or a first fragment shorter than all the others together, keeps only the first fragment (warning `fragments_trimmed`; the raw prediction is in the debug log only). |
| `check` | A crop goes through RDKit as soon as it is recognised; the names are asked for once every crop is through. RDKit (`chemistry.py`): canonical SMILES, validity, full InChIKey, V2000 molfile with 2D coordinates and wedge bonds, an SVG that starts with `<svg`. Then IUPAC names: one `POST /names` per job to chem (20 structures per request) if `CHEM_URL` is set, else `name`, `nameTier` and `nameVerified` are null. |

`name`, `nameTier` and `nameVerified` come from chem's `name`, `tier` and `verified` (how the name was checked,
e.g. `opsin`); all three are null together, and chem's `reason` is not stored (a `timeout` reason gives the
`names_unavailable` warning, any other decline is just "no name"). `Result.name_verified` is a field added to
`models.py`.

`results` appear in the record when `check` is done (an earlier poll shows `structuresFound`, once every page
has been segmented, but no results; a job that fails before that has `structuresFound` 0). `confidence` is ocsr's `mean_confidence`. An invalid SMILES keeps its raw text with empty
`inchikey`, `molfile` and `svg`. CXSMILES extensions are refused, not honoured.

**R groups.** DECIMER writes the R groups of a scheme as `[R]`, `[R1]`, `[R2]`, and RDKit has no element
R, so it refuses them. Exactly `[R]` and `[R<one or two digits>]` are rewritten before RDKit reads the
SMILES: `[R1]` becomes the labelled dummy atom `[1*]`, a bare `[R]` becomes `*`. Such a structure is
**valid**; its canonical SMILES contains a `*` (that is what the UI looks for to show the R-group note); its
`inchikey` is empty (InChI has no R groups) and it gets no name (chem is not asked); its molfile has `R#`
atoms and an `M  RGP` line, which Ketcher shows as R1, R2 (a bare `*` is written as `R`). `[Rb]`, `[Rh]`,
`[Ra]`, `[Re]`, `[Ru]` are elements and are left alone; `[R1a]`, `[R+]`, `[R123]` stay invalid.

### When something goes wrong

A service call is retried only where retrying can help:

* **503** waits `Retry-After` (default 5 s, capped at 30 s), up to `BUSY_RETRIES` times.
* **Could not connect** (the service is down or starting): doubling backoff, each wait at most
  `CONNECT_BACKOFF_MAX_SECONDS` (10 s), until `CONNECT_RETRY_BUDGET_SECONDS` (90 s) of waiting have been spent.
  ocsr needs about 50 s to load, so a worker that finds it restarting waits for it. This is done inside the
  call rather than by re-queueing the job with arq's `Retry`: a re-queued job would redo the render and seg
  work, show the user a restart, and use up arq's tries.
* **The connection broke after the request went out** (reset, closed by the server): once more, then that call
  fails. The work may have started, or this very input may crash the service; it is not waited for.
* 422 (the service refuses this input), 500 and a **read timeout** are not retried.

At most `<SERVICE>_CONCURRENCY` calls to one **replica** of a service are in flight (see the next section),
counted over all jobs of the process, and a call waiting out a 503 keeps its slot. ocsr gets one call per replica
at a time (it runs one inference at a time, 3 s for a structure and up to 55 s for an image that is not one)
and a 120 s timeout. An answer is read as a stream and refused above 64 MiB (seg, which sends the crops) or
1 MiB (the others).

| What failed | What the job does |
|---|---|
| ocsr 500 / 422 / timeout, or a lost connection, on one crop | that result is `valid: false`, `smiles: ""`, `confidence: 0`; warning `recognition_failed`; the job goes on |
| ocsr times out `OCSR_MAX_TIMEOUTS` (3) times in one job | the job fails: `service_unavailable` |
| cls 500 / 422 on one crop | `isStructure: null` for it; warning `classification_failed` |
| cls unreachable, busy or timing out | no more calls to cls in this job; `isStructure: null` for the crops it did not answer; one warning `classifier_unavailable` |
| chem fails, times out, or answers nonsense | names stay null; one warning `names_unavailable`. A structure chem ran out of time on: null, the same warning. Any other decline (`UNNAMEABLE`, `NO_VERIFIED_PIN`, ...): null, no warning |
| seg 500 / 422 / timeout, or an image that cannot be fitted to seg's limits, on one page | that page is skipped; warning `page_segmentation_failed` ("Page 3 ..."); on every page the job fails: `segmentation_failed` |
| seg or ocsr unreachable or busy for good | the job fails: `service_unavailable` |
| a page cannot be rendered | that page is skipped; warning `page_render_failed`; no page at all: the job fails, `render_failed` |
| the input is missing, or the job runs longer than `PIPELINE_TIMEOUT_SECONDS` | the job fails: `input_missing`, `timeout` |
| anything else | the job fails: `internal_error` (the traceback goes to the log) |

A failed job has `status: failed`, `error` set to a fixed English message, and a warning with the same
message and the failure's code (the record has no separate error-code field). Nothing from inside (a
traceback, a path, what a service said) is stored. `finish()` is called exactly once, in a `finally`, and
the saves of one job take turns (a lock), so the record written last is the one saved last.
Cancellation is the one exception: when arq stops the worker it cancels the job and runs it again
after the restart (`retry_jobs`, at most 3 runs), so the job is left running; a second run starts from
the state the API created (stages, results, structure counts, `truncated` from the page limit) and keeps
only the warnings the API added. If arq's own `job_timeout` ever cuts a run (it is the pipeline's deadline
plus 60 s, so the pipeline's own deadline normally comes first and explains itself) the worker logs an
error saying so; the record is then left running until it expires.

**What a failed job's record holds (the stages overlap).** It holds what was known when the job failed, which is
not always what a run with the stages one after the other would have known:

* `status: failed`, `error`, no `results`, and the failure's own warning **last**, after any warning raised
  before it. Every stage that had started and not finished is still `active` in `stages`; none after the failing
  one is `done`.
* `structuresFound` and `structuresDropped` are filled in when the last page has been segmented (the same moment as
  the segment stage's `done`). A job that fails earlier, typically `OCSR_MAX_TIMEOUTS` timeouts of ocsr on the first
  pages while seg is still busy with the later ones, has `structuresFound: 0`, `structuresDropped: 0`, `truncated`
  as the API set it (the page limit), and neither the `structures_truncated` warning nor a `page_segmentation_failed`
  for a page seg had not yet answered about.
* The warnings of crops that were recognised before the failure stay (`recognition_failed`,
  `classification_failed`, `fragments_trimmed`, `classifier_unavailable`), also when the failure is seg's: if seg
  turns out to be unreachable for page 2 after a crop of page 1 failed in ocsr, the record has that
  `recognition_failed` and then `service_unavailable` (with the stages one after the other, seg's failure came first
  and the record had only `service_unavailable`).

A successful job's record is exactly the sequential one: results, numbering, counts, the cap warning, and the order
of the warnings (render, segment, recognise, check).

`isStructure` is `bool | null` in the model (a change in `models.py`): null means the classifier gave no
answer. The frontend must not read null as "not a structure".

### Replicas, fair dispatching and the fast lane

Code: `worker/replicas.py` (the pool), `worker/services.py` (the calls), `queue.py` (the lanes).

**Replicas.** A model service is one host name and port (`SEG_URL=http://seg:8001`); in the Compose stack
Docker's DNS answers with the address of every running replica (`deploy/compose.yaml`). The worker keeps one
pool per service: it resolves the name (all A records; AAAA only when there are none), sends each call to
`http://<address>:<port><path>`, looks the name up again every `REPLICA_REFRESH_SECONDS` (10 s) and at once after
a call could not connect, drops an address that has left DNS once it is idle, and uses a new one at once. If the
lookup fails the replicas already known are kept. A URL whose host is an IP address or `localhost`, and a
name that does not resolve at all, is **one replica** reached under the URL as given, so development (and
the in-process mode) with `http://127.0.0.1:8001` is unchanged. `*_CONCURRENCY` is **per replica** (default 1).

**Least-busy routing.** A free slot goes to the healthy replica with the fewest calls in flight; among equals, the
one that went longest without a call. A replica that refused the connection or dropped it is skipped for
`REPLICA_COOLDOWN_SECONDS` (10 s) and the call goes to another healthy replica at once, without waiting and
without spending the connect-retry budget; an answer from it ends the skipping early. A read timeout does not
count against a replica (slow is not dead). When every replica is skipped they are all tried again, and the call's
own retry rules decide (doubling backoff within `CONNECT_RETRY_BUDGET_SECONDS`, then `service_unavailable`). The
rules of the previous section hold per call over all replicas: the busy-503 count, the connect budget and the
one retry after a broken connection (which goes to another replica when one is healthy) are the call's own; a
503 is waited out on the same replica, holding the slot. Because that retry is the only time a request is sent
again after a replica may have started on it, an input that crashes the service it reaches takes down **at most
`1 + SENT_RETRIES` (2) replicas per call**, however many replicas there are.

**No blocking, no starvation.** Several jobs run at once (`WORKER_MAX_JOBS`) and share the replicas. A job asks
for as many calls at once as its service takes in all (replicas x `*_CONCURRENCY`; so a lone job fills every
replica and keeps the lone-job timing of the deploy README). The number is looked up each time a page or crop is
about to start, not once per job, so a replica that joins while a job runs is used from the next page or crop on
(a page or crop already waiting is moved up when one of the job's pages or crops ends). When a slot frees, the waiting calls of that service
are served in this order: calls of a **small job** first (`Settings.is_small_job`: at most `SMALL_JOB_MAX_PAGES`
processed pages, the same rule in the api and the worker, so nothing about size is stored on the record);
among the jobs of one size class the one served longest ago, so the crops of two big jobs interleave; and after
`SMALL_FIRST_BURST` (4) small calls in a row, while a large call is waiting, the next slot goes to a large call
(the count restarts when no large call waits). So a small job does not queue behind the calls of big PDFs, but it
can still wait for: the call already running on a replica (no call is interrupted; ocsr takes 3 s, up to 55 s
for an image that is not a structure), including a call of the other worker process (a replica serves one call at
a time for both); the calls of other small jobs ahead of it in the turn order; and the one slot in
`SMALL_FIRST_BURST + 1` that goes to a large call while one is waiting. A large job gets at least one slot in
`SMALL_FIRST_BURST + 1`. Rendering is one page at a time per job through a shared semaphore, which already takes
turns between jobs. Since the stages overlap, a large job has ocsr calls running while it is still
segmenting its later pages, so a pasted image that arrives then can find one of that job's ocsr calls on a replica and
wait for it: at most one call per replica, within the bound above (a call is never interrupted), only reached
earlier in the large job's life than before.

**Memory.** A page is read, decoded and held as a PNG (up to 36 MiB for a 50 MP page) only once the job has a seg
slot, and a crop only once it has an ocsr slot (`ServiceCaller.slot`, then `post(lease=...)`), so what a worker
holds, and how many threads decode images, is bounded by the slots of the services, not by the number of jobs
waiting for them. A lookup of the replicas never holds up a call: after the first one it runs in a background
task, and only a call that has just failed to connect waits for a fresh one. The HTTP client has no connection
cap (the pools bound what is in flight) and lets go of an idle connection after 2 s, before uvicorn's 5 s, so a
reused connection is not reset under a call.

**The fast lane.** `Settings.is_small_job(job.pages_processed)` is decided when the job is created; the api
puts a small job on `WORKER_FAST_QUEUE_NAME` and every other job on `WORKER_QUEUE_NAME`. The fast queue is
served by a second worker (`worker-fast`, part of the default Compose start): the same program with
`WORKER_QUEUE_NAME` set to the fast queue's name, which gives it its own `WORKER_MAX_JOBS`, so a small job does not
wait for a place behind big PDFs either. The fast worker may not be running (stopped, starting, a development
setup), and a queue nobody serves would hold a job forever, so:

* the api sends a small job to the fast queue **only while a fast worker is alive**: arq's worker keeps
  `<queue name>:health-check` (set every minute, 61 s lifetime, deleted on a clean stop), and the api checks that
  key (at most every `FAST_LANE_CHECK_SECONDS`). No fast worker: the small job goes on the default queue and
  the default worker runs it, its calls still going first. If Redis cannot answer, the default queue is used.
  There is no setting to keep in step with the container.
* a job on the fast queue whose worker has died is **moved** to the default queue by the default worker
  (`queue.requeue_stranded`, every `FAST_LANE_RESCUE_SECONDS`, 30 s, only when the fast queue has entries and no
  fast worker is alive). Each move is one Lua script: it happens only if the job is still on the fast queue (a
  fast worker that is starting may have taken it since the list was read), the score is kept (so the job keeps
  its turn) and a job already on the default queue keeps its place (`ZADD NX`); a job in both queues runs once,
  because arq's in-progress key is per job id. A job still **waiting** on the dead worker's queue waits at most
  about 90 s (the key's 61 s plus the 30 s). A job that was **running** on it is moved too, but it is started
  again only when arq's in-progress key expires, `job_timeout + 10 s` after the run began, about 18 minutes with
  the defaults, and its record (20 minutes after its last save) may be gone by then.
* the container health check (`deploy/worker_healthcheck.py`) reads `WORKER_QUEUE_NAME` and looks for the same
  key arq's worker of that queue keeps (a test compares the two).

`queuePosition` is the job's place in the arrival order over both lanes (it is kept in the store's own
sorted set, not in an arq queue), so a small job on the fast lane can be shown a position behind jobs it does
not wait for.

The in-process development mode has no lanes: `InProcessEnqueuer` runs every job in the process, at most
`WORKER_MAX_JOBS` at once, and only the dispatching of the calls to the services is fair.

### Worker settings

Environment variables, read by `WorkerSettings` (`worker/settings.py`); `.env.example` has the block.

| Variable | Default | Meaning |
|---|---|---|
| `SEG_URL`, `OCSR_URL`, `CLS_URL` | `http://127.0.0.1:8001`, `:8002`, `:8003` | Base URLs of the model services. |
| `CHEM_URL` | empty | The chem service; empty switches names off. |
| `SEG_CONCURRENCY`, `OCSR_CONCURRENCY`, `CLS_CONCURRENCY`, `CHEM_CONCURRENCY` | `1`, `1`, `1`, `1` | Calls in flight **per replica** of the service, per worker process (before P3-04b: service totals, `2`, `1`, `2`, `1`). |
| `RENDER_CONCURRENCY` | `1` | Pages rendered at once (a 50 MP page needs about 0.4 GB). |
| `WORKER_MAX_JOBS` | `4` | Jobs one worker process runs at once (arq `max_jobs`); they share the replicas. About twice the ocsr slots: `4` beside legacy (2 replicas), `8` after cutover (3). |
| `REPLICA_REFRESH_SECONDS` | `10` | How often a service's name is looked up again for replicas (and at once after a connection failed). |
| `REPLICA_COOLDOWN_SECONDS` | `10` | How long a replica that refused or dropped a connection is skipped. Above 0. |
| `SMALL_FIRST_BURST` | `4` | Small calls in a row after which a waiting large call gets the next slot. At least 1. |
| `FAST_LANE_RESCUE_SECONDS` | `30` | How often the default worker looks for jobs stranded on the fast queue (0 = never). |
| `CONNECT_TIMEOUT_SECONDS` | `5` | Connect timeout of every call. |
| `SEG_TIMEOUT_SECONDS`, `OCSR_TIMEOUT_SECONDS`, `CLS_TIMEOUT_SECONDS`, `CHEM_TIMEOUT_SECONDS` | `180`, `120`, `60`, `120` | Read timeout: the longest a call may take, queue time in the service included. ocsr needs up to 55 s for an input that is not a structure; chem's own budget for a batch is 60 s plus a restart. |
| `OCSR_MAX_TIMEOUTS` | `3` | ocsr timeouts in one job after which the job gives up. |
| `PIPELINE_TIMEOUT_SECONDS` | `1000` | One job, start to finish. arq's own limit is this plus 60 s. **Start-up check:** this plus 60 must be below `RUNNING_JOB_TTL_SECONDS` (1200), and `HEARTBEAT_SECONDS` at most a quarter of it; to allow longer jobs raise both. |
| `HEARTBEAT_SECONDS` | `60` | A running record is saved at least this often. |
| `BUSY_RETRIES`, `BUSY_RETRY_AFTER_DEFAULT_SECONDS`, `BUSY_RETRY_AFTER_MAX_SECONDS` | `3`, `5`, `30` | 503 handling. |
| `CONNECT_RETRY_BUDGET_SECONDS`, `CONNECT_BACKOFF_SECONDS`, `CONNECT_BACKOFF_MAX_SECONDS` | `90`, `0.5`, `10` | Could-not-connect handling: doubling backoff, capped per wait, until the budget is spent. |
| `SENT_RETRIES` | `1` | Retries after the connection broke once the request was sent. |
| `SEG_RESPONSE_LIMIT_MB`, `SMALL_RESPONSE_LIMIT_KB` | `64`, `1024` | The most an answer may be (seg's, the others'). |
| `RENDER_TIMEOUT_SECONDS`, `RENDER_MEMORY_MB` | `90`, `2048` | Deadline and extra address space of one render child. |
| `MODEL_INPUT_MAX_PIXELS`, `MODEL_INPUT_MAX_SIDE`, `MODEL_INPUT_MAX_BYTES` | `4000000`, `4096`, `19922944` | Larger crops are downscaled and re-encoded before ocsr and cls (they take 17.5 MP / 8192 px / 20 MiB and 50 MP / 20 MiB). |
| `SEG_INPUT_MAX_PIXELS`, `SEG_INPUT_MAX_BYTES` | `25000000`, `37748736` | Larger pages are sent to seg as a smaller copy (seg takes 40 MP and 40 MiB). |
| `CHEM_BATCH_SIZE` | `20` | Structures per `POST /names` (chem takes at most 20). |
| `SHUTDOWN_WAIT_SECONDS` | `0` | How long arq waits for running jobs when stopped (0: cancel them). A container's stop grace period must exceed it. |
| `CDK_FALLBACK` | `false` | D4: a seam only. With it on and no CDK backend plugged into `Deps.cdk_fallback`, the worker logs a warning and RDKit decides alone. |

The worker also reads the API's `REDIS_URL`, `DATA_DIR` and `MAX_STRUCTURES`. The HTTP client ignores
`HTTP_PROXY` and friends (`trust_env=False`): the services are on the internal network. The worker
process (and the in-process mode) makes itself non-dumpable at start, like the API, so its render children
cannot read its environment.

### Tests

`uv run pytest` runs the worker tests too: fake seg, cls, ocsr and chem behind `httpx.MockTransport`,
PDFs and PNGs built in code, fakeredis, and a real arq worker in burst mode on fakeredis. One opt-in
test runs the real pipeline against model services you started yourself:

```bash
SEG_URL=http://127.0.0.1:8001 OCSR_URL=http://127.0.0.1:8002 CLS_URL=http://127.0.0.1:8003 \
  uv run pytest -m slow tests/worker/test_live.py -s     # page 3 of the golden paper; LIVE_PAGES="3,4"
```

It is deselected by default (`addopts = -m "not slow"`) and skipped when the three variables are unset.

### Dependencies added for the worker

`httpx==0.28.1` (moved from the dev group) and `rdkit==2026.9.1` (BSD-3-Clause; the same version the
golden-set tool uses, so both compute identical InChIKeys).

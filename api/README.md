# decimer-api

FastAPI job API for decimer.ai (Python 3.12). This project holds the HTTP API, the job store, the
upload hardening and the TTL sweep. The arq worker joins it later; the places where it
plugs in are marked below.

```
POST /api/jobs ──► take the client's slot ► read the body ► classify (magic bytes)
                   ► ingest in a child process (limits, deadline) ► files in DATA_DIR/<id>/input/
                   ► Redis record ► arq queue
GET  /api/jobs/<id> ──► status, progress, results
DELETE /api/jobs/<id> ──► stop it, free the client's slot, remove its files
GET  /api/jobs/<id>/media/<name> ──► X-Accel-Redirect to nginx (job-scoped)
POST /api/jobs/<id>/results/<n>/validate ──► an edited molfile in, SMILES, InChIKey, SVG, IUPAC name out
POST /api/jobs/<id>/export ──► ZIP, SDF or CSV of the structures with the user's edits
POST /api/jobs/<id>/report ──► keep a result and its image on the private reports volume
GET  /api/engines ──► the versions of the models and libraries behind the results
```

## Run

```bash
cd api
uv sync
DATA_DIR=/tmp/decimer-jobs REDIS_URL=redis://localhost:6379/0 \
  MEDIA_STREAM_DIRECT=true uv run python -m decimer_api        # http://127.0.0.1:8000

uv run python -m decimer_api.sweep                 # delete expired job folders, once (for cron)
uv run python -m decimer_api.sweep --every 300     # or as a long-running sidecar
uv run python -m decimer_api.sidecar               # the ingest sidecar (INGEST_SIDECAR_URL=tcp://127.0.0.1:9000 for the api)
uv run python -m decimer_api.usage --days 30       # the per-day usage counts (jobs, pages, structures, failures)
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
| `CHEM_URL` | empty | The chem service, for the IUPAC name of an edited structure (validate, export). Empty: names are null. The worker has its own `CHEM_URL` (see "Worker settings"). |
| `NAMES_TIMEOUT_SECONDS` | `4` | The most validate and export spend on chem, with no retry. A name that is not there in time is null. The api has **one** call in flight to chem; a validate that finds it occupied answers with no name at once, only an export waits for it: the worker has priority on chem. |
| `MAX_CONCURRENT_ANALYSES`, `MAX_CONCURRENT_DOWNLOADS` | `2`, `8` | Child processes reading client molfiles (one place each; one place for all the edits of an export), and ZIP downloads streamed at once. Nothing queues: the next request gets `503 busy` with `Retry-After`. |
| `MAX_DOWNLOADS_PER_CLIENT`, `DOWNLOAD_TIMEOUT_SECONDS` | `2`, `120` | Of the ZIP downloads, what one client address may have going (`429` with `Retry-After` beyond it), and how long one may take: after that the stream is aborted (the client sees an incomplete file) and its place and files are freed. |
| `ANALYSIS_MEMORY_MB`, `ANALYSIS_TIMEOUT_SECONDS`, `ANALYSIS_BATCH_SECONDS`, `ANALYSIS_STARTUP_SECONDS`, `ANALYSIS_RECYCLE_AFTER` | `384`, `5`, `20`, `30`, `500` | A molfile-reading child may allocate this much beyond what RDKit maps (address-space limit); one molfile may take this long, all the edits of an export this long; a child has this long to start; it is replaced after this many molfiles. |
| `CANCELLING_SLOT_SECONDS` | `185` | A client that deletes a **running** job keeps its slot for this long, whatever the worker does (`CANCEL_POLL_SECONDS` plus the longest service read timeout: what an abandoned call can go on computing; the worker refuses to start with numbers that add up to more). |
| `MAX_MOLFILE_BYTES` | `65536` | A molfile sent by the client. `413` above it. A request body is cut off at twice this (validate) or 1 MiB (export, as nginx does). |
| `VALIDATE_RATE_PER_MINUTE`, `EXPORT_RATE_PER_MINUTE`, `REPORT_RATE_PER_HOUR` | `60`, `10`, `30` | Requests per client address, a fixed window counted in Redis before the body is read. `429` with `Retry-After` beyond it. |
| `REPORTS_DIR` | `/data/reports` | The private volume of problem reports (only the api mounts it). |
| `MAX_REPORTS`, `REPORTS_MAX_BYTES`, `MAX_REPORT_IMAGE_BYTES` | `5000`, `2147483648`, `4194304` | At most this many report folders and this many bytes in all (counted from the disk, so a restart does not forget them; `503` beyond, a warning at 80 % of either); a crop image over the size is left out of its report. |
| `REPORTS_MAX_AGE_DAYS` | `364` | A report older than this many days is deleted, whether or not anyone has analysed it: at start-up and then every hour, by the api itself. The privacy page says 12 months at the latest, the shortest of which is 365 days, so the default leaves the hour between runs (and a day's margin); keep it under 365 (`tests/test_privacy_promises.py` checks the examples and the default). |
| `HOST`, `PORT` | `127.0.0.1`, `8000` | `python -m decimer_api` only. |
| `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE` | empty, `production`, empty | Error reports to the stack's GlitchTip; empty DSN: off. A bad DSN is logged and ignored. In compose the DSN host is `glitchtip-gate:8000`, a proxy that passes event posts only; GlitchTip itself is not reachable from the api. The release is the package version when empty (compose passes `DECIMER_TAG`). See "Observability". |
| `LOG_FORMAT`, `LOG_LEVEL` | `json`, `INFO` | One JSON object per line (`text` for a terminal); the level of every logger. |
| `USAGE_REDIS_URL`, `USAGE_TTL_DAYS` | empty (`REDIS_URL`), `800` | Where the per-day usage counts are kept, and for how long. See "Usage counter". |
| `INGEST_SIDECAR_URL` | empty | `tcp://ingest:9000`: send uploads and molfiles to the ingest sidecar instead of parsing them in children of this process. Empty: in-process (development). |
| `SIDECAR_HOST`, `SIDECAR_PORT`, `SIDECAR_SCRATCH_DIR` | `127.0.0.1`, `9000`, `/scratch` | `python -m decimer_api.sidecar` only: where it listens, and where it keeps the request in flight. |

## Endpoints

| Method and path | Success | Errors |
|---|---|---|
| `POST /api/jobs` multipart, one or more `file` parts | `202` job JSON, `Location: /api/jobs/<id>` | `400` mixed PDF and images, several PDFs, too many files, corrupt or encrypted file, no file; `413` too large, too many pixels, or a file the parser could not handle within its limits (`processing_limit`); `415` not a supported type, or not multipart; `429` client has `MAX_ACTIVE_JOBS_PER_IP` active jobs; `503` queue full or service busy (`Retry-After`), Redis or enqueue down |
| `GET /api/jobs/{id}` | `200` job JSON (`queuePosition` while queued), `Cache-Control: no-store` | `404` unknown, expired or malformed id |
| `DELETE /api/jobs/{id}` | `204`; the job is gone at once (see "Deleting a job") | `404` unknown, expired or malformed id, or already deleted |
| `POST /api/jobs/{id}/results/{n}/validate` JSON `{"molfile"}`, `n` the 0-based result index | `200` `{smiles, inchikey, svg, valid, name, nameTier, nameVerified}` | `404` job or result; `413` molfile too large; `422` not a molfile; `429` |
| `POST /api/jobs/{id}/export` JSON `{"format": "zip"\|"sdf"\|"csv", "molfiles": {"<index>": "<molfile>"}}` | `200` the file (`Content-Disposition: attachment`) | `404`; `409` `no_results`; `413`; `422` a key that is not a result of the job, a molfile that cannot be used, unknown fields; `429` |
| `POST /api/jobs/{id}/report` JSON `{"index"}` | `204` | `404` job or result; `413`; `422`; `429`; `503` the volume is full or cannot be written |
| `GET /api/engines` | `200` `{engines: [{id, name, role, version, revision}]}`, cacheable for 5 minutes | |
| `GET /api/jobs/{id}/media/{name}` | `200`, empty body, `X-Accel-Redirect: /_protected/jobs/<id>/<name>` (or the file itself with `MEDIA_STREAM_DIRECT`) | `404` unknown job, name not `crop-<n>.png` or `page-<n>.png`, file missing, symlink, or traversal |
| `GET /healthz` | `200` | |
| `GET /readyz` | `200` when Redis answers and `DATA_DIR` is writable | `503` |
| `GET /api/openapi.json` | the OpenAPI schema (the source for the generated TypeScript types, P3-10) | |

Every error body has one shape, whatever produced it (our checks, an unknown path, a wrong method, a
validation failure, a bug): `{"detail": {"code": "...", "message": "..."}}`. `code` is the enum
`ErrorCode` (`errors.py`), so the OpenAPI schema lists every code and the web client's own list is
checked against it: `rate_limited`, `queue_full`, `busy`, `too_large`, `too_many_pixels`,
`processing_limit`, `unsupported`, `mixed_upload`, `multiple_pdfs`, `too_many_files`, `corrupt_file`,
`encrypted_pdf`, `empty_upload`, `invalid_request`, `unavailable`, `not_found`, `no_results`,
`method_not_allowed`, `http_error`, `internal_error`. An unknown code fails where an `ApiError` is
raised. A rejected upload never has a job id or a queue position.

### Results: validate, export, report

`result_routes.py`. The three POSTs are `@guarded` (`guards.py`): the client's rate limit is counted and
the body size checked **before** the body is read (FastAPI would read it first, whatever its size).
Everything the client sends is checked against the job's own record. No name, path or order comes from
the request; unknown JSON fields are refused.

* **validate.** The molfile goes through `clean_molfile` (control characters, a lone surrogate, a `$$$$`
  line or no `M  END` line are `422`) and RDKit (`analyse_molfile`). **The byte cap does not bound what
  RDKit costs** (44 dummy atoms joined each to each are 15 KB and take 5.7 s and 484 MB to parse, 60 take
  46 s and 2.6 GB, with the GIL held), so `clean_molfile` first counts the structure **from its text,
  before RDKit is asked anything**: the counts line (V2000) or `COUNTS` (V3000) checked against the atom and
  bond lines that are there, and refuses (`422`) more than 300 atoms, 350 bonds, 40 independent rings
  (bonds - atoms + pieces) or 8 bonds at one atom, a version line that disagrees with the body, or a bond
  to an atom that is not there. **A gate is a copy of a parser, so it reads the version the way RDKit
  does** (the counts line's field at columns 35-39; blank is V2000, anything but `V2000` and `V3000` is
  refused; no `M  V30` line in a V2000 file, nothing but `M  V30` lines after the counts line of a V3000
  one; plain continuation lines only; **everything from the counts line to `M  END` ASCII**, because RDKit
  reads columns by byte and Python splits at Unicode spaces that RDKit does not; fields split at ASCII
  spaces, no tabs) and `tests/test_molfile_fuzz.py` checks it against RDKit on 5000 seeded adversarial
  molfiles, comparing which atoms each bond joins and not only the counts (zero disagreements at 400,000
  seeds; the same fuzz finds the bypasses of the gates it replaced).
  **Defence in depth:** the api process never runs RDKit's parser. Client molfiles are read by
  `analysis_pool.AnalysisPool`: `MAX_CONCURRENT_ANALYSES` (2) long-lived child processes
  (`analysis_worker`, started when the api starts), each reading one molfile at a time with an
  address-space limit (`ANALYSIS_MEMORY_MB`, 384 beyond what RDKit itself maps, set in the child for
  good), no file to write, and a wall-clock timeout (`ANALYSIS_TIMEOUT_SECONDS`, 5; an export's edits
  share `ANALYSIS_BATCH_SECONDS`, 20). A child that is slow, out of memory, crashed or talking nonsense
  is killed and replaced and the request is `422` "too complex to read"; if no child can be started it
  is `503 unavailable`. A request that finds both children busy is `503 busy` with `Retry-After`
  (`capacity.py`: no queue); an export takes one child for all its edits. The event loop never waits
  for a molfile, so a bad one cannot stall the other requests (RDKit holds the GIL while it parses).
  A child is replaced after `ANALYSIS_RECYCLE_AFTER` (500) molfiles. It gets the ingest child's
  minimal environment and runs as the same user, with the api non-dumpable, as the ingest child does
  (`ingest_runner.py`). **Memory:** about 70 MB resident per child idle, at most 70 + 384 MB at
  the limit, so about 0.15 GB in normal use and 0.9 GB at the ceiling for two. The api container's
  `mem_limit` is 4608m: the api and its spooled bodies 0.5 GB + two ingest children at 1.5 GB + two
  molfile children at about 0.45 GB, every one at its ceiling at once (compose.yaml has the sum).
  Structures the gate admits were measured at under 0.06 s and 72 MB. These limits are for
  client molfiles; a stored one is only written to a file.
  What comes back: the same canonical SMILES, full
  InChIKey and SVG the worker computes for a recognised structure (`chemistry.py`). A molfile RDKit reads
  but cannot sanitize is `valid: false` with empty `smiles`, `inchikey` and name and **its drawing as it is**
  (`svg`, drawn without sanitizing, like what only CDK accepted: an edit of a still hypervalent structure
  keeps a picture); one with no atoms is `valid: false` with all values empty. Neither is an error. The name
  comes from chem (`names.py`) for a valid structure without a dummy atom; it is `null` when
  `CHEM_URL` is empty, chem is down, slow (`NAMES_TIMEOUT_SECONDS`) or declines. A name is never an
  error, and is never kept between requests (Orthonym can name one structure differently depending on
  what it named before). The api has one call in flight to chem, and a validate that finds it occupied
  answers with no name at once (the worker has priority on chem; an export waits for its turn).
  **No CDK here.** CDK reads SMILES, and the worker tries DECIMER's SMILES with it; the
  molfile of a validate came from the editor, which writes its own, so RDKit alone decides (`valid: false`
  when it refuses it). A structure only CDK accepted has CDK's molblock stored as its molfile; that text
  passes `clean_molfile` unchanged (the worker only stores a molblock that does) and goes through the same
  validate and export as any other. The answer has no `validBy`: the UI takes `valid` as RDKit's word.
* **export.** Rows are the job's results in order. `molfiles` holds only the edits: a molfile sent for
  result `i` replaces the stored one, and that row's SMILES, InChIKey, validity and name are computed
  from it (chem is asked for the edited, valid ones only); the others are as stored. A key that is not
  `0`..`n-1` (no signs, no padding, no spaces) or a molfile that cannot be used refuses the whole export
  with `422`, and one over `MAX_MOLFILE_BYTES` with `413`. The ZIP has `structure-NN.png` (the crop; left out for a result that has no crop file, an image of
  several that could not be read, D23, whose line in the list stays; a symlink is never followed),
  `structure-NN.mol` (the edit, verbatim up to `M  END`, or the stored molfile; left out when there is
  none) for each structure and `results.smiles.txt`, `image<TAB>SMILES<TAB>IUPAC name`. File names are
  made from the structure's number (zero-padded to the width of the largest). It is streamed: the crops
  are opened before the first byte, so a job deleted or swept during the download cannot cut it short,
  and at most `MAX_CONCURRENT_DOWNLOADS` (8) streams run at once (`503 busy` beyond), two per client
  (`MAX_DOWNLOADS_PER_CLIENT`, `429`), each for at most `DOWNLOAD_TIMEOUT_SECONDS` (120; a client that does not
  read is cut off); the places and the files are given back when the response ends, however it ends; compose gives the api `nofile` 4096).
  The SDF has a record per structure with a molfile (`NUMBER`, `SMILES`, `INCHIKEY`, `NAME`, `EDITED`
  data items, and `VALID` = `cdk` where only CDK accepted the structure), the CSV a row for every structure
  (`number,smiles,inchikey,valid,name,edited`; **contract change:** `valid` used to be `true|false` and is now
  `true|cdk|false`: `true` when RDKit accepts the structure, `cdk` when
  RDKit refused it and CDK read it (CDK's molblock is then the molfile, the SMILES is DECIMER's, `inchikey`
  and `name` are empty), `false` for neither; an edited structure is `true` or `false` only: RDKit judges the
  editor's molfile). Text that
  came from OCSR or chem is put on one line, cannot start an SDF record or data item, and a CSV cell that
  starts like a formula gets a leading `'`.
* **report.** Only `{"index"}` is sent. The api writes `REPORTS_DIR/<job id>-<index>/` with `crop.png`
  (copied from the job, no symlinks; left out above `MAX_REPORT_IMAGE_BYTES`) and `result.json` (the
  result as recognised, the job's page counts and warning codes, the engines' versions, the time):
  folders `0700`, files `0600`, written under a temporary name (`.tmp-*`, not counted against
  `MAX_REPORTS` or `REPORTS_MAX_BYTES`; `tidy` removes those older than an hour at start-up, and warns at 80 % of either cap) and
  renamed into place, with the cap checked and the rename done under one lock. A result reported
  twice is kept once (`204` both times). The folder name is built from a validated job id and an
  integer; nothing from the client reaches a path. What a report holds, and no more: the crop image,
  and in `result.json` the job's id, the result as recognised (never the client's edits: only the
  index comes from the client), the job's page counts and warning codes, the engines' versions and the
  time. No client address. The privacy page says a report is deleted once it has been analysed (by hand:
  delete the folder) and at the latest after 12 months (by the api: **`REPORTS_MAX_AGE_DAYS`**, 364:
  a day short of a year, because the shortest twelve months are 365 days).
  `tidy` deletes the reports older than that at start-up, and `tidy_every` (a task of the api's
  lifespan, cancelled at shutdown) runs it again every hour, so a report is gone within an hour
  after it reaches the age, which is a day before the 12 months are up. A report's age is the older of its folder's time and `reportedAt` in
  its `result.json`; only folders named `<32 hex>-<index>` are ever deleted (no symlink is followed,
  nothing else on the volume is touched). The volume is in no backup (`deploy/README.md`).
* **engines.** The model versions are in `engines.py`, next to the pins in `services/*/uv.lock` that they
  mirror (a test compares them); RDKit's is read from the installed package. No service is called.

### Deleting a job

`DELETE /api/jobs/{id}` (a user who starts over) calls `JobStore.cancel`: the `cancelled:<id>` marker
(Redis, `RUNNING_JOB_TTL_SECONDS`) is set first, then the record is deleted and the job's folder removed.
From then on the job is a `404` everywhere. The client's **slot** is freed at once only for a job that
was still **queued** (taking it off the queue proves no worker has it) or has finished. For a job that
may be **running** the slot is kept, as a `cancelling:<id>` member, for `CANCELLING_SLOT_SECONDS`
(185), whatever the worker does, even once it has stopped the run: the model services are still
computing the calls it abandoned, and upload, run, delete, upload must not pile up more work than `MAX_ACTIVE_JOBS_PER_IP`.
A **queued** job is skipped by the worker (no record). A **running** job: until the worker stops,
`JobStore.save` (one atomic Lua step: write unless the marker exists) and `finish` write nothing for it, so
the record cannot come back, and
`worker/cancel.py` (`run_cancellable`, around the pipeline in `arq_app.process_job` and the in-process
mode) looks for the marker every `CANCEL_POLL_SECONDS` and cancels the run, then removes what the run
wrote. The pipeline itself knows nothing about this. A finished job just loses its files early.

JSON is camelCase. A job:

```json
{"id": "<32 hex>", "status": "queued", "queuePosition": 1,
 "stages": [{"name": "upload", "state": "done"}, {"name": "render", "state": "pending"}],
 "pages": 12, "pagesProcessed": 10, "pageUrls": ["/api/jobs/<id>/media/page-0.png", "..."], "truncated": true,
 "structuresFound": 0, "structuresDropped": 0, "results": [],
 "warnings": [{"code": "pages_truncated", "message": "..."}], "error": null,
 "createdAt": "...Z", "expiresAt": "...Z"}
```

`Result` (filled by the worker): `index`, `number`, `pageIndex`, `bbox` `[x0,y0,x1,y1]`, `smiles`,
`inchikey`, `valid`, `validBy`, `isStructure`, `confidence`, `name`, `nameTier`, `svg`, `molfile`, `cropUrl`.
`validBy` is `"rdkit"` for a valid result, `"cdk"` for one that RDKit refused and only CDK accepted (`valid` is
`false`; `molfile` is CDK's, `svg` RDKit's drawing of it, `smiles` DECIMER's own text, `inchikey` and `name`
empty), `null` for one neither accepted.
`pageUrls` lists `page-0.png` to `page-<pagesProcessed - 1>.png`, one URL for each page the job processes, in
page order and same-origin like `cropUrl` (the UI's "Display uploaded document", shown for PDFs). An upload of
images lists every image; a PDF lists its first `MAX_PDF_PAGES` pages, and the pages past the cap are not
processed and not listed. The list is the plan, not a check that the files exist: a page the worker could not
render (warning `page_render_failed`), or has not rendered yet, answers `404`.
The stored record (`Job`, `Result`) holds none of the derived fields (`queuePosition`, `number`,
`cropUrl`, `pageUrls`); the API composes them when it returns the job (`JobView`, `ResultView`).

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
  root. **The sidecar (P3-11, below) closes the rest**: with `INGEST_SIDECAR_URL` set, uploads are parsed in
  another container that has no job data and no route to Redis, and the API sends them over a socket.
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
6. A job whose owner deletes it (`DELETE /api/jobs/{id}`) is stopped and removed by `run_cancellable`
   (see "Deleting a job"): `save` and `finish` write nothing for it, and `process_job` returns normally.

## Retention

Redis expires records by itself. `python -m decimer_api.sweep` deletes the folders: those whose record
is gone (only when the folder is older than `SWEEP_GRACE_SECONDS`, because the API creates the folder
before the record), those whose `expiresAt` has passed (at the instant it is reached), and those whose
record cannot be read (once nothing in the folder has changed for `JOB_TTL_SECONDS`). It only looks at
directories named like a job id and never follows symlinks. A folder it cannot judge or remove is logged
and counted as failed, and the sweep goes on; with `--every` a failed run does not stop the loop.

## Observability and the sidecar (TODO, D21)

### Error reports

`observability.init_error_reporting` starts the Sentry SDK (`sentry-sdk==2.71.0`) when `SENTRY_DSN` is set: in
`create_app_from_env` (service `api`: Starlette and FastAPI integrations, route templates as transaction names, so
`/api/jobs/{job_id}` and never an id) and in the worker's `main` (service `worker`: arq integration, started before arq
builds its worker). Both also report every `log.error` / `log.exception` (that is how the pipeline's "failed
unexpectedly" and the 500 handler reach GlitchTip). It is configured to collect nothing: `send_default_pii=False`, no
request bodies, no local variables, no source lines, no breadcrumbs, no sessions or tracing or metrics, the default
integrations replaced by an explicit list (the standard-library one would add subprocess and HTTP breadcrumbs). The
ingest, render and molfile children never import it. `before_send` is `scrub.scrub_event`: an **allowlist** that rebuilds
the event from the exception types, the stack as function names, line numbers and module names, the SDK and runtime
versions, tags and the request's method and path (query and fragment cut, job ids `:id`, only `Content-Type` and
`Content-Length` headers), and drops everything else; the text it keeps (messages, exception values, tags) goes through
`scrub.scrub_text`, which replaces molfiles, file names, job ids, IP addresses and SMILES strings (`[smiles]`) by
placeholders. A SMILES is found as a *token*: text between spaces and the delimiters a message puts around one
(quotes, brackets, `,;|<>{}`, `!?&`), tried whole, then without the brackets, quotes and punctuation that wrap it, then
as the value of `key=value` or the part of a path (`/uploads/CCO`), up to three levels deep and repeated until the text
stops changing (`id=CCO/id=CCO`); a token is SMILES when it is wholly SMILES pieces and starts with an atom (`10s`,
`n=1` and `2026-10-23` are safe; a lower-case start needs five characters). `?query` is cut from anything that looks
like a URL or path. File names are found by their extension (images, PDF, Office, chemistry formats such as `.mol`,
`.cdx`, `.ket`) and, when quoted, as a whole including spaces. **What it does not catch**, the same in
`web/src/observability/scrub.ts` and written down so that nobody relies on it: a file name with no extension, an
unquoted file name with spaces (the words before the last one stay), and a SMILES of at most four capital letters and
no carbon (`NN`, `OO`, `ONNO`; `INFO` and `NASA` look the same, so they are left alone). A scrubber that fails
drops the report. Tests (`tests/test_scrub.py`, `tests/worker/test_reporting.py`) run a crafted event through it, and the
real SDK with a recording transport through a crashing FastAPI request, a logged error and a failing arq job. The web app
has the matching scrubber (`web/src/observability`).

### Logs

`logs.configure_logging` (called by `python -m decimer_api`, the worker, the sweep and the sidecar) writes one JSON object
per line: `time`, `level`, `logger`, `message`, and `exc` for a traceback, the same shape as the model services' logs. The
text goes through the same scrubber, with two differences: job ids are kept (they are how a problem with one job is
followed up) and private, loopback and link-local addresses are kept (`replica 172.29.0.5 joined`); public addresses,
SMILES, molfiles and file names are replaced. uvicorn runs with `log_config=None` (its own format has the client address)
and its access log stays off.

### Usage counter

`usage.py` replaces the legacy TSV files: per UTC day, in a Redis hash `decimer:usage:<date>`, the jobs accepted by kind
(`pdf`, `image`, `paste`: one image named `pasted-image...`, which is what the web app calls a pasted one; a test keeps the
two in step) and size (`small`/`large`, the fast-lane rule), `done` and `failed` jobs, `pages` and `structures` of the jobs
that finished `done`, and `refused.<error code>` for uploads that got an error. The api counts at `POST /api/jobs`, the
worker when a job finishes. No address, no job id, no file name, no time of day. Best effort: a Redis that is down or slow
(one second) costs a warning and that count. `USAGE_REDIS_URL` points at a Redis that survives a restart (compose has one,
`usage`); `USAGE_TTL_DAYS` (800) after the last count of a day it expires. `python -m decimer_api.usage --days 30 [--json]`
prints it (a table with a total, and the refusals by code); there is no HTTP route.

### The ingest sidecar

`python -m decimer_api.sidecar` is a small TCP server (`sidecar.py`, framing in `sidecar_wire.py`) that runs the api's
untrusted-file work in a container of its own: `ingest` (the same `SubprocessIngest`: a child per upload with a deadline and
hard limits) and `analysis` (the same `AnalysisPool` of RDKit children, one place per connection). With
`INGEST_SIDECAR_URL=tcp://ingest:9000` the api uses `RemoteIngest` and `RemoteAnalysis` (`sidecar_client.py`) behind the same
`IngestRunner` and `Analysis` seams, so nothing else changes. The uploads travel over the connection into the sidecar's own
scratch directory, the normalised files come back, and the api writes them into `DATA_DIR/<job>/input/` itself after
checking them: the names must be exactly `document.pdf` (for a PDF upload) or `0001.png` .. `NNNN.png` (for N images), sizes
and counts are bounded, page numbers must be possible for the limits, warnings have a short lower-case code, an error is a
status from 400 to 599 and a code of `ErrorCode`; anything else, a closed connection or silence past the deadline is `503
unavailable`. A structure comes back as exactly the five fields of `Chemistry` with the right types, a bounded SMILES with no
control character, an InChIKey of the right shape or none, a molfile that is one record ending in `M  END`, and an SVG
that is a drawing and nothing else (only the elements RDKit draws with; no script, event handler, link, style sheet,
entity, doctype or second comment); anything else is also `503 unavailable`, because the api puts the SVG into a page
and the molfile into a download (`sidecar_client.checked_chemistry`). `/readyz` asks the sidecar (`ping`), so the api is not ready without it. The sidecar takes its limits from
the same settings (the same `.env`), enforces its own places (`INGEST_CONCURRENCY`; `503 busy` beyond), cleans its scratch
after every request, and runs as a non-dumpable process with no secret. What this closes and what it does not:
`deploy/README.md`, "What the API review required". `tests/test_sidecar.py` runs it with real sockets and real child
processes.

## Tests and checks

```bash
uv run pytest                 # no Redis, no network: fakeredis, test files built in code
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Most API tests run the ingest code in a thread; `tests/test_isolation.py` runs the real child process
(deadline, memory limit, crash, signals, environment, stderr). P3-11 adds `test_scrub.py` and `worker/test_reporting.py`
(what an error report and a log line may carry), `test_logs.py`, `test_usage.py` and `worker/test_usage.py` (the counter),
`test_sidecar.py` (real sockets and child processes), `worker/test_chem_down.py` (the worker without chem) and
`test_glitchtip_edge.py` (the edge's and the gate's configuration for the error-report path).

## Dependencies

Exact pins in `pyproject.toml`. PDFs are read with `pypdfium2` (Apache-2.0 / BSD-3-Clause), images with
Pillow and `pillow-heif`. `sentry-sdk==2.71.0` (MIT; newest release at least two weeks old on 2026-10-23, with `urllib3`
2.8.0 as its transport) reports errors to GlitchTip.

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
| `segment` | seg is called once per page, except for a job of **several images**. A page above 25 MP or 36 MiB (seg takes 40 MP and 40 MiB; a B2 page is 49 MP) is sent as a smaller copy; the boxes seg returns are scaled back to the page and the crops are **cut from the full-resolution page** (not from seg's answer: those are the copy's pixels, white outside seg's mask). The crops of a page are written to part files as its answer arrives, only the first `MAX_STRUCTURES` of a page, and read back later, so no crop is held in memory. They are put in reading order (page by page, seg's order within a page) and cut at `MAX_STRUCTURES` (20): `structuresFound` is the total, the rest is `structuresDropped`, `truncated` is set and the warning `structures_truncated` added. **Single image that fills the frame (`SINGLE_IMAGE_WHOLE_FRACTION`, default 0.6):** for a job made of exactly one uploaded image (not a PDF, not several images: see D23), when seg finds exactly ONE structure and its box covers at least that fraction of the image's area, the whole image goes to ocsr and cls instead of seg's crop, and the reported bbox is the whole image (`crop-0.png` is a hard link to `page-0.png`, as in the fallback below). A pasted image is usually already a tight crop, and seg's box, grown from its mask, can clip its edge (a methoxy group, a ring edge). Several structures, or one small structure in a big image, keep seg's crops; the numbering and the cap are untouched (one image is one page). **Single-image fallback:** only for a job made of exactly one uploaded image (not a PDF, not several images), and only when seg answered and found nothing: the whole image is the one structure (bbox = the page, `crop-0.png` a hard link to `page-0.png`). seg's answer is checked against what was sent: the echoed page index and image size, every box inside the image. **Several images:** legacy never segments an upload of several images, and neither does the worker. For a job of more than one uploaded image seg is not called at all (no seg slot, no seg ticket): every image is one structure, read whole by ocsr and cls (its crop is a hard link to the image, fitted for the models like any crop, and read only once an ocsr slot is held), numbered in image order, and its box is the whole image (`pageIndex` is the image's place in the upload). The numbering is the same prefix rule and the same cap as for any job: `structuresFound` is the number of images that could be put in their place, the first `MAX_STRUCTURES` (20) are recognised, the rest is `structuresDropped` (legacy read the first 20 and left the others empty), `truncated` is set and the warning `structures_truncated` added; nothing is sent to ocsr or cls for the dropped images. Every image keeps its place, as in legacy (an image it cannot read still gets its slot with an empty SMILES): an image that cannot be read (its box is `[0, 0, 0, 0]`, its size unknown, and it has no `page-N.png` and no crop file: the media endpoint answers `404` for them, the ZIP export leaves its image out), whose crop cannot be written (no crop file), or that ocsr refuses is a result with an empty SMILES, `valid: false` and the warning `recognition_failed` ("Structure N ..."), and the images after it are not renumbered; even when none of the images can be read the job is `done`, not `render_failed`. The others are read as usual. The job is still a "not small" job for scheduling (`SMALL_JOB_MAX_PAGES`). |
| `recognise` | Each kept crop, as soon as its number is known, is read, cut down for the models, and sent to ocsr and cls (together), at most as many crops at once as the replicas of the two services take. The models look at 512 px (ocsr) and 224 px (cls), so a crop above 4 MP or 4096 px on a side is downscaled (and re-encoded until under 19 MiB); the crop file keeps its size. ocsr's SMILES goes through **legacy's fragment filter**: more than four `.`-separated fragments, or a first fragment shorter than all the others together, keeps only the first fragment (warning `fragments_trimmed`; the raw prediction is in the debug log only). |
| `check` | A crop goes through RDKit as soon as it is recognised; CDK and the names are asked for once every crop is through. RDKit (`chemistry.py`): canonical SMILES, validity, full InChIKey, V2000 molfile with 2D coordinates and wedge bonds, an SVG that starts with `<svg`. Then, if `CHEM_URL` is set: the SMILES RDKit refused go to chem's `POST /cdk` in **one batch per job**, then IUPAC names: one `POST /names` per job (20 structures per request); without `CHEM_URL`, `name`, `nameTier` and `nameVerified` are null and CDK is not tried. |

`name`, `nameTier` and `nameVerified` come from chem's `name`, `tier` and `verified` (how the name was checked,
e.g. `opsin`); all three are null together, and chem's `reason` is not stored (a `timeout` reason gives the
`names_unavailable` warning, any other decline is just "no name"). `Result.name_verified` is a field added to
`models.py`.

**CDK.** Legacy sent every SMILES through CDK 2.8, whose parser is more
tolerant than RDKit's. The new stack keeps CDK as a fallback only: the SMILES (DECIMER's own text, at most 2000
printable characters) of the structures RDKit refused go to chem's `POST /cdk` in one batch per job (chunks of
`CHEM_BATCH_SIZE`; per-crop calls would meet chem's gate of one running and four waiting requests, then 503), before
the names. The job's ticket and the replica pool are the ones the names use. For each answer: CDK's V2000 molblock
is accepted when `clean_molfile` (the bounded gate the validate route applies) and RDKit (read without
sanitizing) accept it; the result then has `valid: false`, `validBy: "cdk"`, CDK's molblock (unchanged but for
`clean_molfile`'s line breaks) as `molfile`, RDKit's drawing of it as `svg` (empty if the drawing fails), the
SMILES as DECIMER wrote it, an empty `inchikey`, and **no name** (Orthonym needs RDKit's reading). A decline
(`invalid_smiles`, `layout_failed`, `too_large`) leaves the result as RDKit left it. A failed call, and a
structure CDK ran out of time or broke on (`timeout`, `engine_error`, `engine_unavailable`), leave the structures
invalid and give one `cdk_unavailable` warning per job. `CDK_FALLBACK=false` makes no call at all.

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
  `CONNECT_BACKOFF_MAX_SECONDS` (10 s), until `CONNECT_RETRY_BUDGET_SECONDS` (90 s) have been spent on waiting and
  on attempts that took a while to fail (an address that swallows packets instead of refusing).
  ocsr needs about 25 s to load (longer on a busy box), so a worker that finds it restarting waits for it.
  This is done inside the call rather than by re-queueing the job with arq's `Retry`: a re-queued job would
  redo the render and seg work, show the user a restart, and use up arq's tries.
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
| the `/cdk` batch fails, times out, or answers nonsense; CDK times out or breaks on a structure | those structures stay invalid (`validBy: null`); one warning `cdk_unavailable`. CDK declining a SMILES (`invalid_smiles`, `layout_failed`, `too_large`): invalid, no warning |
| seg 500 / 422 / timeout, or an image that cannot be fitted to seg's limits, on one page | that page is skipped; warning `page_segmentation_failed` ("Page 3 ..."); on every page the job fails: `segmentation_failed` |
| seg or ocsr unreachable or busy for good | the job fails: `service_unavailable` (seg is not asked for a job of several images) |
| a page cannot be rendered | that page is skipped; warning `page_render_failed`; no page at all: the job fails, `render_failed`. Not for several images: each keeps its place as a result that failed to be recognised (`recognition_failed`) |
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
| `CANCEL_POLL_SECONDS` | `2` | A running job is looked at this often for having been deleted by its owner; the run stops within this long. |
| `BUSY_RETRIES`, `BUSY_RETRY_AFTER_DEFAULT_SECONDS`, `BUSY_RETRY_AFTER_MAX_SECONDS` | `3`, `5`, `30` | 503 handling. |
| `CONNECT_RETRY_BUDGET_SECONDS`, `CONNECT_BACKOFF_SECONDS`, `CONNECT_BACKOFF_MAX_SECONDS` | `90`, `0.5`, `10` | Could-not-connect handling: doubling backoff, capped per wait, until the budget is spent. The budget counts the waits **and** the time lost in attempts that failed slowly (a connect that hangs instead of being refused), so it is a bound on the whole. |
| `CHEM_CONNECT_RETRY_BUDGET_SECONDS` | `5` | chem is optional, so a call to it spends this long in all (waiting and failed attempts: a blackholed address costs one attempt, not several) on getting a connection (instead of `CONNECT_RETRY_BUDGET_SECONDS`) before the job goes on without names; once per job (the names are not asked again after the CDK call failed to connect). The worker starts, and runs jobs, with chem down: one `names_unavailable` warning (`tests/worker/test_chem_down.py`); compose waits for chem to have started, not to be healthy. |
| `SENT_RETRIES` | `1` | Retries after the connection broke once the request was sent. |
| `SEG_RESPONSE_LIMIT_MB`, `SMALL_RESPONSE_LIMIT_KB` | `64`, `1024` | The most an answer may be (seg's, the others'). |
| `RENDER_TIMEOUT_SECONDS`, `RENDER_MEMORY_MB` | `90`, `2048` | Deadline and extra address space of one render child. |
| `MODEL_INPUT_MAX_PIXELS`, `MODEL_INPUT_MAX_SIDE`, `MODEL_INPUT_MAX_BYTES` | `4000000`, `4096`, `19922944` | Larger crops are downscaled and re-encoded before ocsr and cls (they take 17.5 MP / 8192 px / 20 MiB and 50 MP / 20 MiB). |
| `SEG_INPUT_MAX_PIXELS`, `SEG_INPUT_MAX_BYTES` | `25000000`, `37748736` | Larger pages are sent to seg as a smaller copy (seg takes 40 MP and 40 MiB). |
| `SINGLE_IMAGE_WHOLE_FRACTION` | `0.6` | Above 0, at most 1. A job of exactly one uploaded image where seg finds exactly one structure covering at least this fraction of the image sends the whole image to ocsr and cls instead of seg's crop (the box reported is the whole image). `1`: only a box over the whole image. |
| `CHEM_BATCH_SIZE` | `20` | Structures per `POST /names` (chem takes at most 20). |
| `SHUTDOWN_WAIT_SECONDS` | `0` | How long arq waits for running jobs when stopped (0: cancel them). A container's stop grace period must exceed it. |
| `CDK_FALLBACK` | `true` | D4 (kept): a SMILES RDKit refuses is tried with CDK through chem (`POST /cdk`, one batch per job); needs `CHEM_URL`. `false`: no call. |

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

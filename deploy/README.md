# deploy/: the new stack's images, Compose project and nginx edge

Everything needed to run the new decimer.ai stack **beside the legacy one** on the production VM
. The project itself has not been started with `up` by this directory's
authors: the commands below are written out for whoever runs them (the orchestrator, later). The pieces added by
P3-11 (GlitchTip with its Postgres, the usage Redis, the ingest sidecar, the nginx locations) were each run in
scratch containers with the compose file's settings, and `python3 -I deploy/check-compose.py` checks the
file's invariants; the sections say what was and was not run.

The legacy stack (`decimer_web-nginx-1` on host port 8080, `decimer_web-app-1`,
`decimer_web-supervisor-1`) is never touched. The new stack is a separate Compose project, `decimer-next`,
with its own networks and volumes, and publishes one port: `127.0.0.1:8090`.

## What is in here

| Path | What |
|---|---|
| `compose.yaml` | The project: redis, `usage` (the usage counter's Redis), seg, cls, ocsr (replicated), chem, `ingest` (the ingest sidecar), api, worker, sweep, edge, `worker-fast` (the fast lane, part of the default start), and GlitchTip with its Postgres (`glitchtip`, `glitchtip-db`) |
| `.env.example` | Every setting a deployment sets (copy to `deploy/.env`, which is never committed) |
| `seg.Dockerfile`, `cls.Dockerfile`, `ocsr.Dockerfile` | TensorFlow 2.15.1 model services, Python 3.11, weights baked in and checked |
| `chem.Dockerfile` | Python 3.12 + Temurin 21 JRE + Orthonym, its two jars and the CDK 2.8 jar (the layout fallback), baked in and checked |
| `api.Dockerfile` | Python 3.12; one image for the API, the arq worker, the sweep, the ingest sidecar and the usage command |
| `web.Dockerfile` | Node 24 build of the React app, then nginx with the app and the edge configuration |
| `*.Dockerfile.dockerignore` | The build context of each image (BuildKit reads these next to the Dockerfile): an allow-list |
| `nginx/` | `nginx.conf`, `edge.conf`, `glitchtip-ui.conf` (GlitchTip's UI, loopback only), `glitchtip-gate.conf` (the whole nginx.conf of the gate in front of GlitchTip for the api and the workers), `security-headers.conf`, `edge-entrypoint.sh`, `check-csp.py`, `beta-front-door.conf.example` |
| `glitchtip-init.py` | Run by the `glitchtip` service before its web server starts: the first account, the organisation, the two projects, "scrub IP addresses" on, and the DSNs printed (see "Observability") |
| `check-compose.py` | `python3 -I deploy/check-compose.py`: the properties of `compose.yaml` a typo could break (the worker does not wait for chem's health, ports, networks, secrets, GlitchTip and the sidecar are isolated), checked on `docker compose config` with dummy secrets; nothing is started |
| `weights/` | `fetch.sh`, `verify.sh`, `SHA256SUMS`; the fetched weights and jars land here (not in git) |
| `patches/` | `<service>-pyproject.patch` for seg, ocsr, cls and chem (opencv headless, pinned build backends) and the script that applies them |
| `.gitignore` | Re-includes `weights/` (the repository's root `.gitignore` ignores every `weights/` directory, which would hide the tracked scripts and checksums) |
| `healthcheck.py`, `worker_healthcheck.py` | Container healthchecks (standard library and redis-py; no curl in the images) |

All builds use the **repository root** as the build context and need BuildKit (Docker 28 and buildx 0.27
are on the VM, so it is the default). The images are named `localhost/decimer-next/<service>:<tag>` and
are never pulled (`pull_policy: never`): there is no registry namespace somebody else could own.

## The choices behind the files

* **Base images, all pinned by tag and digest:** `python:3.11.17-slim-trixie` (seg, cls, ocsr),
  `python:3.12.15-slim-trixie` (api, chem), `eclipse-temurin:21.0.12.1_1-jre-noble` (chem's JRE; the
  service was measured on 21.0.13, the official image's newest 21 is 21.0.12.1; Orthonym needs 11+),
  `node:24.22.1-trixie-slim` (web build), `nginx:1.31.8-alpine3.24` (edge), `redis:8.10.2-alpine3.23`,
  `ghcr.io/astral-sh/uv:0.13.5` (the version of CI and of the `uv_build` pin). The digests are in the
  Dockerfiles and `compose.yaml`. **Trixie, not bookworm:** the wheels are manylinux, so the distribution
  only matters for its support window; bookworm went to LTS in June 2026, trixie has regular security
  updates until 2028. Update tag and digest together.
* **Multi-stage, non-root, no build tools in the final stage.** The builder has `git` (three services
  have git dependencies) and `uv`; the runtime stage has neither. Services run as uid 10001; the edge
  as nginx (101). `uv sync --locked --no-dev --no-editable` from each project's `uv.lock`;
  `--no-editable` matters because `decimer-service` is a path dependency (`../common`) and the source
  tree is not in the final image (checked: it installs as a real package).
* **`patches/` (apply before building):** one patch per service, `apply-pyproject-patches.sh` applies them
  and runs `uv lock` (checked in a scratch copy: only the opencv entries and a `[manifest]` table change in
  the locks; chem installs with it, and the build hook downloads nothing). Services are not edited by this
  task, hence patches.
  * **opencv-python-headless (seg, ocsr).** `opencv-python`'s `cv2` needs libGL, glib, X11 and xcb at
    import time (checked with `ldd`); the headless wheel of the same version needs only libc, libm, libgcc
    and libstdc++. With it the runtime images contain no apt packages, no mesa or X11 libraries, and the
    wheel is 13 MB smaller. cls has no OpenCV. Until the patch is applied, build with `OPENCV_GUI_LIBS=1`
    (in `deploy/.env`, or `--build-arg`): the Dockerfiles then install `libgl1` and `libglib2.0-0`. Either
    way the build imports `cv2` and TensorFlow as a smoke test.
  * **Pinned build backends (seg, cls, chem): `[tool.uv] build-constraint-dependencies`.** The git
    dependencies (decimer-segmentation and decimer-image-classifier with setuptools, Orthonym with
    hatchling and a build hook) are built from source during `uv sync`, in an isolated environment whose
    backend uv resolves fresh from PyPI. That code runs at install time, with network access, and
    `uv.lock` does not pin it. The constraints pin setuptools 84.0.0 (seg, cls; `wheel` is not pinned,
    because a setup.py-only project never requests it under the fallback backend), hatchling 1.32.4 and
    its dependencies (chem), and `uv-build` 0.13.5 (the newest releases that were two weeks old on
    2026-10-22). **Release builds must pass `--no-cache`** (next to the build commands in step 4).
    Checked: uv honors them when it builds the git dependency. The alternative, prebuilding the three
    wheels and hashing them, would take them out of `uv.lock`; the constraints keep the lock the single
    source and are a few lines each. Without the patches the builds still work, with an unpinned backend.
    For chem the Dockerfile also exports `ORTHONYM_SKIP_JAR_PREFETCH=1` (Orthonym's build hook would
    download both jars into a cache during `uv sync`; services/chem sets it for uv, the Dockerfile for
    any other path).
* **Weights and jars are baked in and checked.** `weights/SHA256SUMS` lists every file; `weights/verify.sh`
  fails on a wrong hash, a missing file, or a listed file that is a symlink. For seg, ocsr and chem it runs
  `--strict`: `find . -mindepth 1` over the whole build-context directory must be exactly the listed files
  and the directories above them, so an extra file, an extra or empty directory, a dot-file (a
  `.model_url`) or a symlink fails the build. For cls it checks the installed pip package's `model/`
  directory only (the weights ship inside the package; the rest of the package is not listed). Tested
  with `verify.sh` against flipped bytes, extra files and directories, dot-files, symlinks and missing
  files; not yet with `docker build`. Sources and evidence are in the comments of `SHA256SUMS`: the seg
  file's MD5 equals the one Zenodo publishes, both ocsr zips' MD5 and SHA-256 equal Zenodo's and
  `services/ocsr/README.md`'s, the extracted files equal the legacy archive and the files the ocsr
  service's own download made, and the chem jars' hashes are the ones Orthonym pins (OPSIN, centres) and
  `services/chem/README.md` lists (a download from the GitHub releases verified against them); the third
  chem jar, `cdk-2.8.jar` (CDK 2.8, the fallback of D4), is the file legacy ships, and a fresh https download of
  the `cdk-2.8` release asset of github.com/cdk/cdk has the same SHA-256 and size.
* **ocsr bakes BOTH DECIMER models (owner decision D15).** `import DECIMER` loads the printed and the
  hand-drawn model and would download a missing one at start-up, so both are in the image (about 650 MB).
  The hand-drawn one is never used by the service and costs 1.9 GB of memory and 26 s of start-up: that is
  why ocsr's limit is **6 GB** and its health check waits (**start period 180 s**, probes every 5 s during
  start-up, every 120 s afterwards; start-up is about 50 s). Reopen only if memory gets tight on the VM
  (then make the hand-drawn load lazy upstream in DECIMER).
* **chem (services/chem).** `CHEM_JARS_DIR=/opt/orthonym/jars` holds three jars (OPSIN, centres and
  `cdk-2.8.jar`, all root-owned and read-only for the service user; the service re-checks all three against
  their hashes before it starts and never downloads one); the jars are made world-readable (Orthonym
  writes them 0600) and read as uid 10001 at build time; `ORTHONYM_JVM_XMX=512m` is the heap cap
  (an explicit `-Xmx` wins over `-XX:MaxRAMPercentage`, so the image sets no `JAVA_TOOL_OPTIONS`);
  none of `ORTHONYM_ALLOW_REDUCED`, `ORTHONYM_OPSIN_JAR`, `ORTHONYM_CENTRES_JAR`,
  `ORTHONYM_DISABLE_JPYPE`, `CHEM_ALLOW_JAR_DOWNLOAD` is ever set (the service refuses to start with the
  first four). `JAVA_HOME` points at the copied Temurin JRE. D14 (the Orthonym `valid` tier) is a setting
  of the service's code and changes nothing in the image.
  **CDK: `CDK_FALLBACK` (worker, default `true`).** The worker (both lanes) sends the SMILES that RDKit
  refuses to chem's `POST /cdk`, one batch per job, after the check stage and before the names; the structures CDK
  accepts are shown as "accepted by CDK only". It needs `CHEM_URL` (no chem: no call) and costs at most one more
  request to chem per job, which takes turns with `/names` on chem's one slot. `CDK_FALLBACK=false` in `deploy/.env`
  switches it off; compose passes the variable to `worker` and `worker-fast` (`x-worker-env`) and not to the api
  (the validate route has no CDK step). Contract change for anything that reads the exports: the CSV's `valid`
  column, `true|false` before, is now `true|cdk|false` (`cdk`: RDKit refused the structure and only CDK accepted
  it; its `.mol` and SDF record carry CDK's molblock, the SDF record `VALID` = `cdk`).
* **Permissions are checked as the service user.** Weights and jars are made `a+rX` and the build runs a
  read of every file as uid 10001; the web build is `chmod -R a+rX dist`, copied without `--chmod` (which
  applies to directories too and once made them unenterable for nginx) and read as uid 101.
* **pystow needs a README in its home directory.** `import pystow` writes `$PYSTOW_HOME/README.md` when it
  is missing, which fails on a read-only `/models`; the seg and ocsr images put the file there.
* **Environment:** `PYSTOW_HOME=/models` (seg, ocsr); the thread counts per replica come from `.env` and are
  set by compose (see "Replicas, threads and memory"; the Dockerfiles carry standalone defaults of 4/1 for
  seg and ocsr and 2/1 for cls, which compose overrides); `MALLOC_ARENA_MAX=2` for cls (the RSS growth soak
  note); `OPENCV_IO_MAX_IMAGE_PIXELS=40000000` for seg and ocsr (seg's own default, written out; do not
  raise it).
* **Healthchecks** hit `/readyz` (which runs a real inference) for the model services and the API,
  `/healthz` for the edge, the arq health-check key in Redis for the worker, and nothing for the sweep.
  seg and ocsr probe every 120 s once running (an idle service pays its selftest on each probe) and every
  5 s during start-up (`--start-interval`, Docker 25 or newer; the engine on the VM is 28.4), so
  `depends_on: condition: service_healthy` does not wait a whole interval. The worker waits for seg, cls and
  ocsr to be healthy and for chem only to have *started* (it runs with chem down: names off, one warning).
* **Redis has no persistence** (`--save "" --appendonly no`, 256 MB, `noeviction`). Every key has a TTL of
  at most 6 h, the job files are on a volume with their own sweep, and a Redis that never writes job
  data to disk is the simplest way to keep the one-hour retention promise. The cost: a Redis restart
  loses the queue and all job records (clients see 404 and upload again). Redis restarts only with a
  deploy or a reboot, which interrupt running jobs anyway.
* **Redis has a password (`REDIS_PASSWORD`).** It is in `REDIS_URL` of api, worker and sweep. It reaches Redis
  on stdin (the image's entrypoint is replaced by `redis-server -` with a here-document; argv would be
  readable by every user of the VM in the host's process table) and `redis-cli` through `REDISCLI_AUTH`;
  Redis runs as the `redis` user from the start, with no capabilities. **Untested** (`redis-server -`
  reading its config from stdin is documented Redis behaviour; the shell part was run with a stand-in).
  Use a hex password (`openssl rand -hex 24`): it goes into a URL.
* **Networks.** `public` is the edge alone with the published ports (docker cannot publish from an internal
  network, so it is the one network with a route out; nothing but nginx is on it). `app` (internal) is
  edge + api with a pinned subnet; `backend` (internal) is redis, usage, api, worker, sweep; `models` (internal)
  is the worker and the model services. **The api is not on `models`**: the one model service it calls is
  chem (the IUPAC name of an edited structure, for `validate` and `export`), over `chem-api` (internal), a
  network of the api and chem alone. `ingest` (internal) is the api and the ingest sidecar alone. GlitchTip has
  three internal networks: `glitchtip-sdk` (api, worker, worker-fast and the **gate**), `glitchtip-edge` (the edge,
  the gate and GlitchTip) and `glitchtip-db` (GlitchTip and its Postgres). **GlitchTip itself is not on
  `glitchtip-sdk`**: the api and the workers report to `glitchtip-gate`, an nginx whose whole configuration lets
  one request through, `POST /api/<id>/envelope/`, so a compromised api or worker cannot reach GlitchTip's login,
  API or admin. **No two containers that call each other share more than one network**: with two
  shared networks docker's DNS could answer `api` with the address on the other one, and the api would no longer
  see its proxy on `app` (`check-compose.py` checks it). There is no `egress` network: error reports go to
  GlitchTip on this VM, so nothing needs a route out. The model services, redis, the sidecar, GlitchTip and the
  sweep have no route to the internet, and the api cannot reach seg, cls or ocsr.
* **Trusted proxies, one variable per hop.** The API's peer is the edge on `app`, so the API trusts
  exactly `APP_SUBNET` (derived from the variable that defines the network, not typed twice). The edge
  trusts `PUBLIC_SUBNET` (the host side of the published port and, during the beta, the legacy nginx)
  plus the campus proxies in `TRUSTED_PROXY_CIDR` of `.env`, and gives the API one `X-Forwarded-For`
  entry, the real client. **Proxy addresses, never client networks** (every host in a listed network can
  choose the address its limits are counted against; `/0` is refused by both).
* **Process settings.** Services have `no-new-privileges`, `cap_drop: ALL`, log rotation (5 x 10 MB; by size, not
  by age, which is why the edge's error log is kept apart, see "Error log" below), and
  `oom_score_adj` 300 (500 for the model services and chem) so the kernel sacrifices the new stack before
  legacy. seg, cls, ocsr, chem, redis and the edge have a read-only root file system with a RAM-backed
  `/tmp` (untested: if a service stops with "Read-only file system", add the path to `tmpfs`). The api is
  deliberately writable (starlette spools big uploads to `/tmp`; as RAM that would count against its limit).
  The **ingest and render children** are processes of the api and worker containers: compose can set
  `oom_score_adj` only per container, so making the children the first OOM victims (write 1000 to
  `/proc/self/oom_score_adj` in `ingest_worker` and `render`) is an API-side TODO.
* **`pull_policy: never`, not `build`.** The coordinator asked for `build` on the built services. `build`
  makes every `up` rebuild, which also overwrites the tag that a rollback (`DECIMER_TAG=<old>`, `up`)
  goes back to; `never` gives the protection that matters (nothing is pulled, so no Docker Hub namesake
  can be fetched for a missing image) and rollback keeps working. The cost: build first (`$DC build`),
  because `never` with a missing image is an error, not a build. If you want `build`, change one line per
  service.
* **`IP_HASH_SECRET` reaches the api only:** the worker and sweep blank it (`env_file` would otherwise
  hand it to them). `REDIS_PASSWORD` they need.

### What the API review required (coordinator, 2026-10-22)

1. **The API starts with an exec-form `CMD`** (`["python", "-m", "decimer_api"]`, in `api.Dockerfile` and
   again in `compose.yaml`), so python is PID 1 and no shell or init process holds a second copy of the
   environment: the API makes itself non-dumpable, a wrapper would not be. So there is **no `init: true`
   for the api** (the sweep has it: python as PID 1 would ignore SIGTERM). Accepted consequence: PID 1
   does not reap orphaned grandchildren; the ingest children are waited for by their parent, and
   `pids_limit: 256` bounds a leak.
2. **Exactly one API process:** one `api` container, no `--workers`, no `--scale`, no replicas. The ingest
   gate and the queue throttle are per process.
3. **The ingest sidecar (built, TODO; not yet run in containers).** The API used to parse every upload in
   a child process of its own container: untrusted PDFs and images through pdfium, libheif and Pillow, the most
   exploitable code in the stack, and to read every client molfile in two long-lived RDKit children (`analysis_worker`).
   Those children run as the API's user in the API's container, so a compromised one could **read and write all of
   `DATA_DIR` and `REPORTS_DIR`** (every user's uploads and results, the reports kept for analysis, planted files
   and symlinks in other jobs' folders), open connections on the **`backend`** network (Redis behind its password,
   chem over `chem-api`), and use the API's identity towards the edge. The non-dumpable flag and the minimal child
   environment close the `/proc` door and nothing else. **The `ingest` service closes the rest.** It runs
   `python -m decimer_api.sidecar` from `api.Dockerfile`: the same children, started by a small TCP server in a
   container with
   * **no job data**: the api sends the uploads over the connection, the sidecar keeps one request at a time in a
     private scratch volume (`ingest-scratch`, emptied after each request) and sends the normalised files back; the
     api writes them into `DATA_DIR/<job id>/input/` itself, after checking names, sizes, page counts and warnings
     (`sidecar_client.py`; a malformed or oversized answer is `503 unavailable`, nothing is written outside `input/`);
   * **no route** to Redis, chem, the edge, GlitchTip or the internet (its one network, `ingest`, holds the api);
   * **no secret** (compose blanks `REDIS_PASSWORD`, `IP_HASH_SECRET` and `SENTRY_DSN` in it, and the GlitchTip
     secrets) and a non-dumpable process, a read-only root file system, no capabilities;
   * the memory of the parsers: 2 x `INGEST_MEMORY_MB` (1536) + 2 molfile children x about 450 MB + the server = about
     4.0 GB at their limits, so 4352m; the api itself shrinks to `API_MEM_LIMIT` (1g).
   A compromised child can still see **the other request in flight** (same user, same scratch volume): at most
   `INGEST_CONCURRENCY` - 1 uploads, for seconds. That is the design's price: one upload instead of all of `DATA_DIR`.
   The api is **not ready while the sidecar does not answer** (`/readyz` asks it, which makes the api container
   unhealthy), and waits for it to be healthy at start. `INGEST_SIDECAR_URL=` (empty) and `API_MEM_LIMIT=4608m`
   switch back to children of the api (development); the code path is the same `IngestRunner` / `Analysis` seam.
   **Tested** (api tests, real sockets and real child processes on 127.0.0.1; `api/tests/test_sidecar.py`): the
   sidecar returns the same files as the in-process path, every parser error crosses as the same API error, the api
   rejects every kind of answer that cannot be true (about 40 hostile answers and protocol failures are tested;
   removing any one of the checks fails a test), the sidecar rejects bad announcements and cleans its scratch.
   **Not tested here: the containers** (the read-only root and the scratch volume's ownership, the `ingest` network's
   DNS, the health check): `up` was not run. The first start on the VM should check `$DC ps` (ingest healthy before
   api), an upload end to end, and that the sidecar has no route to Redis or GlitchTip (step 6 lists the commands).
4. **nginx:** `proxy_request_buffering on` for `/api/` (uploads are buffered to the `edge-cache` volume,
   disk, not RAM, and removed when the edge starts), `disable_symlinks on;` on the internal
   `/_protected/jobs/` location, and `proxy_read_timeout` taken from the same
   `PROXY_READ_TIMEOUT_SECONDS` the API validates against (`edge-entrypoint.sh` generates the line; the API
   refuses to start when INGEST_WAIT_SECONDS + the child deadline + 5 s reaches it, so the pair cannot
   drift apart). The upload limit comes from `MAX_UPLOAD_MB` the same way (`client_max_body_size` is one MB
   more; the 413 message names the limit).
5. **Absolute `/api/` URLs:** the API returns `/api/jobs/<id>/media/crop-0.png` as `cropUrl` and
   `Location: /api/jobs/<id>`. Under `/beta/` those still point at the site root, so **the front door must
   route `/api/` to the new stack as well as `/beta/`** (see "Before /beta"). The legacy site has no
   `/api/` routes that matter.

### Dependencies on other work

* **The worker downscales pages for seg (done, TODO).** seg refuses images above 40 MP (422) and its memory
  limit is sized for that: the library keeps one full-page mask per detection. The API admits pages up to 50 MP,
  so the worker sends seg a smaller copy of any page above `SEG_INPUT_MAX_PIXELS` (25 MP) or `SEG_INPUT_MAX_BYTES`
  (36 MiB encoded) (`worker/files.py` `fit_png`), scales seg's boxes back and cuts the crops from the full page.
  That is safe for the reason it is for ocsr (the model looks at a thumbnail). seg's own cap stays as a backstop and
  nothing should raise it before its memory limit is re-measured.
* The worker also downscales crops above 16 MP for ocsr and cls (`MODEL_INPUT_MAX_PIXELS`).

### The edge, briefly

`nginx/edge.conf` serves the app (`index.html` `no-cache`; `/assets/` immutable for a year; `.wasm` as
`application/wasm`, in `gzip_types`, precompressed at build time for `gzip_static`: 11.8 MB to 3.8 MB;
brotli is not in the official nginx image), proxies `/api/` to `api:8000` (per-IP `limit_req` 10 r/s burst 60;
uploads 12 per minute burst 5 and 4 connections; `/readyz` rate-limited too; 429s and nginx's own
413/502/503/504 in the API's JSON error shape), serves job media from the read-only job volume through the
internal location, and sends the CSP from `security-headers.conf` (identical to `web/vite.config.ts`:
`python3 -I deploy/nginx/check-csp.py`) with `nosniff`, `X-Frame-Options`, `Referrer-Policy: no-referrer`,
`Permissions-Policy` and COOP/CORP. Legacy links `/about`, `/privacy_policy`, `/impressum` redirect to the
hash routes. HSTS is left to the TLS terminator (commented line in the snippet).

* **Privacy in the logs (P5-06; the privacy page says all of this):** the access log (stdout, docker's json-file
  driver) has no client address, no user agent, no referrer, no query string, and job ids replaced by `:id`
  (they are bearer tokens). **nginx's error log is the one place the edge writes an address.** Any server error
  can be logged there (failed proxying, an upload over the size limit, a media file that cannot be opened, ...),
  and a line holds **the client address, the request line (so a job id and any query string), the host and
  the referrer** (`client: ..., request: "GET /api/jobs/<id>?x HTTP/1.1", host: "...", referrer: "..."`). It is
  kept **seven days at most**, by the mechanism below. Rate-limit refusals are logged below its level and
  show as 429s in the access log.
* **Error log: seven days, not "until docker rotates it".** docker's `json-file` driver (5 x 10 MB) bounds
  the size of a log, not its age, so the error log does not go through it. `nginx.conf` writes it to
  `/var/log/nginx/error.log`, on the `edge-logs` volume, and `nginx/edge-logs.sh` (in the image as
  `/usr/local/bin/edge-logs.sh`) keeps the promise:
  * A day's lines end up in `error.log.YYYY-MM-DD` (UTC; the first run after midnight renames the file and
    sends nginx `USR1` to open a new one), and a file is deleted when its day is a week behind (at the last
    run before its oldest line would be seven days old). So no line outlives seven days and none goes before
    about six. Only files named `error.log.<date>[.n]` are ever deleted. A clock that goes back cannot make a
    file outlive its week: a file dated after today is dated today.
  * **Who runs it.** `edge-entrypoint.sh` runs `edge-logs.sh rotate` and `edge-logs.sh check` before nginx starts
    (a start also deletes whatever a stopped edge left behind past its week, before anything is served; an
    `error.log` that is a symlink to stderr, as a volume seeded by the stock nginx image would have it, stops
    the start), and `edge-logs.sh loop` beside nginx (every 10 minutes). **The image's HEALTHCHECK runs
    `edge-logs.sh enforce` every 15 seconds: `rotate`, then `check`.** So the seven days are enforced by Docker's
    health-check cadence even if the loop died, and the edge is `unhealthy` only for what rotating cannot mend
    (not a plain file, a volume that cannot be written, ...). `rotate` takes a lock (the directory
    `.error.log.lock`; a run that finds it taken leaves the work to the holder, a lock a minute old is broken),
    so the loop, the health check and a host-side run never rotate at once. If healthchecks are ever switched
    off (`healthcheck: disable`), the loop is the only enforcer.
  * **A stopped edge prunes nothing.** After `$DC down` or `stop`, and for the pause before a cutover, the
    `edge-logs` volume ages: its lines are deleted at the next start, not at seven days. The privacy page's
    "no line is kept longer than seven days" holds while the edge runs. For a stop of more than a few days,
    prune from the host (a throwaway container on the volume; it runs the same script, as the volume's owner):

    ```sh
    docker run --rm --user 101:101 --read-only -e EDGE_LOG_DIR=/l -v decimer-next_edge-logs:/l \
      --entrypoint /usr/local/bin/edge-logs.sh localhost/decimer-next/edge:local rotate
    ```

    (Or delete the volume: `docker volume rm decimer-next_edge-logs`, with the edge stopped.) Put it in the
    runbook next to `$DC down`.
  * **Verify:** `docker exec <edge> ls -la /var/log/nginx` shows `error.log`, at most six dated files and
    `.error.log.began` (the day the current file started). `docker exec <edge> edge-logs.sh check` exits 0 when
    the retention is being kept and says why when not. The script is tested, under dash and BusyBox, by
    `api/tests/test_edge_logs.py` (including a simulation of eleven days of lines, deleting every line older
    than seven days and none younger than about six, a dead loop, concurrent runs and a clock that goes
    back), and `api/tests/test_privacy_promises.py` holds the page, the nginx files and the settings together.
  * **Reading the error log** (it is no longer in `docker logs`):
    `docker exec <edge> tail -n 50 /var/log/nginx/error.log`. Do not copy it anywhere that keeps it longer
    than seven days (and do not add an `error_log stderr`: that would put the addresses back into docker's log).
  * If the retention ever changes, change `EDGE_LOG_KEEP_DAYS` (the script's default, 7) and the privacy text together.
  * What the university proxy and the legacy front door log is not governed by this: the privacy page keeps an
    open question for it (ask the network team).
* **DNS:** the edge resolves `api` per request through docker's resolver, so recreating the api container
  does not leave nginx holding a stale address.
* **Leftover uploads:** the edge empties `/var/cache/nginx` when it starts, so the buffered body of a request
  that was cut off by a crash does not outlive the process.

## Replicas, threads and memory

Legacy runs 6 segmentation, 3 OCSR and 3 classifier socket servers in one container (about 22 GB). The new
stack must never be slower or more blocking than that, so the model services are **replicated** (counts
from `deploy/.env`, chem always 1), each replica with a **fixed thread count**, so that all replicas together
match the 8 cores of the VM. Two layouts; the counts are configuration only.

| | seg | ocsr | cls | chem |
|---|---|---|---|---|
| **Beside legacy** (default: local testing, the beta) | 2 | 2 | 2 | 1 |
| **After cutover** (legacy removed; legacy's own layout) | 6 | 3 | 3 | 1 |

### How replicas are made, and what the worker may rely on

`compose.yaml` uses `deploy.replicas: ${SEG_REPLICAS:-2}` (and `OCSR_`, `CLS_`). Checked: `config` renders it
identically with Compose v2.3.3 and v2.39.2 (including the 6/3/3 values from an env file), and the v2.3.3 binary
carries the message "`scale` is deprecated. Use the `deploy.replicas` element", i.e. `deploy.replicas` is the form
it honours. **`up` itself was not run** (rules for this task): on the first start, check that `$DC ps` shows the
configured number of containers per service (`decimer-next-seg-1` ... ) and run the DNS check below. A one-off
override is `$DC up -d --scale seg=3`. Scaled services have no `container_name` (a second replica would be
impossible) and no published port.

**The contract for the worker (TODO, implemented in `api/src/decimer_api/worker/replicas.py`), and nothing
more:**

```
SEG_URL=http://seg:8001    OCSR_URL=http://ocsr:8002    CLS_URL=http://cls:8003    CHEM_URL=http://chem:8004
```

* One host name per service and one fixed port per service; **all replicas answer on that port**. On the
  `models` network Docker's embedded DNS answers a lookup of `seg` with the address of every running replica
  (the order rotates), so the worker resolves **all A records** of the name (AAAA only if there are no A
  records, so a dual-stack network does not count a replica twice), looks again every `REPLICA_REFRESH_SECONDS`
  (10 s) and at once after a connection failed (a replica that restarts comes back with another address; one
  that is down drops out of the answer or refuses connections), and balances itself: each call goes to the
  **least-busy healthy replica** (ties in turns), at most `<SERVICE>_CONCURRENCY` (1) calls per replica at a
  time (seg, ocsr, cls and chem each run one inference at a time and queue a few more). A replica that
  refuses or drops a connection is skipped for `REPLICA_COOLDOWN_SECONDS` (10 s) and the call goes to another
  one at once; a replica that leaves DNS is dropped when it is idle; a name that does not resolve, an IP
  address and `localhost` are one replica (development). The existing retry rules (busy 503, connect budget,
  one retry after a broken connection) hold per call, over all replicas.
* **No per-replica names or addresses**: container names (`decimer-next-seg-3`) and IPs are not part of the
  contract.
* `depends_on: condition: service_healthy` makes the worker wait for **every** replica of seg, cls and ocsr; chem is
  `service_started` only, the worker does not need it (after a replica is recreated later, only the worker's own
  retry logic covers the gap).
* Check on the VM: `$DC exec worker python -c "import socket; print(sorted({a[4][0] for a in
  socket.getaddrinfo('seg', 8001, proto=socket.IPPROTO_TCP)}))"` lists one address per seg replica (the
  same for `ocsr` 8002 and `cls` 8003). The worker logs `replica <address> joined` for each address it
  finds (`docker logs`, level INFO), and `replica <address> left DNS` when one goes.

### Threads and CPU: the calculation

Per replica (compose passes them; `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` and, for seg,
`NUMBA_NUM_THREADS` are held to the same count, so that numpy and OpenCV's bundled BLAS do not start one thread
per core in every replica; TensorFlow inter-op is 1 everywhere):

| | seg | ocsr | cls |
|---|---|---|---|
| TF intra-op threads, **beside legacy** | 2 | 2 | 1 |
| TF intra-op threads, **after cutover** | 1 | 2 | 1 |
| `cpus` limit per replica (cap + half a core for TensorFlow's other threads; ocsr: the cap) | 2.5 / 1.5 | 2 | 1.5 |

**Inputs (measured, from the service READMEs and D17/F-07):** seg 3.6 s per page at all cores and 6 s at 2+1
threads (8-core dev box); ocsr median 3.1-3.5 s per crop at 2+1 threads on the shared dev box, and legacy 2.6 s per
crop on average at full CPU (52 s for 20 golden crops); cls about 0.1 s per crop at 2+1; the golden paper (10
pages) took legacy 95.3 s.

**Model (an estimate, not a measurement):** two points per service fit Amdahl's law, T(t) = serial + parallel/t:
seg serial 2.8 s + parallel 6.4 s, so T(1) 9.2 s, T(2) 6.0 s, T(4) 4.4 s, T(8) 3.6 s; ocsr serial 2.4 s + parallel
1.9 s, so T(1) 4.2 s, T(2) 3.3 s, T(8) 2.6 s (rough: the two points come from different machines' conditions; ocsr
decodes token by token, so extra threads buy little); cls insensitive. CPU work of one maximum job (10 pages, 20
structures): seg 92 core-seconds, ocsr 85, cls 2, render + RDKit + chem + api about 12: **191 core-seconds, 48% seg,
44% ocsr**. On 8 cores that is at best 24 s per job, about 150 maximum jobs an hour, whatever the layout.

| Layout | caps (replicas x threads) | sum of caps | bottleneck | cores busy at saturation | lone 10-page job, upper bound (stages added up) |
|---|---|---|---|---|---|
| Beside legacy 2/2/2 | seg 2x2, ocsr 2x2, cls 2x1 | 10 | ocsr, 109 jobs/h | seg 2.8 + ocsr 2.6 + other 0.5 = **5.8** | about 70 s |
| After cutover 6/3/3 | seg 6x1, ocsr 3x2, cls 3x1 | 15 | ocsr, 164 jobs/h | seg 4.2 + ocsr 3.8 + other 0.6 = **8.7** | about 46 s |

The lone-job column is an **upper bound for typical crops** (one slow non-structure crop can exceed it, as the measured 98.3 s did): it adds the stages up, as if render, segment, recognise and check ran one
after the other, and spreads a stage's items over the replicas in rounds. Since P3-04c the stages of a job
overlap (a page goes to seg as soon as it is rendered, a crop goes to ocsr and cls as soon as its number is known), so
a lone job takes less than the sum: roughly the longer of the segment and recognise chains plus the time until the
first structures reach ocsr, instead of both one after the other. How much less is not modelled here; the real number
comes from the golden-paper rerun after P3-04c. Render about 0.5 s a page (an estimate; one at a time
beside legacy, two after cutover), seg 10 pages, ocsr 20 crops (cls runs beside ocsr and takes 0.1 s a crop), check
(RDKit and chem) about 2 s: beside legacy 5 + 5 rounds x T(2) 6.0 s = 30 + 10 rounds x T(2) 3.3 s = 33 + 2 = **70 s**;
after cutover 2.5 + 2 rounds x T(1) 9.2 s = 18.4 + 7 rounds x T(2) 3.3 s = 23.1 + 2 = **46 s**; beside legacy with 3
threads each for seg and ocsr: 5 + 5 x 4.9 + 10 x 3.0 + 2 = **62 s**. (The first full-stack run of the golden paper, 2/2/2
and the stages in sequence, took 98.3 s, above the 70 s: render 8.1 s, segment 26.2 s, recognise 59.5 s, check 3.0 s;
one crop that is not a structure kept ocsr busy for about 46 s, which the model, built on typical crops, does not
contain.) The worker asks for as many calls at once as a stage has slots (replicas x
`<SERVICE>_CONCURRENCY`), looked up each time a page or crop is about to start, so a lone job fills every replica of a
stage (before P3-04b the concurrency had to be set to the replica counts to get these figures, and with 1 a lone job
would have taken about 130 s beside legacy).

How to read it:

* **After cutover the stack fills the 8 cores** (8.7 asked for, so it is CPU-bound and the ocsr stage runs a little
  below its 164 jobs/h). The sum of caps cannot be 8 there: 12 replicas at one thread each already make 12. What
  matches the cores at saturation is the *busy* sum: with many jobs running, the replicas of different stages are
  busy in proportion to their share of the work, whatever the order of the stages inside one job. A lone job is
  different now that its stages overlap: its seg and ocsr replicas are busy at the same time, so it can use up to the
  sum of caps for a while (10 threads beside legacy), more than the busy sum; `cpus` and `cpu_shares` bound that.
  seg with 2 threads instead of 1 would not raise throughput (ocsr is the bottleneck; 12 s vs 18 s for the seg stage
  of a lone job, 6 more core-slots held); ocsr with 1 thread would drop to 128 jobs/h and about 52 s per lone job
  (7 rounds x T(1) 4.2 s, stages added up).
* **Beside legacy the stack takes about 6 of the 8 cores on purpose**: the legacy site is still serving users and
  keeps the other 2 (its own socket servers are idle most of the time, and then the new stack may use more). For a
  test while legacy is idle, `SEG_TF_INTRAOP_THREADS=3`, `OCSR_TF_INTRAOP_THREADS=3` (and `SEG_CPUS=3.5`,
  `OCSR_CPUS=3`) take it to about 6.4 busy cores and about 62 s for a lone job.
  **When both want the CPU, legacy gets the larger share:** every container of the new stack has `cpu_shares: 512`
  (the `x-service` anchor in `compose.yaml`), half of the default 1024 that legacy's container has. The weight is
  per container, so it shifts the split and does not reserve anything: with k new containers busy at once, legacy
  gets 1024 / (1024 + 512 k) of what they fight over (50% at k = 2, against 33% with equal weights). The thread
  counts and `cpus` caps are what bound the new stack.
* **Against legacy:** a lone 10-page document is estimated at no more than about 70 s beside legacy and about 46 s
  after cutover (upper bounds, see above), against the 95.3 s legacy needed for the golden paper (which also
  includes legacy's per-call JVM and PHP time). Several jobs at once keep every replica fed and interleave their
  crops: a lone job fills the replicas, and when more jobs run, small ones go first. Because a large job now
  has ocsr calls running while it is still segmenting its later pages, a pasted image that arrives then may wait for
  one running ocsr call of that job (at most one per replica, 3 s for a structure and up to 55 s for an image that is
  not one; no call is interrupted): the bound D18 already names, reached a little more often.
* `cpus` is a ceiling per replica (CFS quota), not a reservation: the model services' limits sum to 14 beside
  legacy (chem included; 21.5 after cutover), and the api (2) and worker (2) come on top, so they exceed the 8
  cores by design. If the legacy site slows down during the beta, lower `SEG_TF_INTRAOP_THREADS`/`SEG_CPUS` and
  the ocsr pair first.
* **To verify**: run the golden paper (now with the stages overlapping) and a stress case with these
  settings and record per-stage time and `docker stats`; the two ocsr points are the weakest part of the model.

### Memory

Per replica (limits are per container; resident from the READMEs): seg 3 GB / about 2 GB, ocsr 6 GB / about 4.8 GB
(both DECIMER models, D15), cls 2.5 GB / about 1 GB, chem 1.5 GB / about 0.4 GB. Everything in GiB, whole stack:

| Service | Beside legacy 2/2/2: limit / resident | After cutover 6/3/3: limit / resident |
|---|---|---|
| seg | 2 x 3 = 6.0 / 4.0 | 6 x 3 = 18.0 / 12.0 |
| ocsr | 2 x 6 = 12.0 / 9.6 | 3 x 6 = 18.0 / 14.4 |
| cls | 2 x 2.5 = 5.0 / 2.0 | 3 x 2.5 = 7.5 / 3.0 |
| chem | 1.5 / 0.4 | 1.5 / 0.4 |
| **models** | **24.5 / 16.0** | **45.0 / 29.8** |
| api (`API_MEM_LIMIT`: 1 g; itself about 100 MB, its spooled bodies and its routes) | 1.0 / 0.15 | 1.0 / 0.15 |
| ingest sidecar (`INGEST_MEM_LIMIT`: 4352m = 2 ingest children x 1.5 + 2 molfile children x 0.45 + the server; resident about 0.2) | 4.25 / 0.2 | 4.25 / 0.2 |
| worker (`WORKER_MEM_LIMIT`: 2 g, after cutover 3 g with 8 jobs and 2 renders) | 2.0 / 0.5 | 3.0 / 1.0 |
| worker-fast (`WORKER_FAST_MEM_LIMIT`: 1 g; only small jobs, 2 at a time) | 1.0 / 0.3 | 1.0 / 0.3 |
| redis, sweep, edge | 0.8 / 0.2 | 0.8 / 0.2 |
| usage (the counter's Redis: 96m) | 0.1 / 0.01 | 0.1 / 0.01 |
| GlitchTip + its Postgres + the gate (384m + 160m + 32m, D21's "about 0.5 GB"; measured 169 + 74 MiB after 100 events, the gate 2.4 MiB) | 0.5 / 0.25 | 0.5 / 0.25 |
| **whole stack** | **34.2 / 17.6** | **55.7 / 31.9** |
| plus what the VM already uses (legacy about 22.2 until cutover, the rest of the host about 2.6) | 59.2 / 42.6 of 62.8 | legacy gone: 58.3 / 34.5 of 62.8 |

* **Beside legacy the new stack is about 17.6 GB resident**: the models alone 16.0 (seg 4.0, ocsr 9.6, cls 2.0, chem
  0.4), the whole stack 17.6. D18 first said "about 17 GB of models", about 1 GB above the models' 16.0; D18 now
  says 16. The resident figures come from the service READMEs; the api, sidecar, worker, redis and usage rows are
  estimates, GlitchTip's were measured (2026-10-23, scratch containers with the compose settings: 125 MiB idle and
  169 MiB after 100 events for GlitchTip, 53 and 74 MiB for Postgres). **The ceilings are higher** (34.2 GB: ocsr's
  6 GB limit x 2 is most of it). The 62.8 GiB VM already uses about 25 GiB besides the new stack (legacy about 22.2,
  the rest of the host about 2.6), so the ceilings together make 34.2 + 25.0 = 59.2 GiB: **3.6 GiB of headroom** for
  the kernel and page cache if every container were at its limit at the same moment (it was 5 before P3-11: the
  ingest sidecar moves the parsers' 3.9 GiB out of the api's 4.5 and adds the 4.25 GiB limit of its own, +0.75,
  GlitchTip with its Postgres +0.53, the usage Redis +0.09). That never happens (resident is 42.6 GiB), but it is
  the number to watch. If memory gets tight, `OCSR_REPLICAS=1` takes 6 GB off the ceiling and 4.8 GB off the
  resident total, running without `worker-fast` (`$DC up -d --scale worker-fast=0`, safe: see "Two queues") takes
  1 GB, and `INGEST_MEM_LIMIT` goes with `INGEST_CONCURRENCY` and `INGEST_MEMORY_MB` (one ingest child: 2.8 GB).
* **After cutover** about 32 GB resident (29.8 GB of it models, D18's "about 30 GB") and
  a 55.7 GB ceiling; with the host's 2.6 GiB that is 58.3 of 62.8 GiB, since legacy is gone. The limits are
  ceilings: a container over its limit is OOM-killed and restarted, never swapped (`memswap_limit` equals
  `mem_limit`).
* **The worker's pages in memory.** A page is read, decoded and held as a PNG (2-4 MB for an A4 page at 300
  DPI, up to 36 MiB for a 50 MP page) only once it has a seg slot, and a crop only once it has an ocsr slot, so
  what the worker holds is bounded by the slots, not by the number of waiting jobs: at most 2 x 36 MiB = 72 MiB of
  pages beside legacy and 6 x 36 MiB = 216 MiB after cutover per worker process (the fast worker has its own),
  and no more decoding threads than that.
* Images are several GB each for the three TensorFlow services (estimate, not built), shared by all replicas of
  a service; disk is no concern. 12 replicas starting at once after a reboot load their models together (ocsr
  about 50 s, CPU-heavy): expect a couple of minutes until `$DC ps` shows everything healthy.

### The worker's settings (.env)

| Variable | Beside legacy | After cutover | Meaning |
|---|---|---|---|
| `SEG_CONCURRENCY`, `OCSR_CONCURRENCY`, `CLS_CONCURRENCY` | 1 each | 1 each | **Per replica**: calls in flight to one replica at a time, over all jobs of the worker; the worker finds the replicas through DNS and multiplies (a service with 3 replicas takes 3 calls). Each replica runs one inference at a time and queues a few more, so 1 keeps it fed. The same in both layouts: they do **not** follow the replica counts (before P3-04b they were service totals and had to) |
| `CHEM_CONCURRENCY` | 1 | 1 | chem has one replica |
| `WORKER_MAX_JOBS` | 4 | 8 | Jobs running at once, about twice the ocsr slots, so that several jobs' crops interleave on the replicas, a small job's calls go first and the replicas are kept fed while other jobs render and check. With the fast lane off it is also how many big PDFs can run before a small job waits for a place |
| `REPLICA_REFRESH_SECONDS`, `REPLICA_COOLDOWN_SECONDS` | 10, 10 | 10, 10 | How often the replicas are looked up again (and at once after a connection failed), and how long a replica that refused a connection is skipped |
| `SMALL_FIRST_BURST` | 4 | 4 | Calls of small jobs go first, but after this many in a row a waiting call of a large job gets the next free slot (a stream of pasted images cannot starve a PDF) |
| `SMALL_JOB_MAX_PAGES` | 1 | 1 | A job of at most this many processed pages is small (a pasted image, one image, a one-page PDF); also used by the api to choose the queue |
| `RENDER_CONCURRENCY` | 1 | 2 | PDF pages rendered at once (a 50 MP page is about 0.5 GB while it renders) |
| `WORKER_MEM_LIMIT`, `WORKER_CPUS` | 2g, 2 | 3g, 2 | The container's limits |

### Two queues and fair dispatching, so that small jobs never wait (TODO, D18)

Two mechanisms, one inside the worker and one around it.

**Inside a worker (always on).** Several jobs run at once and share the replicas. When every replica of a
service is busy, the waiting calls go to the next free slot in this order: a call of a **small job** (a
pasted image, a single image upload, a PDF of at most `SMALL_JOB_MAX_PAGES` = 1 processed page) first;
among jobs of one size the one served longest ago, so two big jobs interleave their crops; and after
`SMALL_FIRST_BURST` (4) small calls in a row a waiting call of a large job gets the slot, so that a stream of
small jobs cannot starve a big one (it gets at least one slot in five). A small job therefore does not queue
behind the calls of big PDFs; it cannot interrupt a call that is already running on a replica (ocsr: about 3 s,
up to 55 s for an image that is not a structure), and it takes its turn behind other small jobs.

**Around the workers: the fast lane.** `worker-fast` is a second worker service on its own arq queue for small
jobs, so that a small job also does not wait for a *place* (`WORKER_MAX_JOBS`) behind 10-page PDFs. It is **part
of the default start** (1 GB limit, in the memory table above). Queue names: `WORKER_QUEUE_NAME` (default
`arq:queue`, arq's own; a worker serves the queue it names, and `worker-fast` gets `WORKER_FAST_QUEUE_NAME`) and
`WORKER_FAST_QUEUE_NAME` (default `decimer:fast`); `worker-fast` has `WORKER_FAST_MAX_JOBS` 2, 1 GB, 1 CPU, and
`worker_healthcheck.py` checks `<queue name>:health-check`, the key arq's worker keeps for its queue.

What this guarantees, and what it does not. A small job never waits for a worker place or in the queue of the big
jobs. It can still wait for: the call already running on a replica (not interrupted: ocsr takes 3 s, up to 55 s
for an image that is not a structure), including a call of the *other* worker process (a replica serves one call
at a time for both); the calls of other small jobs ahead of it in the turn order; and the one slot in
`SMALL_FIRST_BURST + 1` (5) that goes to a large call while one is waiting.

**Running without `worker-fast` is safe.** (`$DC up -d --scale worker-fast=0` or `$DC stop worker-fast`; `up`
was not run for this document, so check which works on the VM's Compose.) The api puts a small job on the fast
queue only while a fast worker is alive, i.e. while `<WORKER_FAST_QUEUE_NAME>:health-check` exists in Redis (arq
sets it every minute with a lifetime of 61 s and deletes it on a clean stop; the api looks at most every 5 s).
Without it, small jobs go on the default queue and the default worker runs them (their calls to the replicas
still go first). So there is no switch to keep in step with the container, and no queue that nobody serves. If a
fast worker dies, the default worker (which looks every `FAST_LANE_RESCUE_SECONDS`, 30) moves the jobs still
**waiting** on its queue to the default queue once the key is gone: about 90 s at most. A job that was already
**running** on the dead fast worker is moved too, but it is started again only when arq's in-progress key for it
expires, `job_timeout + 10 s` after the run began: about 18 minutes with the defaults (and its record expires
20 minutes after its last save, so such a job may be gone by then). The queue position a client sees counts both
lanes together (it is the job's place in the arrival order, not in one arq queue).

## Observability: GlitchTip, JSON logs, usage counts (TODO, D21)

**Error reports** go to a GlitchTip on this VM through the standard Sentry SDKs, so no report leaves Jena. Nothing
here was run in containers together (`up` was not run), but the pieces were run for real on 2026-10-23: GlitchTip
6.2.6 and Postgres 18.4 in scratch containers with exactly the compose settings (hardening flags, memory limits,
`VALKEY_URL=` empty, embedded worker), the real Python SDK sending scrubbed events to it (stored as
`ValueError: cannot parse [smiles] from [ip] in [file]`), an nginx container with the edge's real configuration in
front of it (POST of an envelope to the web project: 200; to another project: 404; GET: 403; gzip: 415; 70 KB: 413; a
POST that forges nine IP-naming headers is stored with no address, while the same request straight to GlitchTip is
stored as `141.35.7.0`; the UI listener's login with a matching Origin: 200, with another: 403; a CORS preflight from
another origin gets no `Access-Control-Allow-Origin`), the gate in a read-only uid-101 container (only the envelope
POST passes: `/`, the login and the API are 404), and the init script on a fresh and on a half-initialised database
(the account exists before the web server listens; a failing init leaves the container stopped, not serving).

| Who | Library | DSN setting | Goes to |
|---|---|---|---|
| api, worker, worker-fast | `sentry-sdk` 2.71.0 (FastAPI/Starlette integrations in the api, arq in the worker, logging at ERROR) | `SENTRY_DSN=http://<key>@glitchtip-gate:8000/<project id>` in `deploy/.env` | the `glitchtip-gate` over `glitchtip-sdk`, which passes the envelope endpoint to GlitchTip |
| web app | `@sentry/browser` 11.6.0, loaded as a separate chunk only when a DSN is built in | `VITE_SENTRY_DSN=https://<key>@<site host>/glitchtip/<project id>` (build argument of the edge image) | the site's own origin: the edge passes `POST /glitchtip/api/<id>/envelope/` to GlitchTip over `glitchtip-edge`, so the CSP's `connect-src 'self'` is unchanged |

Both are **off unless the DSN is set**. The web app refuses a DSN whose origin is not the page's (it would be blocked by the CSP; it
logs a warning and starts without reporting).

**What a report carries.** The scrubbers (`api/src/decimer_api/scrub.py`, `web/src/observability/scrub.ts`; tests
prove each with a crafted event holding a SMILES, a molfile, an IP address and a file name in every place an SDK looks,
and with the real SDK and a recording transport) are an **allowlist**: an event is rebuilt from the exception types, the
stack as function names and line numbers, the SDK and runtime versions, a few tags, and the method and path of the
request. Everything else is dropped: request bodies, query strings, cookies, every header but `Content-Type` and
`Content-Length`, the user and its IP address, `extra`, breadcrumbs (switched off at the source too), the local variables
of every stack frame, source lines, the host name. Free text that is kept (messages, exception values, tags) has
molfiles, file names, job ids, IP addresses and SMILES strings replaced (`[molfile removed]`, `[file]`, `:id`, `[ip]`,
`[smiles]`). That part is a pattern match, best effort, tested with the probes of two reviews: a SMILES glued to
`!?&`, to brackets of any kind (`(CCO)`, `{CCO}`, `[SMILES:CCO]`), to backticks or typographic quotes, joined with `|`,
behind a name of any length (`id=CCO`, `x:CCO`), inside a path or URL (`/names/CCO`), in a query string (cut, with or
without a scheme), several in one token; file names with any of 30 extensions, also quoted with spaces in six kinds of
quotes. **What it does not recognise, by design:** a file name with no extension, an unquoted file name with spaces (the
words before the extension stay), and a carbon-free SMILES of four capital letters or fewer (`NN`, `OO`, `ONNO`: they
are also ordinary words, and INFO is one). The code quotes the names it shows (`uploads.label`) and the API errors that
carry them are answers, not reports. The SDKs also run without sessions, tracing, metrics, logs and client reports,
and `StdlibIntegration` is not used (it records every subprocess and HTTP call as a breadcrumb, and can add trace
variables to the environment of a subprocess).

**Why GlitchTip never gets an address.** It reads a client's address from `X-Forwarded-For`, `X-Real-IP`, `Client-IP`,
`X-Forwarded`, `X-Cluster-Client-IP`, `Forwarded-For`, `Forwarded` and more (python-ipware), and stores it with the
event for 90 days; "scrub IP addresses" only anonymises it to the /24 (`141.35.7.0`), still a network of the university.
So the edge's `/glitchtip/` location turns `proxy_pass_request_headers` **off** and sends GlitchTip exactly four headers
(`Connection`, `Host`, `Content-Type`, `Content-Length`); with no such header GlitchTip sees the edge's own private
address, which it ignores as not routable, and stores none (`api/tests/test_glitchtip_edge.py` holds the list; the real
nginx and GlitchTip were tried with forged headers). The projects have "scrub IP addresses" on as well (the init
script does it, and it is GlitchTip's default), and `GLITCHTIP_PII_SCRUB_DEFAULT` turns GlitchTip's own scrubbing of
passwords, tokens, card numbers and key blocks on for every project. No cookie, `Authorization`, `Origin` or
`User-Agent` is passed either.

**How the browsers' endpoint is kept small.** `POST /glitchtip/api/<id>/envelope/` on the edge answers 404 for every
project but `GLITCHTIP_WEB_PROJECT_ID` (and for all of them while that is empty), 403 for any other method, 415 when the
request has a `Content-Encoding` (a compressed body is how a small request becomes a large one), 413 above 64 KB, and
429 above 30 requests a minute from one client (burst 20) or 120 a minute from all together (burst 30: a constant-key
zone, so that many browsers cannot fill GlitchTip's disk either; the price is that a flood can lock real reports out for
a while). GlitchTip's CORS allows its own UI's origin only (`CORS_ORIGIN_ALLOW_ALL=false`).

At the cutover the front door forwards `/` to the edge, so `/glitchtip/api/...` reaches it with everything else; a front door
that forwards only some paths (as the beta block did for `/beta/` and `/api/`) needs `/glitchtip/api/` too, and must **never** forward to
port 8081 (GlitchTip's UI).

**First start** (once). **GlitchTip is open to registration while it has no account** (`ENABLE_USER_REGISTRATION or
not User.objects.exists()`, whatever `ENABLE_USER_REGISTRATION` says), so the first account is made before its web
server starts: the `glitchtip` service runs the migrations, the cache table and `glitchtip-init.py` and only then
`./bin/start.sh` (`&&`: if the init fails the container stops, it does not come up open). The init makes the superuser of
`GLITCHTIP_ADMIN_EMAIL` with `GLITCHTIP_ADMIN_PASSWORD` (`deploy/.env`; only when there is no user at all, never a second
one, never a password change), the organisation `decimer`, the projects `decimer-api` and `decimer-web`, a team that holds
both, "scrub IP addresses" on for all of them, and prints a line with the DSN of each. It is safe on every start.

```bash
$DC up -d glitchtip-db glitchtip glitchtip-gate   # first start: the migrations, the init: about a minute, then healthy
$DC logs glitchtip | grep DSN                     # project ids and keys: decimer-api is 1 and decimer-web 2 on a fresh database
ssh -L 8091:127.0.0.1:8091 <the VM>               # from your workstation; open http://127.0.0.1:8091/ and log in
```

Put the DSNs into `deploy/.env` (the key and the project id are the parts that change):

```bash
SENTRY_DSN=http://<decimer-api key>@glitchtip-gate:8000/<decimer-api id>
VITE_SENTRY_DSN=http://<decimer-web key>@127.0.0.1:8090/glitchtip/<decimer-web id>   # the local test; at the cutover https://<key>@decimer.ai/glitchtip/<id>
GLITCHTIP_WEB_PROJECT_ID=<decimer-web id>        # the only project the edge lets a browser report to
$DC up -d api worker worker-fast edge            # recreated with the settings
$DC build edge && $DC up -d edge                 # the web app carries its DSN from now on
# in the UI: Settings of each project (and of the organisation) show "Scrub IP addresses" ON: check it once
# a test report from the api, and one from a browser (type in its console: setTimeout(() => { throw new Error('deploy check') }))
$DC exec api python -c "import logging, os, sentry_sdk; from decimer_api.observability import init_error_reporting; \
init_error_reporting(os.environ['SENTRY_DSN'], service='api', environment='check'); \
logging.getLogger('check').error('deploy check: a test report'); sentry_sdk.flush(5)"
# before an `up` with your own settings, the same checks as in CI, on your file (nothing is started):
python3 -I deploy/check-compose.py --env deploy/.env
```

**Operating it.** The UI is only on `127.0.0.1:${GLITCHTIP_PORT}` (the edge's second listener, `nginx/glitchtip-ui.conf`);
the site's front door forwards to port 8080 alone, so it is not reachable from outside the VM. `GLITCHTIP_DOMAIN` is the address GlitchTip puts
into links: keep it in step with the one your browser uses (the port of the ssh tunnel). The login's `Origin` is checked against
the `Host`, which the UI listener passes on as the browser sent it. Events are kept `GLITCHTIP_RETENTION_DAYS` (90; GlitchTip drops whole daily partitions and says so in its
start banner). Alert e-mails do not exist (`EMAIL_URL=consolemail://`, no route out): look at the UI, or give GlitchTip a
mail relay later (the campus SMTP server, a network with that route for GlitchTip alone: the owner's call). Update: put a
newer tag and digest in `compose.yaml` (at least two weeks old, like the other images), `$DC pull glitchtip glitchtip-db`,
`$DC up -d glitchtip`; migrations run at start. Reset: `docker volume rm decimer-next_glitchtip-pgdata` (after `$DC rm -sf
glitchtip glitchtip-db`). Memory: 384m + 160m (about 0.5 GB, D21); the web worker is recycled at 224 MiB
(`GRANIAN_WORKERS_MAX_RSS`). The compose file's comment on `glitchtip` has the details.

**Logs** of the api, worker and the sidecar are one JSON object per line: `time`, `level`, `logger`, `message` (and `exc`
for a traceback), the same shape as the model services' (`docker logs <container> | jq .`). `message` and `exc` go through
the same text scrubber: no SMILES, molfile or file name, and no public IP address (private and loopback addresses stay:
`replica 172.29.0.5 joined`, a proxy missing from `TRUSTED_PROXY_CIDR`). **Job ids stay** in the logs (the way to follow
up one job; they stop working an hour after it ends), and are replaced by `:id` in error reports. uvicorn's access log is
off and its own log format is replaced (`log_config=None`), so no client address is written by the api. `LOG_FORMAT=text`
and `LOG_LEVEL` are in `.env.example`. nginx's own error log is not changed here (it names the client address of a failed
proxy, see "The edge, briefly").

**Usage counts** replace the legacy TSV files. Per UTC day: jobs accepted by kind (`pdf`, `image`, `paste`) and size
(`small`, `large`: the fast-lane rule), jobs finished `done` and `failed`, the pages and structures of the jobs that
finished `done`, and refused uploads by error code (`queue_full` and `rate_limited` are the capacity signals). **No
address, no job id, no file name, no time of day.** They live in the `usage` Redis (an append-only file on the `usage-data`
volume, so a restart or a reboot keeps them; the jobs' own Redis keeps nothing) for `USAGE_TTL_DAYS` (800), and are counted
best effort (a Redis that is down costs a warning and that count, never an upload or a job). Read them:

```bash
$DC exec api python -m decimer_api.usage --days 30         # a table: one row per day and a total
$DC exec api python -m decimer_api.usage --days 7 --json   # the same as JSON
```

There is deliberately no HTTP route for them. Back up `decimer-next_usage-data` with the reports volume.

## Docker Compose on the VM: two versions

`docker compose version` prints **v2.3.3** (2022): `~/.docker/cli-plugins/docker-compose` shadows the system
plugin from the `docker-compose-plugin` package, **v2.39.2** at `/usr/libexec/docker/cli-plugins/docker-compose`
(and Debian's python `docker-compose` 1.29.2 is at `/usr/bin`). `compose.yaml` is written for the older one
and parses with both (checked with `config -q` on each). To use the newer one without touching anything,
call it by path: `/usr/libexec/docker/cli-plugins/docker-compose -p decimer-next -f deploy/compose.yaml
--env-file deploy/.env ...`. Removing the user-level plugin would change `docker compose` for everyone who
uses it, including whatever manages the legacy stack: the owner's call. Pick one and use it for every
command of the project (a project created by one can be managed by the other, but mixing is untidy).

## Procedure

Every command below is for the person running it; none has been run. Use the project name every time.

```bash
cd /opt/decimer
DC="docker compose -p decimer-next -f deploy/compose.yaml --env-file deploy/.env"
```

**1. Weights and jars** (about 650 MB for ocsr, 270 MB for seg, 64 MB for chem; `--zenodo` downloads seg and
ocsr instead of using the archive; downloads are https only):

```bash
deploy/weights/fetch.sh seg ocsr chem   # archive for seg and DECIMER_model, Zenodo for the hand-drawn model, GitHub for the three jars
# cls: nothing to fetch.
```

**2. Patches** (once; afterwards commit the services' `pyproject.toml` and `uv.lock`), or set
`OPENCV_GUI_LIBS=1` in `.env` and accept unpinned build backends:

```bash
deploy/patches/apply-pyproject-patches.sh
```

**3. Settings:**

```bash
cp deploy/.env.example deploy/.env && chmod 600 deploy/.env
sed -i "s/^IP_HASH_SECRET=.*/IP_HASH_SECRET=$(openssl rand -hex 32)/; s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=$(openssl rand -hex 24)/; s/^GLITCHTIP_SECRET_KEY=.*/GLITCHTIP_SECRET_KEY=$(openssl rand -hex 32)/; s/^GLITCHTIP_DB_PASSWORD=.*/GLITCHTIP_DB_PASSWORD=$(openssl rand -hex 24)/; s/^GLITCHTIP_ADMIN_PASSWORD=.*/GLITCHTIP_ADMIN_PASSWORD=$(openssl rand -hex 24)/" deploy/.env
# set GLITCHTIP_ADMIN_EMAIL to your address (GlitchTip's first account; nothing is mailed)
# edit TRUSTED_PROXY_CIDR when the campus proxy's address is known (TODO Q3)
python3 -I deploy/nginx/check-csp.py    # the edge's CSP equals web/vite.config.ts
python3 -I deploy/check-compose.py      # the properties of compose.yaml a typo could break (dummy secrets, nothing started)
$DC config -q                           # parses (it prints secrets without -q: never paste its output)
```

**4. Build**, one image at a time (the TensorFlow images are heavy; do it when the legacy site is quiet).
The plain `docker build` forms, for reference, are the first of each pair:

```bash
export DOCKER_BUILDKIT=1 COMPOSE_PARALLEL_LIMIT=1
docker build -f deploy/seg.Dockerfile  -t localhost/decimer-next/seg:local  .   # or: $DC build seg
docker build -f deploy/cls.Dockerfile  -t localhost/decimer-next/cls:local  .   # or: $DC build cls
docker build -f deploy/ocsr.Dockerfile -t localhost/decimer-next/ocsr:local .   # or: $DC build ocsr
docker build -f deploy/chem.Dockerfile -t localhost/decimer-next/chem:local .   # or: $DC build chem
docker build -f deploy/api.Dockerfile  -t localhost/decimer-next/api:local  .   # or: $DC build api  (worker, sweep and ingest use it)
docker build -f deploy/web.Dockerfile  -t localhost/decimer-next/edge:local .   # or: $DC build edge
# Without the pyproject patches add --build-arg OPENCV_GUI_LIBS=1 (seg, ocsr). UI-only demo: --build-arg VITE_API=mock.
```

**Release builds pass `--no-cache`** (`docker build --no-cache ...`, or `$DC build --no-cache <service>`), so that
the pinned setuptools really builds the git wheels (decimer-segmentation, decimer-image-classifier, Orthonym).
The `uv sync` step keeps uv's cache in a BuildKit cache mount, and with a warm mount uv installs the wheel it
built from the git dependency in an earlier build instead of building it again: a seg rebuild after `uv.lock`
had changed printed no `Built decimer-segmentation` line, so a changed backend pin would not have been
applied. With `--no-cache` the mount starts empty (checked for seg and cls: every package is downloaded and
`Built decimer-segmentation` / `Built decimer-image-classifier` appears again; 62 s for seg, 52 s for cls on
the VM). If a build must keep the cache, at least make uv rebuild decimer-segmentation.

The builds need the network (PyPI, GitHub, npm, Docker Hub for the base images); the images do not. Tag releases
(`DECIMER_TAG=2026-11-03` in `.env`, or `-t localhost/decimer-next/seg:2026-11-03`) so that the previous images stay.

**Check the images as the users that will run them** (the web build once produced a tree nginx could not
enter; the Dockerfiles check at build time, this is the same check on the finished image):

```bash
docker run --rm --entrypoint sh localhost/decimer-next/edge:local -c \
  'id -un && test -r /usr/share/nginx/html/index.html && find /usr/share/nginx/html /etc/nginx -type f -exec head -c 1 {} + >/dev/null && echo "edge: everything readable"'
for s in seg ocsr; do docker run --rm --entrypoint sh localhost/decimer-next/$s:local -c \
  'id -u && find /models -type f -exec head -c 1 {} + >/dev/null && echo "weights readable"'; done
docker run --rm --entrypoint sh localhost/decimer-next/chem:local -c \
  'id -u && find /opt/orthonym/jars -type f -exec head -c 1 {} + >/dev/null && echo "jars readable"'
```

**5. Start:**

```bash
$DC up -d                 # redis, usage, seg, cls, ocsr (replicas), chem, ingest, api, worker, worker-fast, sweep,
                          # glitchtip-db, glitchtip, glitchtip-gate, edge
$DC ps                    # everything "healthy" after a minute or two; ocsr takes longest; N containers
                          # per seg/ocsr/cls (SEG_REPLICAS ... in .env: 2/2/2 by default)
```

**6. Check:**

```bash
curl -fsS http://127.0.0.1:8090/healthz            # the edge: ok
curl -fsS http://127.0.0.1:8090/readyz             # through to the API: {"status":"ready"}
curl -sI http://127.0.0.1:8090/ | grep -i -E 'content-security|cache-control'
$DC logs --tail 50 worker api edge
# every replica is behind the service name (one address per replica; 2 each beside legacy):
$DC exec worker python -c "import socket; print({n: sorted({a[4][0] for a in socket.getaddrinfo(n, p, proto=socket.IPPROTO_TCP)}) for n, p in (('seg', 8001), ('ocsr', 8002), ('cls', 8003))})"
# no egress from a model service (must fail):
$DC exec seg python -c "import urllib.request; urllib.request.urlopen('https://example.org', timeout=5)"
# the api has no route to the models (must fail):
$DC exec api python -c "import socket; socket.create_connection(('seg', 8001), timeout=3)"
# the ingest sidecar has no route to Redis, chem or GlitchTip (all three must fail), and answers the api:
$DC exec ingest python -c "import socket; socket.create_connection(('redis', 6379), timeout=3)"
$DC exec ingest python -c "import socket; socket.create_connection(('glitchtip', 8000), timeout=3)"
$DC ps ingest                                                    # healthy; the api's /readyz asks it
# GlitchTip is up and not public: its UI is on the loopback port only, the site's port passes one endpoint
curl -fsS http://127.0.0.1:8091/_health/                          # ok (the UI listener)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/glitchtip/        # 404: nothing but the envelope endpoint
# an upload end to end (job id from the answer, then poll):
curl -s -F file=@golden/zou2024_penicillium_shentong.pdf http://127.0.0.1:8090/api/jobs
curl -s http://127.0.0.1:8090/api/jobs/<id>
# P2-05 "done when", cold start with the network down:
docker run --rm --network none -d --name seg-offline localhost/decimer-next/seg:local   # then: docker inspect --format '{{.State.Health.Status}}' seg-offline; docker rm -f seg-offline
# and a corrupted weight must fail the build: append a byte to deploy/weights/seg/DECIMER-Segmentation_model/mask_rcnn_molecule.h5,
# run the seg build (it stops at "verify.sh --strict seg"), then restore with deploy/weights/fetch.sh --force seg
```

**7. Roll back.** The new project never touches legacy, so the first rollback is simply `$DC down`
(volumes stay; `$DC down -v` also deletes the job and report volumes). To go back to an earlier build,
set `DECIMER_TAG` in `.env` to the earlier tag and `$DC up -d` (nothing is rebuilt: `pull_policy: never`).
The cutover rollback is the legacy nginx configuration kept from before the switch; it is not part
of this directory.

**Switching layout** (beside legacy to after cutover, or back): edit the replica, thread, CPU and worker lines in
`.env` (the block in `.env.example` says which), then `$DC up -d`. Compose creates or removes replicas and
recreates the services whose settings changed (ocsr takes about 50 s to load); do it when the site is quiet.
Cutover itself (legacy removed) is what frees the memory for 6/3/3.

**Updating** one service: build it with a new tag, set `DECIMER_TAG`, `$DC up -d`. After the `api`
container is recreated nothing else needs a restart (the edge re-resolves it). The legacy nginx, if it
proxies to `decimer-next-edge` with the variable form in `nginx/beta-front-door.conf.example`, re-resolves
too.

## Before /beta

None of this has been done; the proxy values are open (TODO Q3). In order:

1. **The campus proxy's `X-Forwarded-For`** decides whether the per-IP limits mean anything: does it
   append the client, replace the header, or send none? Three cases and how to check are written out at the
   top of `nginx/beta-front-door.conf.example`. List the proxy's address(es) in `TRUSTED_PROXY_CIDR` of
   `deploy/.env` (the edge trusts them plus the public network; the API trusts only the app network).
   Without it every client behind the proxy shares one limit; nobody can choose an identity.
2. **The legacy nginx has no realip configuration** (read from `nginx -T`): it sees the campus proxy as
   the client, so its own `heavy_req` limits count that one address. The new blocks pass
   `X-Forwarded-For` on and do not change legacy's behaviour.
3. **Front-door blocks** (`nginx/beta-front-door.conf.example`, parses in a stub server): `^~` so they win
   over legacy's regex locations, `proxy_http_version 1.1`, `access_log off` (job ids are bearer tokens and
   legacy's access log records the URI), `proxy_read_timeout 65s` (legacy's http level says 300 and would be
   inherited), `client_max_body_size 101m`, and a **resolver with a variable `proxy_pass`**, so a missing
   edge is a 502 on `/beta/` and `/api/` only and never stops the legacy nginx from starting or reloading
   (with a literal host name it fails with "host not found in upstream").
4. **Order:** apply = new stack healthy, attach the legacy nginx to `decimer-next_public`, test the new
   `default.conf` in a throwaway container, reload. Roll back in reverse: restore `default.conf` and reload,
   disconnect, only then `$DC down`.
5. **`/api/` and `/beta/` both**: the API's absolute `/api/...` URLs (see above) mean `/api/` itself must
   reach the new stack; the legacy site has nothing at `/api/` that matters.

## Running beside legacy (P5-02, TODO)

The edge publishes `127.0.0.1:8090` only. To show it on `/beta/` of the public site, something has to
carry `/beta/` **and `/api/`** to it: the legacy nginx after being attached to `decimer-next_public` (a
change to the running legacy container, so it needs the owner's go-ahead), or the campus proxy routing both
paths (TODO Q3). At cutover the same build serves `/`. `edge.conf` ends with a commented variant for a front
door that does not strip the prefix.

## For the coordinator: documents that disagree with this directory

* **ROADMAP §4, Phase 5** ("Deploy beside legacy in the same Compose project"): this is a **separate project**,
  `decimer-next` (its own networks and volumes; `up`, `down` and a failed build of one can never touch the
  other; rollback is `down`). Please change the text, and P5-02 if it says the same.
* **ROADMAP §2 diagram** (`/media/{job}/...` on nginx): media is `/api/jobs/{id}/media/{name}`, answered by the
  API with `X-Accel-Redirect` to the internal `/_protected/jobs/...`; nothing is served at `/media/`.
* **ROADMAP §2 / TODO** (`/beta` or a `beta.` host): the edge always serves at its root; `/beta/` is a
  mapping in the front door, which must also route `/api/`.
* **Root `.env.example`, TRUSTED_PROXY_CIDR** ("Behind docker, include the nginx container's network as well as
  the campus proxy"): in this deployment the API trusts only the app network (derived from `APP_SUBNET`) and
  the edge trusts the public network plus the campus proxies; `deploy/.env.example` documents the variable
  as "campus proxies only".
* **TODO** ("Compose for api, worker, redis and the three services"): it is api, worker, worker-fast, sweep,
  redis, usage, seg, cls, ocsr, chem, ingest, glitchtip-gate, glitchtip, glitchtip-db and edge, plus the `patches/` and `weights/`
  machinery. "Sentry/GlitchTip" is GlitchTip; "the ingest sidecar" and "the worker's hard
  dependency on a healthy chem" are done as described in "What the API review required" and the worker settings.
* **`~/.docker/cli-plugins` shadows the newer Compose** (above): worth a line in `docs/RUNBOOK.md`.

## Backups and retention

* `decimer-next_jobs`: job files, swept 1 h after a job ends (`sweep`), nothing to back up.
* `decimer-next_reports`: problem reports, private (api only, never the edge). Deleted by hand
  once analysed, and by the api itself when older than `REPORTS_MAX_AGE_DAYS` (364 days, a day short of the 12
  months the privacy page promises: at start-up and then every hour; `api/README.md`, "report"). **In no backup, on purpose:** the privacy page says reports
  are in none, so the backup of the VM must leave this volume out, or a deleted report would live
  on in it. Reading it needs a throwaway container:
  `docker run --rm -v decimer-next_reports:/r:ro busybox tar -C /r -cf - .`.
* `decimer-next_edge-cache`: nginx's temporary files for buffered uploads; emptied at every edge start.
* `decimer-next_edge-logs`: nginx's error log and at most six rotated days, deleted after seven days by
  `edge-logs.sh` (above, "Error log"). Client addresses are in it: not for a backup either.
* `decimer-next_usage-data`: the usage counter's append-only file, a few KB per day for 800 days; the only
  history here that cannot be recomputed. **Backed up by nothing yet.**
* `decimer-next_glitchtip-pgdata`: GlitchTip's Postgres, the error reports of the last 90 days (scrubbed, no
  personal data by construction). Not backed up, on purpose; `docker volume rm` starts a fresh GlitchTip.
* `decimer-next_ingest-scratch`: the sidecar's request in flight; emptied after every request.

## Open TODOs

| TODO | Where | What is needed |
|---|---|---|
| pyproject patches | `patches/` | Apply `apply-pyproject-patches.sh` and commit the services' lock files; check seg, ocsr, cls and chem still pass `uv run pytest` and the slow tests |
| ingest sidecar | `compose.yaml`, API | **Built, tested against real sockets and child processes; not yet run in containers**: first start on the VM (ingest healthy before the api; an upload end to end; no route from `ingest` to Redis or GlitchTip: the commands are in step 6). Required before cutover, and then in the default start |
| replica and thread numbers | `.env`, compose | Verify the model's estimates (P3-13: golden paper and a stress case at 2/2/2 and 6/3/3, `docker stats`); the ocsr thread sensitivity is the weakest input |
| first `up` with replicas | the VM | `up` was not run: confirm N containers per service and the DNS check; check the worker's log shows `replica ... joined` once per replica (P3-04b is tested against fake replicas only); Compose v2.3.3 and v2.39.2 both parse the file |
| fast lane on the VM | `compose.yaml` | `worker-fast` is part of the default start now: check it becomes healthy and `redis-cli exists decimer:fast:health-check` is 1; if memory is tight, run without it (`--scale worker-fast=0`, safe) |
| child oom_score_adj | API, worker | Ingest and render children lower their own `oom_score_adj` (1000); compose cannot do it per process |
| `TRUSTED_PROXY_CIDR` | `.env` | The campus proxy's address(es) and what it does to `X-Forwarded-For` (Q3) |
| campus proxy hostname | front door | Can it route `/beta/` and `/api/`, or provide `beta.decimer.ai` (Q3) |
| chem in the api | `compose.yaml` | Done in P3-06: the `chem-api` network and the api's `CHEM_URL`; check on the VM that the api reaches `chem:8004` and not seg, cls or ocsr |
| reports volume | `compose.yaml` | `REPORTS_DIR` is the api's setting (P3-08, done); old reports are deleted by the api (`REPORTS_MAX_AGE_DAYS`); the backup must exclude this volume |
| cls soak | `compose.yaml` | A few thousand requests, watch RSS, confirm 2.5 GB |
| memory limits per replica | `compose.yaml` | seg 3 GB, ocsr 6 GB, cls 2.5 GB come from the READMEs; measure on the VM |
| GlitchTip first start | `.env`, the VM | Set `GLITCHTIP_ADMIN_EMAIL` and the three secrets, start it (the init makes the account and the projects), put the two DSNs and `GLITCHTIP_WEB_PROJECT_ID` into `deploy/.env`, rebuild the edge (see "Observability"); check one test report from the api and one from a browser arrives scrubbed, and that the projects show "Scrub IP addresses" on |
| GlitchTip alerts | compose | No alert e-mail: GlitchTip has no route out. Either look at the UI regularly, or give it a mail relay (the campus SMTP server; a network with that route for GlitchTip alone; the owner's call) |
| SPA API client | `web.Dockerfile` | `VITE_API=http` builds, but the client is TODO: the app errors at start-up until then |
| privacy review | `nginx/edge.conf` | The log content is settled and on the privacy page: no addresses in the access log, addresses in the error log, deleted after seven days (`edge-logs.sh`, tested); open: what the university proxy logs |
| edge-logs on the VM | `compose.yaml`, `web.Dockerfile` | Untested on the VM: that nginx (uid 101) opens `error.log` on a fresh `edge-logs` volume, that `USR1` makes it open a new file after the daily rename, and that the edge is `healthy` (`enforce` passes); smoke-tested on a scratch image only (fresh volume, read-only root, uid 101) |
| untested runtime pieces | compose | Redis reading its config from stdin (also for `usage`, with its append-only file in the `usage-data` volume), the read-only file systems (also `ingest`'s, with the `ingest-scratch` volume owned by uid 10001 from the image), the named-volume ownership, nginx writing `/var/cache/nginx` as uid 101, GlitchTip's and Postgres's first start in the compose project (they ran in scratch containers with the same flags): first start on the VM |
| Compose version | the VM | v2.3.3 shadows v2.39.2: pick one (above) |

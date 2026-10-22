# deploy/: the new stack's images, Compose project and nginx edge

Everything needed to run the new decimer.ai stack **beside the legacy one** on the production VM
. Nothing here has been built or started: the commands below are
written out for whoever runs them (the orchestrator, later).

The legacy stack (`decimer_web-nginx-1` on host port 8080, `decimer_web-app-1`,
`decimer_web-supervisor-1`) is never touched. The new stack is a separate Compose project, `decimer-next`,
with its own networks and volumes, and publishes one port: `127.0.0.1:8090`.

## What is in here

| Path | What |
|---|---|
| `compose.yaml` | The project: redis, seg, cls, ocsr, chem, api, worker, sweep, edge |
| `.env.example` | Every setting a deployment sets (copy to `deploy/.env`, which is never committed) |
| `seg.Dockerfile`, `cls.Dockerfile`, `ocsr.Dockerfile` | TensorFlow 2.15.1 model services, Python 3.11, weights baked in and checked |
| `chem.Dockerfile` | Python 3.12 + Temurin 21 JRE + Orthonym and its two jars, baked in and checked |
| `api.Dockerfile` | Python 3.12; one image for the API, the arq worker and the sweep |
| `web.Dockerfile` | Node 24 build of the React app, then nginx with the app and the edge configuration |
| `*.Dockerfile.dockerignore` | The build context of each image (BuildKit reads these next to the Dockerfile): an allow-list |
| `nginx/` | `nginx.conf`, `edge.conf`, `security-headers.conf`, `edge-entrypoint.sh`, `check-csp.py`, `beta-front-door.conf.example` |
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
    `uv.lock` does not pin it. The constraints pin setuptools 84.0.0, wheel 0.48.0, hatchling 1.32.4 and
    its dependencies, and `uv-build` 0.13.5 (the newest releases that were two weeks old on 2026-10-22).
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
  service's own download made, and the chem jars' hashes are the ones Orthonym pins and
  `services/chem/README.md` lists (a download from the GitHub releases verified against them).
* **ocsr bakes BOTH DECIMER models (owner decision D15).** `import DECIMER` loads the printed and the
  hand-drawn model and would download a missing one at start-up, so both are in the image (about 650 MB).
  The hand-drawn one is never used by the service and costs 1.9 GB of memory and 26 s of start-up: that is
  why ocsr's limit is **6 GB** and its health check waits (**start period 180 s**, probes every 5 s during
  start-up, every 120 s afterwards; start-up is about 50 s). Reopen only if memory gets tight on the VM
  (then make the hand-drawn load lazy upstream in DECIMER).
* **chem (services/chem).** `CHEM_JARS_DIR=/opt/orthonym/jars`; the jars are made world-readable (Orthonym
  writes them 0600) and read as uid 10001 at build time; `ORTHONYM_JVM_XMX=512m` is the heap cap
  (an explicit `-Xmx` wins over `-XX:MaxRAMPercentage`, so the image sets no `JAVA_TOOL_OPTIONS`);
  none of `ORTHONYM_ALLOW_REDUCED`, `ORTHONYM_OPSIN_JAR`, `ORTHONYM_CENTRES_JAR`,
  `ORTHONYM_DISABLE_JPYPE`, `CHEM_ALLOW_JAR_DOWNLOAD` is ever set (the service refuses to start with the
  first four). `JAVA_HOME` points at the copied Temurin JRE. D14 (the Orthonym `valid` tier) is a setting
  of the service's code and changes nothing in the image.
* **Permissions are checked as the service user.** Weights and jars are made `a+rX` and the build runs a
  read of every file as uid 10001; the web build is `chmod -R a+rX dist`, copied without `--chmod` (which
  applies to directories too and once made them unenterable for nginx) and read as uid 101.
* **pystow needs a README in its home directory.** `import pystow` writes `$PYSTOW_HOME/README.md` when it
  is missing, which fails on a read-only `/models`; the seg and ocsr images put the file there.
* **Environment:** `PYSTOW_HOME=/models` (seg, ocsr); `TF_NUM_INTRAOP_THREADS`/`TF_NUM_INTEROP_THREADS`
  4/1 for seg and ocsr (the containers get 4 CPUs) and 2/1 for cls (measured in its README), all
  overridable from `.env`; `MALLOC_ARENA_MAX=2` for cls (the RSS growth soak note);
  `OPENCV_IO_MAX_IMAGE_PIXELS=40000000` for seg and ocsr (seg's own default, written out; do not raise it).
* **Healthchecks** hit `/readyz` (which runs a real inference) for the model services and the API,
  `/healthz` for the edge, the arq health-check key in Redis for the worker, and nothing for the sweep.
  seg and ocsr probe every 120 s once running (an idle service pays its selftest on each probe) and every
  5 s during start-up (`--start-interval`, Docker 25 or newer; the engine on the VM is 28.4), so
  `depends_on: condition: service_healthy` does not wait a whole interval. The worker waits for seg, cls,
  ocsr and chem.
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
* **Networks.** `public` is the edge alone with the published port (docker cannot publish from an internal
  network, so it is the one network with a route out; nothing but nginx is on it). `app` (internal) is
  edge + api with a pinned subnet; `backend` (internal) is redis, api, worker, sweep; `models` (internal)
  is the worker and the model services. **The api is not on `models`**: it calls no model service yet;
  when `POST .../validate` lands it needs chem and nothing else, and `compose.yaml` ends with a commented
  chem-only network for that. There is no `egress` network until error reporting exists (then: a plain
  bridge for api and worker only). So the model services, redis and the sweep have no route to the
  internet, and the api cannot reach seg, cls or ocsr.
* **Trusted proxies, one variable per hop.** The API's peer is the edge on `app`, so the API trusts
  exactly `APP_SUBNET` (derived from the variable that defines the network, not typed twice). The edge
  trusts `PUBLIC_SUBNET` (the host side of the published port and, during the beta, the legacy nginx)
  plus the campus proxies in `TRUSTED_PROXY_CIDR` of `.env`, and gives the API one `X-Forwarded-For`
  entry, the real client. **Proxy addresses, never client networks** (every host in a listed network can
  choose the address its limits are counted against; `/0` is refused by both).
* **Process settings.** Services have `no-new-privileges`, `cap_drop: ALL`, log rotation (5 x 10 MB), and
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
3. **The ingest sidecar is still required before cutover (TODO, not built).** The API parses every
   upload in a child process of its own container: untrusted PDFs and images through pdfium, libheif and
   Pillow, the most exploitable code in the stack. That child runs as the API's user, in the API's
   container, so a compromised parser can: **read and write all of `DATA_DIR`**, that is every other
   user's uploads and results, and plant files or symlinks in other jobs' folders (privacy, and the
   retention promise); open connections on the `backend` network (Redis, behind its password; the
   child's environment holds no secret and the API is non-dumpable, but a password only slows guessing);
   and use the API's identity towards the edge. The non-dumpable flag and the minimal child
   environment close the `/proc` door and nothing else. The sidecar closes the rest: an `ingest`
   service from `api.Dockerfile` running only the ingest worker, on a private network shared with the api
   only (**no route to Redis**), under another uid, with the memory limit (INGEST_CONCURRENCY x
   INGEST_MEMORY_MB) moved from the api to it, and **per-request scratch, not all of `DATA_DIR`**: the api
   stages a request's files in a scratch directory (or volume) that holds only that request, the sidecar
   reads the input from it and writes the result back into it, and the api moves the result into
   `DATA_DIR/<job id>/input/` itself. Then a compromised parser sees one upload. The API calls it through
   the `IngestRunner` seam (`create_app(..., ingest_runner=...)`) instead of spawning a child; its limit
   shrinks to about 0.5 GB. This needs an API change; until then `api` has 3.5 GB and the beta carries
   the risk, which is acceptable for a beta and not for the cutover.
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

* **The worker downscales pages for seg.** seg refuses images above 40 MP (422) and its
  memory limit is sized for that: the library keeps one full-page mask per detection. The API admits pages up
  to 50 MP, so pages between 40 and 50 MP (B2 size at 300 DPI) only work when the worker shrinks them
  before the call, which is safe for the same reason it is for ocsr (the model looks at a thumbnail).
  Nothing here raises seg's cap, and nothing should until the memory limit is re-measured.
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

* **Privacy in the logs (for P5-06, the data-protection review):** the access log has no client address, no
  query string, and job ids replaced by `:id` (they are bearer tokens). **nginx's error log still names the
  real client address of failed proxying** (level `error`), and lives as long as docker's log rotation (5 x
  10 MB) keeps it; rate-limit refusals are logged below its level and show as 429s in the access log. Say so
  in the privacy text or lower the error log (`error_log stderr crit`), and decide before the beta.
* **DNS:** the edge resolves `api` per request through docker's resolver, so recreating the api container
  does not leave nginx holding a stale address.
* **Leftover uploads:** the edge empties `/var/cache/nginx` when it starts, so the buffered body of a request
  that was cut off by a crash does not outlive the process.

## Memory budget

The VM: 62 GB RAM, 8 CPUs, 395 GB free disk (checked 2026-10-22). The legacy stack uses about 22 GB.
`free -g` at that moment: 28 used, 32 buff/cache, 34 available.

| Service | Limit | Expected resident | Basis |
|---|---|---|---|
| seg | 3 GB | about 2 GB | seg README (RSS about 2 GB, 3.5 s a page); mask memory grows with page size and detections |
| cls | 2.5 GB | about 1 GB, 1.9 GB worst | cls README; RSS grows 57-100 KB per request: soak test TODO |
| ocsr | 6 GB | about 4.8 GB | ocsr README: 4.8 GB resident with both models, +0.4 GB worst request, +0.3 GB bodies |
| chem | 1.5 GB | about 0.4 GB | chem README: 360 MB naming process + 50 MB web process, flat over 1000 names |
| api | 3.5 GB | 0.3 GB + up to 2 x 1.5 GB while uploads are parsed | INGEST_CONCURRENCY x INGEST_MEMORY_MB (1536) + base |
| worker | 2 GB | 0.5 GB idle | a 50 MP page render child is about 0.5 GB; TODO measure |
| redis | 384 MB | tens of MB | `maxmemory 256mb` |
| sweep | 192 MB | about 100 MB | a Python process with the redis client |
| edge | 256 MB | about 20 MB | buffered uploads go to disk |
| **Sum of limits** | **19.3 GB (17.8 GB without chem)** | about 9.5 GB | |

Worst case with every limit reached: 22 + 19.3 = 41 GB of 62 GB. The limits are ceilings: a container
over its limit is OOM-killed and restarted, never swapped (`memswap_limit` equals `mem_limit`). CPU limits
add up to more than the 8 cores (seg 4, ocsr 4, cls 2, chem 2, api 2, worker 2, redis 1, edge 1, sweep 0.5) on
purpose: a job runs its stages one after the other. If the legacy site slows down during the beta,
lower seg and ocsr first (and their `TF_NUM_*` threads). Images are several GB each for the three
TensorFlow services (estimate, not built); disk is no concern.

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

**1. Weights and jars** (about 650 MB for ocsr, 270 MB for seg, 16 MB for chem; `--zenodo` downloads seg and
ocsr instead of using the archive; downloads are https only):

```bash
deploy/weights/fetch.sh seg ocsr chem   # archive for seg and DECIMER_model, Zenodo for the hand-drawn model, GitHub for the jars
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
sed -i "s/^IP_HASH_SECRET=.*/IP_HASH_SECRET=$(openssl rand -hex 32)/; s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=$(openssl rand -hex 24)/" deploy/.env
# edit TRUSTED_PROXY_CIDR when the campus proxy's address is known (TODO Q3)
python3 -I deploy/nginx/check-csp.py    # the edge's CSP equals web/vite.config.ts
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
docker build -f deploy/api.Dockerfile  -t localhost/decimer-next/api:local  .   # or: $DC build api  (worker and sweep use it)
docker build -f deploy/web.Dockerfile  -t localhost/decimer-next/edge:local .   # or: $DC build edge
# Without the pyproject patches add --build-arg OPENCV_GUI_LIBS=1 (seg, ocsr). UI-only demo: --build-arg VITE_API=mock.
```

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
$DC up -d                 # redis, seg, cls, ocsr, chem, api, worker, sweep, edge
$DC ps                    # everything "healthy" after about a minute; ocsr takes longest
```

**6. Check:**

```bash
curl -fsS http://127.0.0.1:8090/healthz            # the edge: ok
curl -fsS http://127.0.0.1:8090/readyz             # through to the API: {"status":"ready"}
curl -sI http://127.0.0.1:8090/ | grep -i -E 'content-security|cache-control'
$DC logs --tail 50 worker api edge
# no egress from a model service (must fail):
$DC exec seg python -c "import urllib.request; urllib.request.urlopen('https://example.org', timeout=5)"
# the api has no route to the models (must fail):
$DC exec api python -c "import socket; socket.create_connection(('seg', 8001), timeout=3)"
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
* **TODO** ("Compose for api, worker, redis and the three services"): it is api, worker, sweep, redis,
  seg, cls, ocsr, chem and edge, plus the `patches/` and `weights/` machinery.
* **`~/.docker/cli-plugins` shadows the newer Compose** (above): worth a line in `docs/RUNBOOK.md`.

## Backups and retention

* `decimer-next_jobs`: job files, swept 1 h after a job ends (`sweep`), nothing to back up.
* `decimer-next_reports`: problem reports, private (api only, never the edge), kept until
  analysed, then deleted by hand. **Backed up by nothing yet.** Reading it needs a throwaway
  container: `docker run --rm -v decimer-next_reports:/r:ro busybox tar -C /r -cf - .`.
* `decimer-next_edge-cache`: nginx's temporary files for buffered uploads; emptied at every edge start.

## Open TODOs

| TODO | Where | What is needed |
|---|---|---|
| pyproject patches | `patches/` | Apply `apply-pyproject-patches.sh` and commit the services' lock files; check seg, ocsr, cls and chem still pass `uv run pytest` and the slow tests |
| ingest sidecar | `compose.yaml`, API | Required before cutover (see above); needs an API change (an `IngestRunner` that calls the sidecar, per-request scratch) |
| seg page downscaling | worker | P3-04: downscale pages above 40 MP for seg; seg's cap stays |
| child oom_score_adj | API, worker | Ingest and render children lower their own `oom_score_adj` (1000); compose cannot do it per process |
| `TRUSTED_PROXY_CIDR` | `.env` | The campus proxy's address(es) and what it does to `X-Forwarded-For` (Q3) |
| campus proxy hostname | front door | Can it route `/beta/` and `/api/`, or provide `beta.decimer.ai` (Q3) |
| chem in the api | `compose.yaml` | When `/validate` lands: the `chem-api` network for api and chem |
| reports volume | `compose.yaml` | The real variable name for the report directory (P3-08; `REPORTS_DIR` is a guess) and the backup |
| cls soak | `compose.yaml` | A few thousand requests, watch RSS, confirm 2.5 GB |
| TF threads and CPU caps | `.env` | Tune with the golden-paper measurement; ocsr's 6 GB from its README, measure on the VM |
| Sentry or GlitchTip | `.env`, compose | Decide which; then an `egress` network for api and worker, and the SPA's CSP `connect-src` |
| SPA API client | `web.Dockerfile` | `VITE_API=http` builds, but the client is TODO: the app errors at start-up until then |
| privacy review | `nginx/edge.conf` | The log content: no addresses in the access log, addresses in the error log |
| untested runtime pieces | compose | Redis reading its config from stdin, the read-only file systems, the named-volume ownership, nginx writing `/var/cache/nginx` as uid 101: first start on the VM |
| Compose version | the VM | v2.3.3 shadows v2.39.2: pick one (above) |

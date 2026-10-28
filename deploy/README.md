# deploy/: build, run and operate decimer.ai

Everything needed to run decimer.ai on one Linux server with Docker: the Dockerfiles, one Compose project
(`decimer-next`) with the nginx edge, the job API, the workers, the model services and a self-hosted GlitchTip for
error reports, plus a front door and an uptime check for your own reverse-proxy setup. The images are built on your
machine and never pulled (`pull_policy: never`, names `localhost/decimer-next/<service>:<tag>`), so there is no registry
namespace that somebody else could own.

| Path | What |
|---|---|
| `compose.yaml` | The project: redis, `usage` (the usage counter's Redis), seg, cls, ocsr (replicated), chem, `ingest` (the ingest sidecar), api, worker, `worker-fast` (the fast lane), sweep, edge, GlitchTip with its Postgres and `glitchtip-gate` |
| `.env.example` | Every setting a deployment sets (copy to `deploy/.env`, which is never committed) |
| `seg.Dockerfile`, `cls.Dockerfile`, `ocsr.Dockerfile` | The TensorFlow 2.15.1 model services (Python 3.11), weights baked in and checked |
| `chem.Dockerfile` | Python 3.12, a Temurin 21 JRE, Orthonym with its two jars and the CDK 2.8 jar |
| `api.Dockerfile` | Python 3.12; one image for the API, the worker, the sweep, the ingest sidecar and the usage command |
| `web.Dockerfile` | Node 24 build of the React app, then nginx with the app and the edge configuration |
| `nginx/` | `nginx.conf`, `edge.conf`, `security-headers.conf`, the GlitchTip UI listener and gate, `edge-logs.sh`, `check-csp.py` |
| `frontdoor/` | An optional small nginx between your TLS-terminating proxy and the edge (see "Reverse proxy and TLS") |
| `uptime/` | An uptime check for cron or systemd with a pluggable alert hook |
| `weights/` | `fetch.sh`, `verify.sh` and `SHA256SUMS`; the fetched weights land here (not in git) |
| `patches/` | The `pyproject.toml` changes behind the images (already applied) and the script that re-checks them |
| `check-compose.py` | `python3 -I deploy/check-compose.py`: the properties of `compose.yaml` that a typo could break (networks, ports, secrets, isolation), checked on `docker compose config` with dummy secrets; nothing is started |

## What you need

* A Linux server with **Docker Engine 25 or newer** (the image health checks use `--start-interval`; an older engine
  rejects the Dockerfiles), Compose v2 (`docker compose`; v2.3.3 and v2.39.2 both read `compose.yaml`) and BuildKit
  (the default in current Docker).
* Memory and CPU for your layout (see "Replicas, threads and memory"): the default layout needs about 17 GB resident
  and 34 GB if every container sat at its limit; 8 CPU cores are what it is tuned for.
* `python3`, `curl`, `openssl`, GNU coreutils (`md5sum`, `sha256sum`, `cp`) for the scripts, and
  [uv](https://docs.astral.sh/uv/) for the golden check.
* Internet access while you fetch the weights and build (PyPI, GitHub, npm, Docker Hub, Zenodo). The running stack needs
  none: the model services, Redis, the sidecar and GlitchTip have no route out.
* For a public site, a TLS reverse proxy of your own (see "Reverse proxy and TLS"). The stack speaks plain http on
  `127.0.0.1:8090` only.

## First start

Run everything from the repository root. `/opt/decimer` stands for wherever you cloned it.

```bash
git clone https://github.com/Steinbeck-Lab/DECIMER.ai /opt/decimer && cd /opt/decimer
DC="docker compose -p decimer-next -f deploy/compose.yaml --env-file deploy/.env"
```

Always pass the project name: it names the networks and volumes (`decimer-next_public`, `decimer-next_jobs`, ...) that
the commands below use.

**1. Weights and jars** (about 650 MB for ocsr, 270 MB for seg, 64 MB for chem; https only, every file checked against
`SHA256SUMS`):

```bash
deploy/weights/fetch.sh seg ocsr chem      # Zenodo for the models, GitHub for the three jars
# cls: nothing to fetch (its weights ship inside the pip package).
```

`--archive DIR` uses a local directory of earlier downloads where it has the files (`segmentation/mask_rcnn_molecule.h5`
and `decimer-transformer/DECIMER-V2/DECIMER_model/`); `--zenodo` ignores it. A fetch whose result does not verify is
removed again, so a directory that exists has been checked.

**2. Settings.** `deploy/.env` holds secrets: it is never committed, never printed (`$DC config` prints it; use
`$DC config -q`) and should be mode 600.

```bash
cp deploy/.env.example deploy/.env && chmod 600 deploy/.env
sed -i "s/^IP_HASH_SECRET=.*/IP_HASH_SECRET=$(openssl rand -hex 32)/; s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=$(openssl rand -hex 24)/; s/^GLITCHTIP_SECRET_KEY=.*/GLITCHTIP_SECRET_KEY=$(openssl rand -hex 32)/; s/^GLITCHTIP_DB_PASSWORD=.*/GLITCHTIP_DB_PASSWORD=$(openssl rand -hex 24)/; s/^GLITCHTIP_ADMIN_PASSWORD=.*/GLITCHTIP_ADMIN_PASSWORD=$(openssl rand -hex 24)/" deploy/.env
# set GLITCHTIP_ADMIN_EMAIL to your address (GlitchTip's first account; nothing is mailed)
python3 -I deploy/nginx/check-csp.py                 # the edge's CSP equals web/vite.config.ts
python3 -I deploy/check-compose.py --env deploy/.env # the invariants of compose.yaml, on your settings (nothing is started)
$DC config -q                                        # parses
```

Use hex for the Redis and database passwords: they go into URLs. `IP_HASH_SECRET` keys the per-client hash of addresses
(the api keeps no raw addresses); without it the per-client counters would reset at every restart. Also decide now: the
replica counts (next section) and `TRUSTED_PROXY_CIDR` (see "Reverse proxy and TLS").

**3. Build.** Every build uses the repository root as its context.

```bash
export DOCKER_BUILDKIT=1 COMPOSE_PARALLEL_LIMIT=1   # one build at a time: the TensorFlow images are heavy
$DC build                                            # edge, api, seg, cls, ocsr, chem (worker, sweep and ingest use the api image)
# or one image: $DC build seg    ==    docker build -f deploy/seg.Dockerfile -t localhost/decimer-next/seg:local .
```

The build needs the network, the images do not. **Release builds pass `--no-cache`** (`$DC build --no-cache`): the
git dependencies of cls and chem are built from source with build backends that `pyproject.toml` pins, and a warm uv
cache mount would install the wheel it built earlier instead of building it again, so a changed pin would never apply.
Tag releases (`DECIMER_TAG=2026-11-03` in `deploy/.env`) so that the previous images stay for a rollback.

**4. Start.**

```bash
$DC up -d
$DC ps          # everything "healthy" after a minute or two; ocsr takes longest (about 25 s to load its model)
```

**5. Check.**

```bash
curl -fsS http://127.0.0.1:8090/healthz     # the edge: ok
curl -fsS http://127.0.0.1:8090/readyz      # through to the API (Redis, the job volume, the ingest sidecar): {"status":"ready"}
$DC logs --tail 50 worker api edge
# every replica is behind its service name (one address per replica):
$DC exec worker python -c "import socket; print({n: sorted({a[4][0] for a in socket.getaddrinfo(n, p, proto=socket.IPPROTO_TCP)}) for n, p in (('seg', 8001), ('ocsr', 8002), ('cls', 8003))})"
# an upload end to end (take the job id from the answer, then poll):
curl -s -F file=@golden/zou2024_penicillium_shentong.pdf http://127.0.0.1:8090/api/jobs
curl -s http://127.0.0.1:8090/api/jobs/<id>
```

The isolation the design promises can be checked too; each of these must fail:

```bash
$DC exec seg python -c "import urllib.request; urllib.request.urlopen('https://example.org', timeout=5)"   # no route out
$DC exec api python -c "import socket; socket.create_connection(('seg', 8001), timeout=3)"                  # the api has no route to the models
$DC exec ingest python -c "import socket; socket.create_connection(('redis', 6379), timeout=3)"            # nor does the ingest sidecar to Redis
```

**6. Golden check.** Before a stack serves users, and before any change to a model, TensorFlow, DECIMER, RDKit, CDK or the
PDF renderer reaches them, run the golden paper through it:

```bash
golden/smoke.sh [BASE_URL]      # default http://127.0.0.1:8090; PASS or FAIL with the numbers; leaves golden/runs/next-<time>/
```

It needs uv, takes about 80 to 100 s and loads the model services like one visitor with a 10-page PDF. The pass rule
is in `golden/README.md`: at least as many structures right as the earlier Laravel version of decimer.ai (13 of the 24
scored labels), no more invalid SMILES than it (1), at most one cut-off prediction (`GOLDEN_MIN_CORRECT`,
`GOLDEN_MAX_INVALID`, `GOLDEN_MAX_INCOMPLETE` change the thresholds), and no crash. Exit 0 is a pass, 1 a failure, 2 "could
not run".

## Replicas, threads and memory

seg, ocsr and cls run as N identical containers each (`SEG_REPLICAS`, `OCSR_REPLICAS`, `CLS_REPLICAS` in
`deploy/.env`; chem is always 1). The counts, threads and memory limits are configuration only. `deploy.replicas` makes
Compose create the containers, and **the service name resolves, on the `models` network, to the address of every
replica**. The worker finds them that way: it resolves all A records of `seg`, `ocsr` and `cls` (every
`REPLICA_REFRESH_SECONDS`, 10, and at once after a connection failed), sends each call to the **least-busy healthy
replica**, keeps at most `<SERVICE>_CONCURRENCY` (1) calls per replica in flight and skips for 10 s a replica that
refuses connections. Nothing else is promised: per-replica names and addresses are not part of the contract, and a
scaled service has no `container_name` and no published port. A one-off change is `$DC up -d --scale seg=3`.

**Per replica** (limits are per container; resident figures are from the service READMEs):

| | memory limit | resident | TensorFlow intra-op threads | `cpus` |
|---|---|---|---|---|
| seg | 3 GB (`SEG_MEM_LIMIT`) | about 2 GB | 2 (small server), 1 (8-core server) | 2.5 / 1.5 |
| ocsr | 4 GB | about 3.0 GB (3.6 GB at the very worst) | 2 | 2 |
| cls | 2.5 GB | about 1 GB | 1 | 1.5 |
| chem (one replica) | 1.5 GB | about 0.4 GB | | 2 |

`cpus` is the thread cap plus half a core for TensorFlow's other threads (ocsr decodes token by token and uses about
the cap). **The `*_CPUS` values do not follow the thread counts by themselves: change both by hand, together.** On
linux/arm64 (Docker on Apple Silicon) seg holds about 4.2 GiB right after start-up: set `SEG_MEM_LIMIT=6g` there.

**Two example layouts** (the first is the default of `compose.yaml`; `.env.example` has the lines for both):

| | seg | ocsr | cls | worker jobs / renders | memory resident / ceiling | meant for |
|---|---|---|---|---|---|---|
| **Small server** (default) | 2 | 3 | 2 | 4 / 1 (`WORKER_MEM_LIMIT` 2g) | about 17 GB / 34 GB | 8 cores, about 24 GB or more |
| **8-core server** | 6 | 4 | 3 | 8 / 2 (`WORKER_MEM_LIMIT` 3g) | about 29.5 GB / 54 GB | 8 cores, 64 GB |

The 8-core layout also sets `SEG_TF_INTRAOP_THREADS=1` and `SEG_CPUS=1.5`. "Ceiling" is every container at its limit at the
same moment; resident is what the stack uses in steady state (the models' figures come from the service READMEs, the
api, sidecar, worker and Redis figures are estimates, GlitchTip's were measured). ocsr is the slowest stage per call, which is
why the small server gives it more replicas than seg or cls. A smaller machine can go further down by arithmetic from the per-replica figures: one of each
model service is about 8 GB resident and 21 GB at every limit (each ocsr replica fewer takes 4 GB off the ceiling and
about 3 GB off the resident total; `$DC up -d --scale worker-fast=0` takes 1 GB off the ceiling). Measure with
`docker stats --no-stream`, and change the replica, thread, CPU and worker lines of `.env` together, then `$DC up -d`:
Compose creates or removes replicas and recreates the services whose settings changed (ocsr needs about 25 s to
load, and many replicas starting at once load their models together: expect a couple of minutes until `$DC ps` shows
everything healthy). Do it when the site is quiet.

**Where the numbers come from.** One maximum job (10 pages, 20 structures) costs about 190 core-seconds, about half of it
in seg and half in ocsr, so 8 cores cannot do better than about 24 s per such job (about 150 an hour), whatever the layout.
The sum of the thread caps is 12 for the small server and 17 for the 8-core one: more than the cores, because the replicas
of different stages are busy in proportion to their share of the work and are not all busy at once. The model is an
estimate (two timings per service fitted with Amdahl's law), not a measurement of the whole stack; the ocsr thread
sensitivity is its weakest input.

**CPU and sharing the host.** `cpus` is a ceiling (a CFS quota), not a reservation, and the limits of all containers sum to
more than the host's cores by design. Every container has `cpu_shares: 512`, half the default weight of 1024: when this stack
and another workload on the same host both want the CPU, the other one gets the larger share (with k of these containers busy
it gets 1024 / (1024 + 512 k) of what they fight over). If the host runs out of memory the kernel takes this stack first:
`oom_score_adj` is 300 (500 for the model services and chem). If something else on the host slows down, lower `SEG_TF_INTRAOP_THREADS` and `SEG_CPUS`, then the ocsr pair.

**Memory limits are ceilings.** A container over its limit is OOM-killed and restarted (`memswap_limit` equals `mem_limit`: no
swap). **Never import TensorFlow in a running model container** (`docker exec <seg|ocsr|cls> python -c "import tensorflow"`): the
second TensorFlow process can push the container over its limit and the kernel kills the service. Measure from outside
(`docker stats --no-stream`, or `/sys/fs/cgroup/memory.current` of the container's cgroup). Do not lower a limit below the worst case in the service README, and **do not raise `SEG_MAX_IMAGE_PIXELS`**
(`OPENCV_IO_MAX_IMAGE_PIXELS` of seg, 40 MP): the API admits pages up to 50 MP, so the worker sends seg a smaller copy of
any page above `SEG_INPUT_MAX_PIXELS` (25 MP) or `SEG_INPUT_MAX_BYTES` (36 MiB encoded) and cuts the crops from the full
page, which means no page reaches seg's cap; the cap stays as a backstop and seg's memory limit is sized for it
(the library keeps one full-page mask per detection). The same worker scales crops above 16 MP for ocsr and cls
(`MODEL_INPUT_MAX_PIXELS`). The other limits that matter: `API_MEM_LIMIT` (1g) and `INGEST_MEM_LIMIT` (4352m, sized from
`INGEST_CONCURRENCY` x `INGEST_MEMORY_MB` plus the molfile children, see `.env.example`), `WORKER_MEM_LIMIT` and
`WORKER_FAST_MEM_LIMIT` (1g).

**The worker's settings** (all in `.env.example`):

| Variable | Small server | 8-core server | Meaning |
|---|---|---|---|
| `SEG_CONCURRENCY`, `OCSR_CONCURRENCY`, `CLS_CONCURRENCY` | 1 | 1 | **Per replica**: calls in flight to one replica, over all jobs of the worker; the worker multiplies by the replica count. Each replica runs one inference at a time and queues a few more, so 1 keeps it fed. Do not set them to the replica counts |
| `WORKER_MAX_JOBS` | 4 | 8 | Jobs running at once, about twice the ocsr slots, so that several jobs' crops interleave on the replicas |
| `RENDER_CONCURRENCY` | 1 | 2 | PDF pages rendered at once (a 50 MP page is about 0.5 GB while it renders) |
| `WORKER_MEM_LIMIT`, `WORKER_CPUS` | 2g, 2 | 3g, 2 | The worker container's limits |

**Small jobs never wait behind big ones.** Inside a worker, a call of a small job (a pasted image, a single image, a PDF of at
most `SMALL_JOB_MAX_PAGES` (1) pages) takes the next free replica slot before the calls of large jobs; after
`SMALL_FIRST_BURST` (4) small calls in a row a waiting call of a large job gets the slot, so a stream of small jobs cannot
starve a PDF. Around the workers, `worker-fast` serves a second arq queue for small jobs, so a small job also does not wait
for a worker place behind 10-page PDFs. It is part of the default start (1 GB). A call already running on a replica is never
interrupted (ocsr: about 3 s, up to 55 s for an image that is not a structure). **Running without `worker-fast` is safe**
(`$DC up -d --scale worker-fast=0` or `$DC stop worker-fast`): the api puts a small job on the fast queue only while a fast
worker is alive (its health-check key is in Redis), and the default worker moves jobs left waiting there to the default queue
within about 90 s. A job that was already running on a dead fast worker is started again only when arq's in-progress key
expires, about 18 minutes later.

## Reverse proxy and TLS

The stack serves plain http. The edge listens on `127.0.0.1:${EDGE_PORT}` (8090) only and sets every header of the app
itself (the CSP with `'wasm-unsafe-eval'`, `no-cache` for `index.html`, a year `immutable` for `/assets/`, `nosniff` and the
rest) plus per-client rate limits and the 413/429/503 answers in the API's JSON error shape. It sends **no HSTS and holds no
certificate**: whoever terminates TLS owns both. Two ways to put your proxy in front:

* **The proxy runs on the same host** and forwards to `http://127.0.0.1:8090`. Connections through the published port come
  from the `public` network's subnet, which the edge always trusts, so the proxy only has to set `X-Forwarded-For` to the
  client. A sketch for nginx (adapt it; certificates and the rest of the server block are yours):

  ```nginx
  client_max_body_size 102m;            # MAX_UPLOAD_MB (100) + 2
  location / {
      proxy_pass http://127.0.0.1:8090;
      proxy_http_version 1.1;
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-For $remote_addr;   # one entry: the client
      proxy_read_timeout 65s;           # PROXY_READ_TIMEOUT_SECONDS (60) + 5
      proxy_request_buffering off;      # the edge buffers an upload once, to disk
      proxy_max_temp_file_size 0;       # stream the export ZIP
      access_log off;                   # see below
  }
  ```

* **The proxy is on another machine** (or a load balancer). Use the **front door**, `deploy/frontdoor/`: a small nginx
  container that publishes host port 8080 (`FRONTDOOR_PORT`, all interfaces: firewall it so that only your proxy reaches
  it) and relays everything to the edge over the `public` network. It adds a flood limit per real client (when `TRUSTED_PROXY_CIDR` lists a proxy), streams uploads,
  answers its own errors in the API's JSON shape, and logs one short line (`<time> <status> <seconds> <upstream status>`)
  only for 429 and 5xx, with no address and no URI. It is a template rendered from your `.env`:

  ```bash
  mkdir -p deploy/frontdoor/conf.d && deploy/frontdoor/render.sh > deploy/frontdoor/conf.d/default.conf
  FD="docker compose -p decimer-frontdoor -f deploy/frontdoor/compose.yaml"
  $FD config -q && $FD up -d
  # after a re-render: docker exec decimer-frontdoor nginx -t && docker exec decimer-frontdoor nginx -s reload
  deploy/frontdoor/check.sh        # the rendered file through `nginx -t` in a throwaway container; needs Docker
  ```

  The front door is a project of its own on purpose: `$DC down` must never take the public port with it. It can also be
  loaded into the `conf.d` of an nginx container you already run (attach that container to `decimer-next_public`; see the
  header of the template and of `check.sh`).

Either way, **forward the whole site** (`/` and `/api/`: the API answers with absolute `/api/...` URLs) **and never forward
to port 8081 or 8091**, GlitchTip's UI listener. Job ids in `/api/` URLs are bearer tokens (a job lives one hour): the edge
replaces them by `:id` in its logs, and your proxy should not keep them in an access log either.

### Reverse proxy and the client address

The per-client limits (10 requests/s for the API, 12 uploads a minute, 2 active jobs per client) count the address the edge
believes is the client. **`TRUSTED_PROXY_CIDR` in `deploy/.env` lists your reverse proxy's addresses** (comma or space
separated), as the edge or the front door sees them. **List proxy addresses, never client networks:** every host inside a
listed network can put any address into `X-Forwarded-For` and so choose the identity its limits are counted against. `/0` is
refused, `render.sh` refuses anything wider than IPv4 `/16` or IPv6 `/48`, and the edge trusts the rightmost entry that is not
a listed proxy. Do not list docker networks: compose adds the `public` subnet for the edge and the `app` subnet for the API
(`PUBLIC_SUBNET`, `APP_SUBNET`; change them only if they collide with a route of the host). Three cases, to settle with
whoever runs the proxy:

1. The proxy sends `X-Forwarded-For: <client>` (appending or replacing what the client sent): the wanted case. With the proxy
   listed, every client has its own limits.
2. It appends to a client-supplied value: still safe, because a client can only write to the left of the proxy's own entry and
   the edge reads from the right and stops at the first address it does not trust.
3. It sends none: every visitor is "the proxy" and shares one limit. Add the header at the proxy.

Left empty, `TRUSTED_PROXY_CIDR` is safe (nobody can choose an identity) but shared. **Check it with two machines:** from A, send
40 quick requests, and from B on another network, at the same moment, one:

```bash
curl -s -w '%{http_code}\n' $(printf -- '-o /dev/null https://your.site/readyz %.0s' $(seq 40)) | sort | uniq -c     # A: some 429s
curl -s -o /dev/null -w '%{http_code}\n' https://your.site/readyz                                                    # B: 200 means B has its own limit
```

If B also gets 429, the proxy is not sending the client address, or is not listed. After changing `TRUSTED_PROXY_CIDR`, recreate
the edge (`$DC up -d edge`) and re-render the front door.

**The visit counter.** The web app loads Matomo, `matomo.nfdi4chem.de` with site id 2, when it is served from a real host
(never from `localhost` or `127.0.0.1`), and the CSP in `nginx/security-headers.conf` allows exactly that host
(`web/src/analytics/matomo.ts`, `web/vite.config.ts`). If you run your own site and do not want its visits counted there,
change the module and the CSP in step (`python3 -I deploy/nginx/check-csp.py` compares the two) before you build.

## Error reports: GlitchTip

Crash reports from the api, the worker and the web app go to a **GlitchTip that runs in this stack** through the standard Sentry
SDKs, so no report leaves your server. It needs only itself and Postgres (one container runs the web server and the task
worker; Postgres is its queue, cache and session store) and takes about 0.5 GB (`glitchtip` 384m, `glitchtip-db` 160m). Both
reporters are **off unless the DSN is set**.

| Who | DSN setting | Goes to |
|---|---|---|
| api, worker, worker-fast | `SENTRY_DSN=http://<key>@glitchtip-gate:8000/<project id>` in `deploy/.env` | the `glitchtip-gate`, an nginx whose whole configuration lets one request through, `POST /api/<id>/envelope/`, so a compromised api or worker cannot reach GlitchTip's login, API or admin |
| web app | `VITE_SENTRY_DSN=https://<key>@<your host>/glitchtip/<project id>`, a build argument of the edge image | the site's own origin: the edge passes `POST /glitchtip/api/<id>/envelope/` to GlitchTip, so the CSP's `connect-src 'self'` is unchanged. A DSN with another origin is refused by the app at start-up |

**What a report carries.** The scrubbers (`api/src/decimer_api/scrub.py`, `web/src/observability/scrub.ts`) are an allowlist:
an event is rebuilt from the exception types, the stack as function names and line numbers, the SDK and runtime versions, a few
tags, and the method and path of the request. Request bodies, query strings, cookies, headers, users, IP addresses, breadcrumbs
and local variables are dropped, and free text that is kept has molfiles, file names, job ids, IP addresses and SMILES strings
replaced (a pattern match, best effort). **GlitchTip never gets an address:** it would read one from `X-Forwarded-For`, `X-Real-IP`,
`Forwarded` and others, so the edge's `/glitchtip/` location passes GlitchTip exactly four headers (`Connection`, `Host`,
`Content-Type`, `Content-Length`), and every project has "scrub IP addresses" on. The browsers' endpoint answers 404 for every
project but `GLITCHTIP_WEB_PROJECT_ID`, 403 for any other method, 415 for a `Content-Encoding`, 413 above 64 KB and 429 above its
rate limits.

**First start** (once). GlitchTip is open to registration while it has no account, so the first account is made *before* its web
server starts: the `glitchtip` service runs the migrations and `glitchtip-init.py` (the superuser of `GLITCHTIP_ADMIN_EMAIL` /
`GLITCHTIP_ADMIN_PASSWORD`, the organisation `decimer`, the projects `decimer-api` and `decimer-web`, "scrub IP addresses" on) and
only then the server; if the init fails the container stops. It is safe on every start and never adds a second account.

```bash
$DC up -d glitchtip-db glitchtip glitchtip-gate   # about a minute, then healthy
$DC logs glitchtip | grep DSN                     # project ids and keys (decimer-api is 1 and decimer-web 2 on a fresh database)
ssh -L 8091:127.0.0.1:8091 <your server>          # from your workstation; open http://127.0.0.1:8091/ and log in
```

Put the DSNs into `deploy/.env`, recreate and rebuild:

```bash
SENTRY_DSN=http://<decimer-api key>@glitchtip-gate:8000/<decimer-api id>
VITE_SENTRY_DSN=https://<decimer-web key>@your.site/glitchtip/<decimer-web id>   # for a local test: http://<key>@127.0.0.1:8090/glitchtip/<id>
GLITCHTIP_WEB_PROJECT_ID=<decimer-web id>         # the only project the edge lets a browser report to
$DC up -d api worker worker-fast edge             # recreated with the settings
$DC build edge && $DC up -d edge                  # the web app carries its DSN from now on (a build-time setting)
# a test report from the api:
$DC exec api python -c "import logging, os, sentry_sdk; from decimer_api.observability import init_error_reporting; init_error_reporting(os.environ['SENTRY_DSN'], service='api', environment='check'); logging.getLogger('check').error('deploy check: a test report'); sentry_sdk.flush(5)"
```

**Operating it.** The UI is only on `127.0.0.1:${GLITCHTIP_PORT}` (8091, the edge's second listener,
`nginx/glitchtip-ui.conf`); keep `GLITCHTIP_DOMAIN` in step with the address your browser uses (the port of the ssh tunnel). Events are
kept `GLITCHTIP_RETENTION_DAYS` (90). GlitchTip has no route out, so **it sends no alert e-mails** (`EMAIL_URL=consolemail://`): look
at the UI, or give it a mail relay yourself (a network with that route for GlitchTip alone). To update it, put a newer tag and
digest in `compose.yaml`, `$DC pull glitchtip glitchtip-db` and `$DC up -d glitchtip`; migrations run at start. To reset it:
`$DC rm -sf glitchtip glitchtip-db && docker volume rm decimer-next_glitchtip-pgdata`.

## Logs and retention

* **api, worker and sidecar** write one JSON object per line (`time`, `level`, `logger`, `message`, `exc` for a traceback):
  `docker logs <container> | jq .`. `LOG_FORMAT=text` and `LOG_LEVEL` are in `.env.example`. Messages go through the same
  scrubber as error reports: no SMILES, molfile, file name or public IP address (private and loopback addresses stay, which is how a
  proxy missing from `TRUSTED_PROXY_CIDR` shows). Job ids stay in the logs (the way to follow one job; they stop working an hour
  after it ends). uvicorn's access log is off, so the api writes no client address. Every container rotates its log at 5 x 10 MB:
  by size, not by age.
* **The edge's access log** (`docker logs decimer-next-edge-1`) has no client address, no user agent, no referrer, no query string,
  and job ids replaced by `:id`.
* **The edge's error log is the one place the edge writes an address.** Any server error can be logged there (failed proxying, an
  upload over the limit, a media file that cannot be opened), and a line holds the client address, the request line (so a job id),
  the host and the referrer. It is kept **seven days at most**, and docker's log driver cannot promise that (it bounds size, not
  age), so nginx writes it to a file on the `edge-logs` volume and `nginx/edge-logs.sh` rotates it daily (`error.log.YYYY-MM-DD`,
  UTC) and deletes files when their day is a week behind. It runs before nginx starts, beside it every ten minutes, and from the
  image's `HEALTHCHECK` every 15 s (`edge-logs.sh enforce`: rotate, then check), so the edge is `unhealthy` only for what rotating
  cannot mend. **Keep the edge's healthcheck.** Read it with `docker exec decimer-next-edge-1 tail -n 50 /var/log/nginx/error.log`; do
  not copy it anywhere that keeps it longer than seven days and do not add an `error_log stderr` (that puts the addresses into
  docker's log). If you change the retention, change `EDGE_LOG_KEEP_DAYS` (default 7) and your privacy text together.
* **A stopped edge prunes nothing.** After `$DC down` or `stop` the `edge-logs` volume ages: its lines are deleted at the next start,
  not at seven days. For a stop of more than a few days, prune from the host (it runs the same script as the volume's owner):

  ```bash
  docker run --rm --user 101:101 --read-only -e EDGE_LOG_DIR=/l -v decimer-next_edge-logs:/l \
    --entrypoint /usr/local/bin/edge-logs.sh localhost/decimer-next/edge:local rotate
  ```

  or delete the volume (`docker volume rm decimer-next_edge-logs`, with the edge stopped). `docker exec decimer-next-edge-1
  edge-logs.sh check` exits 0 when the retention is being kept and says why when not. Your own proxy and front door have logs of
  their own that this does not govern.
* **Leftover uploads:** the edge empties `/var/cache/nginx` when it starts, so the buffered body of a request cut off by a crash
  does not outlive the process.

**Usage counts.** Per UTC day: jobs accepted by kind (`pdf`, `image`, `paste`) and size (`small`, `large`), jobs finished `done` and
`failed`, the pages and structures of the jobs that finished `done`, and refused uploads by error code (`queue_full` and
`rate_limited` are the capacity signals). **No address, no job id, no file name, no time of day.** They live in the `usage` Redis
(an append-only file on the `usage-data` volume, so a restart keeps them) for `USAGE_TTL_DAYS` (800) and are counted best effort: a
Redis that is down costs a warning and that count, never an upload. There is deliberately no HTTP route for them:

```bash
$DC exec api python -m decimer_api.usage --days 30         # a table: one row per day and a total
$DC exec api python -m decimer_api.usage --days 7 --json   # the same as JSON
```

## Uptime check

`deploy/uptime/uptime-check.sh [BASE_URL]` probes the public site the way a visitor's browser would, every minute from cron or a
systemd timer, and alerts through a hook when it fails and again when it recovers. It does not upload anything (the golden check is the
deep check). Probes: `/readyz` (200 and `{"status":"ready"}`: the proxy and edge reach the API, which reaches Redis and the job
volume), `/` (the app's HTML) and `/api/engines` (a real route under `/api/`, so the proxying of `/api/` itself is covered). It
alerts after `FAILS_BEFORE_ALERT` failed runs in a row (2), reminds every `REMIND_MINUTES` (60) and says when the site is back. An
`UPTIME_MODE=legacy` set of probes exists for the earlier Laravel version of decimer.ai (`auto` detects it); ignore it.

```bash
mkdir -p ~/.local/state/decimer-uptime ~/.config/decimer-uptime
cp deploy/uptime/alert-hook.example.sh ~/.config/decimer-uptime/alert-hook.sh      # keep ONE branch (mail through an SMTP relay, ntfy, a chat webhook); chmod 700
deploy/uptime/selftest.sh                                                          # the check's own logic against a stub server
crontab -e     # add:   * * * * *  ALERT_CMD=$HOME/.config/decimer-uptime/alert-hook.sh ERROR_DASHBOARD_URL=<GlitchTip project URL> /opt/decimer/deploy/uptime/uptime-check.sh https://your.site >> $HOME/.local/state/decimer-uptime/cron.log 2>&1
```

A systemd timer is the alternative: `decimer-uptime.service` and `.timer` in the same directory (edit `User=` and the `ExecStart` path; the
settings go in `/etc/decimer/uptime.env`, see `uptime.env.example`). The alert channel is a hook because it differs per site:
`ALERT_CMD` is run with the event (`down`, `reminder`, `recovered`) and the subject, the text on stdin; `ALERT_EMAIL` uses `mail` if the
machine has it. **A check that runs on the server cannot see the server, your proxy or the network being down**: set `HEARTBEAT_URL`
to a dead-man's-switch service (healthchecks.io style: it expects a ping after every successful run and alerts when it stops), or
probe `/readyz` from another machine. `ERROR_DASHBOARD_URL` is added to every alert.

## Updates and rollback

1. Build with a **new release tag** (`DECIMER_TAG=2026-11-03` in `deploy/.env`): `$DC build --no-cache`. Never reuse a tag; the previous
   images are the rollback. `DECIMER_TAG` is one tag for every image of the project.
2. `$DC up -d` recreates what changed (nothing is rebuilt or pulled). After the `api` container is recreated nothing else needs a restart:
   the edge and the front door look the name up per request. A Redis restart drops the queue and every job record, so running jobs
   are lost (their clients see 404 and upload again), and so do the jobs a restarted worker was running: update when the site is quiet.
3. A change to a model, TensorFlow, DECIMER, RDKit, CDK or the PDF renderer needs the golden check (`golden/smoke.sh`) before it reaches users.
4. **Roll back** by setting `DECIMER_TAG` to the earlier tag and running `$DC up -d`: about a minute (ocsr loads for about 25 s), because
   `pull_policy: never` means nothing is built or pulled. Do not `docker image prune -a` or `docker system prune`: they delete the earlier
   tags. `$DC down` keeps the volumes; **`$DC down -v` also deletes the job and report volumes**.

**Rotate a secret** by editing `deploy/.env`: `IP_HASH_SECRET` (`$DC up -d api`; per-client counters reset), `REDIS_PASSWORD` (`$DC up -d`: Redis,
api, worker, worker-fast and sweep are recreated together; the queue is lost). If `deploy/.env` may have been read by someone else, rotate both.

## Backups and retention

| Volume | Holds | Backup |
|---|---|---|
| `decimer-next_jobs` | job files, swept an hour after a job ends (`sweep`) | none: jobs live one hour |
| `decimer-next_reports` | problem reports: private (the api alone mounts it), deleted by hand once analysed and by the api when older than `REPORTS_MAX_AGE_DAYS` (364; start-up, then hourly) | **none, on purpose**: a deleted report must not live on in a backup |
| `decimer-next_edge-cache` | nginx's temporary files for buffered uploads, emptied at every edge start | none |
| `decimer-next_edge-logs` | the edge's error log and at most six rotated days (client addresses) | none |
| `decimer-next_usage-data` | the usage counter's append-only file, a few KB a day for 800 days | **yes**: the only history here that cannot be recomputed |
| `decimer-next_glitchtip-pgdata` | GlitchTip's Postgres, the error reports of the last 90 days (scrubbed) | none; `docker volume rm` starts a fresh GlitchTip |
| `decimer-next_ingest-scratch` | the sidecar's request in flight, emptied after every request | none |

Also copy `deploy/.env` (it is in no repository) and note your `TRUSTED_PROXY_CIDR`, the replica lines and `DECIMER_TAG`. Images are rebuilt
from the tagged source. To read the problem reports: `docker run --rm -v decimer-next_reports:/r:ro busybox tar -C /r -cf - .`

## How the stack is isolated

Keep these properties when you change `compose.yaml`; `check-compose.py` checks most of them.

* **Networks.** `public` is the edge alone, with the published ports (docker cannot publish from an internal network, so it is the one
  network with a route out). Everything else is `internal`: `app` (edge and api, pinned subnet), `backend` (redis, usage, api, worker,
  sweep), `models` (the worker and the model services; **the api is not on it**), `chem-api` (the api and chem alone: the IUPAC name of an
  edited structure), `ingest` (the api and the sidecar alone) and three for GlitchTip (`glitchtip-sdk`, `glitchtip-edge`, `glitchtip-db`).
  **No two containers that call each other share more than one network**: with two shared networks docker's DNS could answer `api` with
  the address on the other one, and the api would no longer see its proxy on `app`. The model services, Redis, the sidecar, GlitchTip and
  the sweep have no route to the internet.
* **One api process.** One `api` container, no `--workers`, no `--scale`, no replicas: the ingest gate and the queue throttle are per
  process. The api starts with an exec-form `CMD` so that python is PID 1 and no shell holds a second copy of the environment (the api makes
  itself non-dumpable); that is why `init: true` is not set for it.
* **The ingest sidecar.** Uploads (untrusted PDFs and images through pdfium, libheif and Pillow) and client molfiles are parsed in the
  `ingest` container, which has no job data, no route to Redis, chem, the edge, GlitchTip or the internet, no secret, a read-only root and a
  private scratch volume. A compromised parser sees the one other request in flight instead of every user's files. The api is not ready
  while the sidecar does not answer. `INGEST_SIDECAR_URL=` (empty) with `API_MEM_LIMIT=4608m` switches back to children of the api (for
  development).
* **Redis** has no persistence (`--save "" --appendonly no`, 256 MB, `noeviction`; every key has a TTL of at most 6 h), which is what keeps
  the one-hour retention promise. It has a password, handed to it on stdin (argv is readable by every user of the host) and to `redis-cli`
  through `REDISCLI_AUTH`, and runs as the `redis` user without capabilities. `IP_HASH_SECRET` reaches the api only (the others blank it).
* **Process settings.** `no-new-privileges`, `cap_drop: ALL`, a read-only root file system with a RAM `/tmp` for seg, cls, ocsr, chem, Redis,
  the edge, the sidecar and the gate (if a service stops with "Read-only file system", add the path to `tmpfs`; the api is writable on
  purpose, starlette spools big uploads to `/tmp`), services as uid 10001 and the edge as nginx (101). The edge buffers uploads to the
  disk-backed `edge-cache` volume, not to RAM.
* **Trusted proxies, one variable per hop.** The API trusts exactly `APP_SUBNET` (the edge is its peer); the edge trusts `PUBLIC_SUBNET` plus
  `TRUSTED_PROXY_CIDR`. Proxy addresses, never client networks.
* `PROXY_READ_TIMEOUT_SECONDS` and `MAX_UPLOAD_MB` are read by both the edge and the API: the API refuses to start when
  `INGEST_WAIT_SECONDS` + the child deadline + 5 s reaches the proxy timeout, and nginx's body limit is `MAX_UPLOAD_MB` + 1 MB.

## Notes on the images

* **Base images are pinned by tag and digest** (`python:3.11.17-slim-trixie`, `python:3.12.15-slim-trixie`, `eclipse-temurin:21.0.12.1_1-jre-noble`,
  `node:24.22.1-trixie-slim`, `nginx:1.31.8-alpine3.24`, `redis:8.10.2-alpine3.23`, `ghcr.io/astral-sh/uv:0.13.5`): update tag and digest together.
  Trixie rather than bookworm because the wheels are manylinux and only the support window matters. All builds are multi-stage with no build
  tools in the final stage, and `uv sync --locked --no-dev --no-editable` installs from each project's `uv.lock`.
* **Weights and jars are baked in and checked.** `weights/SHA256SUMS` lists every file and `weights/verify.sh --strict` fails the build on a wrong
  hash, a missing, extra or symlinked file, a dot-file or an empty directory. cls is checked against its pip package's `model/` directory.
  ocsr bakes both DECIMER models (about 650 MB on disk, shared by all replicas) and loads only the printed one; dropping the hand-drawn weights
  saves 348 MB of disk (`SHA256SUMS` lists what to remove).
* **No PyMuPDF in any image** (it is AGPL): `patches/apply-pyproject-patches.sh` fails if `uv export --no-dev` of any service lists it, and
  `seg.Dockerfile` fails the build if it is importable. The services use `opencv-python-headless` and pinned build backends
  (`[tool.uv] build-constraint-dependencies`); those `pyproject.toml` changes are already in the repository and `patches/` documents them. The
  script re-checks them, and says "already applied".
* **Health checks** hit `/readyz` (a real inference) for the model services and the API, `/healthz` for the edge, the arq health-check key in
  Redis for the workers (`worker_healthcheck.py`) and nothing for the sweep. seg and ocsr probe every 120 s when running (an idle service pays
  its selftest on each probe) and every 5 s while starting, so `depends_on: condition: service_healthy` does not wait a whole interval. The
  worker waits for every replica of seg, cls and ocsr and only for chem to have *started*: a job without chem has no IUPAC name, one warning,
  and a few seconds of waiting.
* **chem** runs `CHEM_JARS_DIR=/opt/orthonym/jars` (OPSIN, centres and `cdk-2.8.jar`, root-owned, re-checked by the service at start and never
  downloaded). The worker sends the SMILES that RDKit refuses to chem's `POST /cdk` (`CDK_FALLBACK`, default `true`); a structure only CDK
  accepts is shown as "accepted by CDK only" and the CSV's `valid` column is `true|cdk|false`.
* `pull_policy: never`, not `build`: `build` would rebuild on every `up` and overwrite the tag a rollback goes back to. The price is that you
  build first (`$DC build`): `never` with a missing image is an error, not a build.

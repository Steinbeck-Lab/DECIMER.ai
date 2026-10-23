# decimer.ai job API, worker and TTL sweep: ONE image, three commands. Python 3.12, no
# TensorFlow, no JVM.
#
# The build context is the REPOSITORY ROOT:
#   docker build -f deploy/api.Dockerfile -t localhost/decimer-next/api:local .
#
# Commands (compose.yaml sets the last two; the image's own CMD is the API):
#   python -m decimer_api                      the API (uvicorn, ONE process, see below)
#   python -m decimer_api.worker               the arq worker
#   python -m decimer_api.sweep --every 300    the retention sweep
#
# The API must run as exactly ONE process (never uvicorn --workers N, never two api containers): the
# ingest gate and the queue throttle are per process (api/README.md). The CMD is the exec form with
# python as PID 1, with no shell and no init wrapper in between: the API makes itself non-dumpable
# (prctl) so that its ingest children, which run as the same user, cannot read its environment
# through /proc/<pid>/environ, and a wrapper process holding the same environment would not be
# non-dumpable. compose.yaml therefore does not set `init: true`.
#
# Base image: python 3.12 on Debian 13 (trixie); wheels only (pillow-heif and pypdfium2 bundle
# libheif and pdfium, rdkit bundles most of its libraries). The one exception: rdkit's drawing
# module (rdMolDraw2D, used for the result SVGs) links against libXrender, libXext, libX11 and
# libexpat from the system (ldd over the venv finds nothing else), so the final stage installs those.

ARG PYTHON_IMAGE=python:3.12.15-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff

FROM ${UV_IMAGE} AS uv

# ---- builder ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src/api
COPY api/pyproject.toml api/uv.lock api/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --no-install-project
COPY api/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- runtime ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="decimer-api" \
      org.opencontainers.image.description="decimer.ai job API, arq worker and TTL sweep"

# uid/gid 10001. /data/jobs is DATA_DIR (the job volume, shared with the worker, the sweep and, read
# only, the edge) and /data/reports is the private problem-report volume. A new named
# volume takes the ownership of these directories on its first mount.
# Unpinned like the other images: the versions come from Debian trixie at build time.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends libxrender1 libxext6 libx11-6 libexpat1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent \
               --shell /usr/sbin/nologin app \
    && mkdir -p /data/jobs /data/reports \
    && chown 10001:10001 /data/jobs /data/reports \
    && chmod 755 /data/jobs \
    && chmod 700 /data/reports

COPY --from=builder /opt/venv /opt/venv
COPY deploy/healthcheck.py deploy/worker_healthcheck.py /usr/local/bin/

# HOST: python -m decimer_api listens on 127.0.0.1 unless told otherwise; in a container compose's
# network is the access control (only the edge reaches port 8000, nothing is published).
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATA_DIR=/data/jobs \
    HOME=/tmp

USER 10001:10001
# Smoke test at build time: the libraries that bundle native code import on this base.
RUN python -c "import decimer_api, decimer_api.worker.arq_app, rdkit.Chem, pypdfium2, pillow_heif, arq; print('ok')" \
    && find /tmp -mindepth 1 -delete

EXPOSE 8000
# /readyz: Redis answers and DATA_DIR is writable. compose turns the check off for the worker and the
# sweep (the worker has its own, worker_healthcheck.py).
HEALTHCHECK --interval=15s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "/usr/local/bin/healthcheck.py", "8000", "/readyz", "5"]
CMD ["python", "-m", "decimer_api"]

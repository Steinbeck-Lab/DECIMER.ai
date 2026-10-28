# DECIMER Image Classifier model service. Python 3.11, TensorFlow 2.15.1, CPU only.
#
# The build context is the REPOSITORY ROOT (path dependency on services/common):
#   docker build -f deploy/cls.Dockerfile -t localhost/decimer-next/cls:local .
#
# Nothing to fetch: the classifier weights ship inside the decimer-image-classifier package (git
# commit 146b00b, pinned in services/cls/uv.lock). The build checks the four installed model files
# against the cls/ lines of deploy/weights/SHA256SUMS and fails on any difference, extra file or
# missing file. The image needs no network. The build does (PyPI, GitHub).
#
# Base image: see seg.Dockerfile (python 3.11 on Debian 13 trixie, tag and digest pinned).
# No OpenCV in this service's lock file, so no opencv question here. Before building, apply
# deploy/patches/apply-pyproject-patches.sh to services/cls: it pins the build backend (setuptools) of
# the decimer-image-classifier git dependency, which is built from source during the install. For
# release builds see the --no-cache note in deploy/README.md.

ARG PYTHON_IMAGE=python:3.11.17-slim-trixie@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff

FROM ${UV_IMAGE} AS uv

# ---- builder ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /uvx /bin/
# git: decimer-image-classifier is a git dependency. Builder stage only.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src/services/cls
COPY services/common /src/services/common
COPY services/cls/pyproject.toml services/cls/uv.lock services/cls/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --no-install-project
COPY services/cls/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- runtime ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="decimer-cls" \
      org.opencontainers.image.description="DECIMER Image Classifier model service (decimer.ai)"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent \
               --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv
COPY deploy/healthcheck.py /usr/local/bin/healthcheck.py

# The weights check, on what actually ships: the installed package's model/ directory.
COPY deploy/weights/verify.sh deploy/weights/SHA256SUMS /tmp/verify/
RUN sh /tmp/verify/verify.sh cls /opt/venv/lib/python3.11/site-packages/decimer_image_classifier /tmp/verify/SHA256SUMS \
    && rm -rf /tmp/verify

# TF_NUM_*: the classifier is small; 2 + 1 gives about 100 ms per crop (services/cls/README.md).
# MALLOC_ARENA_MAX=2: the service's resident set grows 57-100 KB per request (README "Memory", not
# explained). Fewer glibc arenas keeps the growth from also being fragmentation. The 2.5 GB limit and the
# restart policy are the safety net for that growth.
# HOME is /tmp, a tmpfs under compose's read_only (Keras makes ~/.keras on import).
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TF_NUM_INTRAOP_THREADS=2 \
    TF_NUM_INTEROP_THREADS=1 \
    MALLOC_ARENA_MAX=2 \
    HOME=/tmp \
    PORT=8003

USER 10001:10001
# Smoke test at build time, as the service user: imports only (the service's production wiring,
# decimer_cls.app, and TensorFlow), no model load; then nothing in the model directory may be
# writable for the service user (the files are root-owned, as the package installer wrote them).
RUN python -c "import decimer_cls.app, decimer_service, tensorflow as tf; print('tensorflow', tf.__version__)" \
    && [ -z "$(find /opt/venv/lib/python3.11/site-packages/decimer_image_classifier/model -writable)" ] \
    && find /tmp -mindepth 1 -delete

EXPOSE 8003
HEALTHCHECK --interval=30s --timeout=20s --start-period=90s --retries=3 \
    CMD ["python", "/usr/local/bin/healthcheck.py", "8003", "/readyz", "15"]
CMD ["python", "-m", "decimer_cls"]

# DECIMER Image Transformer (OCSR) model service (TODO, service TODO). Python 3.11,
# TensorFlow 2.15.1, decimer 2.8.0, CPU only.
#
# The build context is the REPOSITORY ROOT (path dependency on services/common). Fetch the weights
# first (deploy/weights/fetch.sh ocsr), then:
#   docker build -f deploy/ocsr.Dockerfile -t localhost/decimer-next/ocsr:local .
#
# Weights: `import DECIMER` loads TWO SavedModels (DECIMER_model, and DECIMER_HandDrawn_model, which
# the service never calls) from $PYSTOW_HOME/DECIMER-V2/ and downloads whatever is missing, so BOTH are
# baked in (about 650 MB) and checked against the ocsr/ lines of deploy/weights/SHA256SUMS with
# `verify.sh --strict` (exactly those nine files, nothing else). Owner decision D15: both models stay
# in the image. That costs 1.9 GB of memory and 26 s of start-up (services/ocsr/README.md), and is why the
# limit is 6 GB and the health check waits; reopen it only if the VM gets short of memory (then make the
# hand-drawn load lazy upstream in DECIMER). There are no .model_url marker files on purpose: without
# them decimer 2.8.0 never re-downloads.
#
# Before building, apply deploy/patches/apply-pyproject-patches.sh to services/ocsr (opencv-python-
# headless instead of opencv-python; without it build with OPENCV_GUI_LIBS=1).
#
# Base image: see seg.Dockerfile (python 3.11 on Debian 13 trixie, tag and digest pinned).

ARG PYTHON_IMAGE=python:3.11.17-slim-trixie@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff

FROM ${UV_IMAGE} AS uv

# ---- weights: the build context's copy, verified ---------------------------------------------
FROM ${PYTHON_IMAGE} AS weights
WORKDIR /w
COPY deploy/weights/verify.sh deploy/weights/SHA256SUMS ./
COPY deploy/weights/ocsr/ ./ocsr/
RUN sh verify.sh --strict ocsr /w/ocsr /w/SHA256SUMS

# ---- builder ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /uvx /bin/
# git: not needed for decimer (a PyPI package), but harmless and keeps the three model builders alike.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src/services/ocsr
COPY services/common /src/services/common
COPY services/ocsr/pyproject.toml services/ocsr/uv.lock services/ocsr/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --no-install-project
COPY services/ocsr/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- runtime ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="decimer-ocsr" \
      org.opencontainers.image.description="DECIMER Image Transformer (OCSR) model service (decimer.ai)"

# opencv-python needs libGL.so.1 and libglib2.0-0 at import time (decimer imports cv2). The intended
# fix is the headless wheel (deploy/patches/ocsr-pyproject.patch, applied to services/ocsr). Until it
# is applied, build with --build-arg OPENCV_GUI_LIBS=1. The smoke test below fails the build if cv2
# does not import.
ARG OPENCV_GUI_LIBS=0
# hadolint ignore=DL3008
RUN if [ "$OPENCV_GUI_LIBS" = "1" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
        && rm -rf /var/lib/apt/lists/*; \
    fi \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent \
               --shell /usr/sbin/nologin app

# Root-owned and read-only for the service. pystow writes README.md into its home directory at import
# when the file is missing (a PermissionError on a read-only /models), so the file is there already.
# Everything is made world-readable (the build context's permissions are whatever the host's umask gave)
# and a check as the service user below proves it.
COPY --from=weights /w/ocsr/ /models/
RUN printf '%s\n' 'Model weights baked into the image at build time (deploy/weights/SHA256SUMS).' > /models/README.md \
    && chmod -R a+rX /models

COPY --from=builder /opt/venv /opt/venv
COPY deploy/healthcheck.py /usr/local/bin/healthcheck.py

# PYSTOW_HOME: decimer reads $PYSTOW_HOME/DECIMER-V2/{DECIMER_model,DECIMER_HandDrawn_model}.
# OPENCV_IO_MAX_IMAGE_PIXELS: the service decodes with Pillow (at most 17.5 MP) and hands arrays to
# DECIMER, so this is a second guard only; same value as seg.
# TF_NUM_*: 4 + 1 (compose limits the container to 4 CPUs); tune with measurements.
# OCSR_ALLOW_WEIGHT_DOWNLOAD must stay unset: the service has no network and must fail loudly.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYSTOW_HOME=/models \
    OPENCV_IO_MAX_IMAGE_PIXELS=40000000 \
    TF_NUM_INTRAOP_THREADS=4 \
    TF_NUM_INTEROP_THREADS=1 \
    HOME=/tmp \
    PORT=8002

USER 10001:10001
# Smoke test at build time: imports only (decimer itself would load both models), no network.
RUN python -c "import decimer_ocsr, decimer_service, cv2, tensorflow as tf; print('tensorflow', tf.__version__, 'cv2', cv2.__version__)" \
    && test -r /models/README.md \
    && find /models -type f -exec head -c 1 {} + > /dev/null \
    && test -r /models/DECIMER-V2/DECIMER_model/saved_model.pb \
    && test -r /models/DECIMER-V2/DECIMER_HandDrawn_model/saved_model.pb \
    && find /tmp -mindepth 1 -delete

EXPOSE 8002
# /readyz reads a real structure (the selftest compares the SMILES; 6 s the first time, a few seconds
# after). Start-up is about 50 s (both models load; services/ocsr/README.md), and longer when the box
# is busy: 180 s of start period. Once running, a check every 120 s (an idle service pays the selftest on
# each one); during start-up one every 5 s, so depends_on service_healthy does not wait a whole interval.
# --start-interval needs Docker 25 or newer (28.4 on the VM).
HEALTHCHECK --interval=120s --timeout=40s --start-period=180s --start-interval=5s --retries=3 \
    CMD ["python", "/usr/local/bin/healthcheck.py", "8002", "/readyz", "35"]
CMD ["python", "-m", "decimer_ocsr"]

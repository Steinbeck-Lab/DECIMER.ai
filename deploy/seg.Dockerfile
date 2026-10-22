# DECIMER Segmentation model service. Python 3.11, TensorFlow 2.15.1, CPU only.
#
# The build context is the REPOSITORY ROOT: the service has a path dependency on services/common.
# Fetch the weights first (deploy/weights/fetch.sh seg), then:
#   docker build -f deploy/seg.Dockerfile -t localhost/decimer-next/seg:local .
#
# The build needs the network (PyPI, GitHub for the pinned decimer-segmentation commit); the image
# needs none: the weights are baked in and checked against deploy/weights/SHA256SUMS (`verify.sh
# --strict`: the directory must be exactly the listed files), so a corrupted, swapped or extra file
# fails the build. This file only reads deploy/weights/seg/ (see seg.Dockerfile.dockerignore for
# everything else that reaches the build).
#
# Before building, apply deploy/patches/apply-pyproject-patches.sh to services/seg: it switches to
# opencv-python-headless and pins the build backend of the decimer-segmentation git dependency
# (setuptools, wheel). Without it the build still works (OPENCV_GUI_LIBS=1) but runs an unpinned backend.
#
# Base image: python 3.11 on Debian 13 (trixie). The wheels are manylinux, so the distribution only
# matters for its support window: bookworm is in LTS since 2026-06, trixie has regular security
# updates until 2028. Both the tag and the digest are pinned; update them together.

ARG PYTHON_IMAGE=python:3.11.17-slim-trixie@sha256:cce12316475d7d56d605f76e1d811d0dc4e7227f2d4afd29951521264f504ff8
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff

FROM ${UV_IMAGE} AS uv

# ---- weights: the build context's copy, verified ---------------------------------------------
FROM ${PYTHON_IMAGE} AS weights
WORKDIR /w
COPY deploy/weights/verify.sh deploy/weights/SHA256SUMS ./
COPY deploy/weights/seg/ ./seg/
RUN sh verify.sh --strict seg /w/seg /w/SHA256SUMS

# ---- builder: the virtualenv, from services/seg/uv.lock ----------------------------------------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /uvx /bin/
# git: decimer-segmentation is a git dependency. Builder stage only; nothing from it ships.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src/services/seg
COPY services/common /src/services/common
COPY services/seg/pyproject.toml services/seg/uv.lock services/seg/README.md ./
# --no-editable: the project and the path dependency (../common) are installed into the venv, not
# linked to the source tree, which does not exist in the final image.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --no-install-project
COPY services/seg/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- runtime ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="decimer-seg" \
      org.opencontainers.image.description="DECIMER Segmentation model service (decimer.ai)"

# opencv-python needs libGL.so.1 and libglib2.0-0 at import time. The intended fix is the headless
# wheel (deploy/patches/seg-pyproject.patch, applied to services/seg): no apt packages, no X11 or
# mesa libraries in the image. Until that patch is applied, build with
#   --build-arg OPENCV_GUI_LIBS=1
# to install the two packages instead. The smoke test below fails the build if cv2 does not import.
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

# The weights, root-owned and read-only for the service. pystow writes a README.md into its home
# directory when it is imported and the file is missing, which a read-only /models would turn into
# a PermissionError at import: so the file is there already. Everything is made world-readable
# (the build context's permissions are whatever the host's umask gave), and a check as the service
# user below proves it.
COPY --from=weights /w/seg/ /models/
RUN printf '%s\n' 'Model weights baked into the image at build time (deploy/weights/SHA256SUMS).' > /models/README.md \
    && chmod -R a+rX /models

COPY --from=builder /opt/venv /opt/venv
COPY deploy/healthcheck.py /usr/local/bin/healthcheck.py

# PYSTOW_HOME: where decimer_segmentation looks for DECIMER-Segmentation_model/mask_rcnn_molecule.h5.
# OPENCV_IO_MAX_IMAGE_PIXELS: the service's own default, written out; do NOT raise it. The API admits
# pages up to 50 MP (MAX_IMAGE_PIXELS), but seg answers 422 above this value, because the library keeps
# one full-page mask per detection (services/seg/README.md). The worker is to downscale pages for seg
# (TODO: that code is a dependency of this setting, see deploy/README.md).
# TF_NUM_*: 4 + 1 (compose limits the container to 4 CPUs); tune with measurements.
# HOME and MPLCONFIGDIR point at /tmp, a tmpfs under compose's read_only.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYSTOW_HOME=/models \
    OPENCV_IO_MAX_IMAGE_PIXELS=40000000 \
    TF_NUM_INTRAOP_THREADS=4 \
    TF_NUM_INTEROP_THREADS=1 \
    HOME=/tmp \
    MPLCONFIGDIR=/tmp/matplotlib \
    PORT=8001

# Smoke test at build time: the venv runs on this base (no missing system library), and numpy,
# TensorFlow and cv2 import. No model is loaded, nothing needs the network. decimer_seg first: it
# sets the OpenCV pixel limit before cv2 is imported.
USER 10001:10001
RUN python -c "import decimer_seg, decimer_service, cv2, tensorflow as tf; print('tensorflow', tf.__version__, 'cv2', cv2.__version__)" \
    && test -r /models/DECIMER-Segmentation_model/mask_rcnn_molecule.h5 \
    && test -r /models/README.md \
    && find /models -type f -exec head -c 1 {} + > /dev/null \
    && find /tmp -mindepth 1 -delete

EXPOSE 8001
# /readyz runs a real inference (a small page with one structure; about 1 to 4 s of CPU): the cached
# pass lasts 30 s, so an idle service would pay that on every check. Hence a check every 120 s once
# running, and a fast one (every 5 s) during start-up, so that depends_on service_healthy does not wait
# a whole interval after the model is up (about 10 s). --start-interval needs Docker 25 or newer
# (28.4 on the VM); an older engine would reject this line.
HEALTHCHECK --interval=120s --timeout=25s --start-period=120s --start-interval=5s --retries=3 \
    CMD ["python", "/usr/local/bin/healthcheck.py", "8001", "/readyz", "20"]
CMD ["python", "-m", "decimer_seg"]

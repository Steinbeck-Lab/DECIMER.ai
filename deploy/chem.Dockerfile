# DECIMER chem service: IUPAC names from Orthonym and the CDK 2.8 layout
# fallback (TODO, D4). Python 3.12, an Eclipse Temurin 21 JRE, Orthonym's two jars (OPSIN, centres)
# and the CDK 2.8 jar. The only image with a JVM. No TensorFlow.
#
# The build context is the REPOSITORY ROOT. Fetch the jars first (deploy/weights/fetch.sh chem), apply
# deploy/patches/apply-pyproject-patches.sh to services/chem (it pins the build backend of the Orthonym
# git dependency: hatchling), then:
#   docker build -f deploy/chem.Dockerfile -t localhost/decimer-next/chem:local .
#
# The build needs the network (PyPI, GitHub for Orthonym's pinned commit); the image needs none. The
# jars are baked in and checked twice: here against deploy/weights/SHA256SUMS (`verify.sh --strict`), and
# by the service itself (Orthonym's own pinned hashes for OPSIN and centres, decimer_chem.cdk's pin for
# cdk-2.8.jar) before it builds its engine. Nothing is downloaded at install time either, see
# ORTHONYM_SKIP_JAR_PREFETCH below.
#
# Base images: python 3.12 on Debian 13 trixie, and the JRE from Temurin's noble (Ubuntu 24.04) image,
# whose /opt/java/openjdk is self-contained: the JVM needs only libc, libm, libz and libstdc++, which the
# python image has. (Its lib/security/cacerts is a link into Ubuntu's /etc/ssl/certs/java, which does not
# exist here; nothing in this service uses TLS, so it never matters.) The service was measured on Temurin
# 21.0.13; the official image's newest 21 JRE is 21.0.12.1. Orthonym needs Java 11 or newer. Tags and
# digests pinned.

ARG PYTHON_IMAGE=python:3.12.15-slim-trixie@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
ARG JRE_IMAGE=eclipse-temurin:21.0.12.1_1-jre-noble@sha256:35501d3f93173b420ac9001658eb68157e813316b865087e345c187fca422c4c
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff

FROM ${UV_IMAGE} AS uv
FROM ${JRE_IMAGE} AS jre

# ---- jars: the build context's copy, verified -------------------------------------------------
FROM ${PYTHON_IMAGE} AS jars
WORKDIR /w
COPY deploy/weights/verify.sh deploy/weights/SHA256SUMS ./
COPY deploy/weights/chem/ ./chem/
RUN sh verify.sh --strict chem /w/chem /w/SHA256SUMS

# ---- builder ----------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /uvx /bin/
# git: Orthonym is pinned to a commit (a git dependency). Builder stage only.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
# ORTHONYM_SKIP_JAR_PREFETCH: Orthonym's build hook (hatch_build.py) downloads both jars into the user
# cache when the wheel is built, i.e. during `uv sync`, in a build step nobody meant to give network
# access to and for files that are baked in separately. services/chem sets it for uv through
# [tool.uv.extra-build-variables]; it is exported here as well, for any build path that does not read
# that table (services/chem/README.md, "the P2-05 trap").
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    ORTHONYM_SKIP_JAR_PREFETCH=1
WORKDIR /src/services/chem
COPY services/common /src/services/common
COPY services/chem/pyproject.toml services/chem/uv.lock services/chem/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --no-install-project
COPY services/chem/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- runtime ----------------------------------------------------------------------------------
# Drawing is not supported in this image: `import rdkit.Chem.Draw.rdMolDraw2D` fails here (libXrender.so.1
# is missing), because the python slim base has no X libraries and rdkit's drawing module links against
# libXrender, libXext, libX11 and libexpat from the system. Nothing in chem draws (engine.py uses rdBase
# and Chem only), so none are installed. api.Dockerfile draws the result SVGs and installs libxrender1
# libxext6 libx11-6 libexpat1 for that; a chem feature that draws needs the same packages in this stage.
FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="decimer-chem" \
      org.opencontainers.image.description="DECIMER chem service: Orthonym IUPAC names and CDK layout (decimer.ai)"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent \
               --shell /usr/sbin/nologin app

COPY --from=jre /opt/java/openjdk /opt/java/openjdk
# The jars (OPSIN, centres, cdk-2.8.jar) are written 0600 by Orthonym's own download and the build
# context's permissions are whatever the host's umask gave: set the modes outright (owner rw, everyone else
# read-only; the service user only reads the jars), and prove below that they are readable and that nothing
# there is writable for it. They are root-owned, so the service user cannot replace one.
COPY --from=jars /w/chem/ /opt/orthonym/jars/
RUN chmod -R u=rwX,go=rX /opt/orthonym
COPY --from=builder /opt/venv /opt/venv
COPY deploy/healthcheck.py /usr/local/bin/healthcheck.py

# JAVA_HOME: JPype looks for $JAVA_HOME/lib/server/libjvm.so (the JRE).
# CHEM_JARS_DIR: where the service looks for the three jars (otherwise Orthonym's cache directory).
# ORTHONYM_JVM_XMX: the JVM heap cap, which Orthonym passes to the JVM as -Xmx (its default is 512m;
# written out here). An explicit -Xmx wins over -XX:MaxRAMPercentage, so this image sets no
# JAVA_TOOL_OPTIONS: the cap is this one variable. Measured (services/chem/README.md): the naming
# process, JVM included, stays near 360 MB and the web process near 50 MB, flat over 1000 names; 1.5 GB in
# compose leaves room for the full 512 MB heap plus the JVM's own memory.
# The service REFUSES to start when ORTHONYM_ALLOW_REDUCED, ORTHONYM_OPSIN_JAR, ORTHONYM_CENTRES_JAR or
# ORTHONYM_DISABLE_JPYPE is set (they bypass the jar checks or the long-lived JVM): never set them here
# or in compose. CHEM_ALLOW_JAR_DOWNLOAD must stay unset too: the service has no network.
ENV JAVA_HOME=/opt/java/openjdk \
    PATH=/opt/venv/bin:/opt/java/openjdk/bin:$PATH \
    CHEM_JARS_DIR=/opt/orthonym/jars \
    ORTHONYM_JVM_XMX=512m \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    PORT=8004

USER 10001:10001
# Smoke test at build time, as the service user: the JRE runs on this base, the packages import
# (JPype, RDKit, Orthonym), and the three jars can be read.
RUN java -version \
    && python -c "import decimer_chem, decimer_service.logs, jpype, orthonym, rdkit; print('chem imports ok')" \
    && find /opt/orthonym/jars -type f -exec head -c 1 {} + > /dev/null \
    && test -r /opt/orthonym/jars/opsin-cli-2.9.0-jar-with-dependencies.jar \
    && test -r /opt/orthonym/jars/centres-cli-1.2.1.jar \
    && test -r /opt/orthonym/jars/cdk-2.8.jar \
    && [ -z "$(find /opt/orthonym -writable)" ] \
    && find /tmp -mindepth 1 -delete

EXPOSE 8004
# /readyz names caffeine through the whole stack (HTTP, naming process, JVM, OPSIN), requires the exact
# name with an OPSIN read-back, and has CDK lay caffeine out as a molblock of 14 atoms and 15 bonds.
# Start-up is about 3 s; the selftest is milliseconds.
HEALTHCHECK --interval=30s --timeout=20s --start-period=60s --retries=3 \
    CMD ["python", "/usr/local/bin/healthcheck.py", "8004", "/readyz", "15"]
CMD ["python", "-m", "decimer_chem"]

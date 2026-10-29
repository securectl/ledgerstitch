# SPDX-License-Identifier: AGPL-3.0-only
# LedgerStitch Community, self-hosted server mode.
#
#   docker build -f docker/Dockerfile -t ledgerstitch:community .
#   sh scripts/update.sh        # later: rebuild only what changed (see docs/INSTALL.md, Updating)
#   docker compose -f docker/docker-compose.yml up -d        # with Caddy for automatic HTTPS
#
# The container runs `ledgerstitch --server`: admin-password sign-in, data in the /data volume
# (vaults stay encrypted at rest with their own passphrases). It refuses plain HTTP unless
# LEDGERSTITCH_ALLOW_HTTP=1, which the compose file sets because Caddy terminates TLS.
# See docs/INSTALL.md ("Self-host with Docker").

# The base image, pinned by digest so a rebuild is reproducible. scripts/update.sh reports when
# the tag has a newer upstream digest (security patches) and rebuilds on it with --refresh-base.
# python:3.14-slim index digest, Docker Hub, 2026-10-28.
ARG PYTHON_IMAGE=python:3.14-slim@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8

# ---------------------------------------------------------------- requirements
# Only the [project] dependency list, so a version bump or a code change doesn't invalidate the
# dependency layer below: an app-only update rebuilds just the last layers.
FROM ${PYTHON_IMAGE} AS reqs
COPY pyproject.toml /src/pyproject.toml
RUN python -c "import tomllib; d = tomllib.load(open('/src/pyproject.toml', 'rb')); print('\n'.join(d['project']['dependencies']))" > /requirements.txt

# ---------------------------------------------------------------- build
FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
# 1. dependencies (cached until the dependency list or the base image changes)
COPY --from=reqs /requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r /tmp/requirements.txt
# Optional extra packages (e.g. add-on wheels copied into the build context):
#   docker build --build-arg EXTRA_PIP="./wheels/<addon>.whl" ...
ARG EXTRA_PIP=""
RUN if [ -n "$EXTRA_PIP" ]; then /opt/venv/bin/pip install $EXTRA_PIP; fi
# 2. LedgerStitch itself (rebuilt on every code change; seconds)
COPY pyproject.toml LICENSE ./
COPY ledgerstitch ./ledgerstitch
RUN /opt/venv/bin/pip install --no-deps . && /opt/venv/bin/pip check

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE}
LABEL org.opencontainers.image.title="LedgerStitch Community" \
      org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.description="Private bookkeeping cleanup: statements in, CPA-ready books out. Self-hosted server mode."
RUN groupadd --system --gid 10001 ledgerstitch \
 && useradd --system --uid 10001 --gid ledgerstitch --home-dir /data --no-create-home --shell /usr/sbin/nologin ledgerstitch \
 && mkdir -p /data && chown ledgerstitch:ledgerstitch /data && chmod 700 /data
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/data \
    LEDGERSTITCH_SERVER=1 \
    LEDGERSTITCH_IN_DOCKER=1 \
    LEDGERSTITCH_DATA_DIR=/data \
    LEDGERSTITCH_HOST=0.0.0.0 \
    LEDGERSTITCH_PORT=8080
VOLUME ["/data"]
USER ledgerstitch
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-c", "import os, ssl, urllib.request; s = 'https' if os.environ.get('LEDGERSTITCH_TLS_CERT') else 'http'; c = ssl._create_unverified_context(); urllib.request.urlopen(f\"{s}://127.0.0.1:{os.environ.get('LEDGERSTITCH_PORT', '8080')}/healthz\", timeout=4, context=c if s == 'https' else None)"]
CMD ["ledgerstitch", "--server", "--no-browser"]

# What this image was built from; scripts/update.sh compares these with the checkout to decide
# whether to rebuild (last, so changing them never invalidates a cached layer).
ARG PYTHON_IMAGE
ARG LEDGERSTITCH_REVISION=unknown
ARG LEDGERSTITCH_VERSION=unknown
ARG LEDGERSTITCH_DEPS_SHA=unknown
ARG LEDGERSTITCH_SRC_SHA=unknown
ARG LEDGERSTITCH_BASE_PIN=unknown
LABEL org.opencontainers.image.revision="${LEDGERSTITCH_REVISION}" \
      org.opencontainers.image.version="${LEDGERSTITCH_VERSION}" \
      org.opencontainers.image.base.name="${PYTHON_IMAGE}" \
      io.ledgerstitch.deps-sha="${LEDGERSTITCH_DEPS_SHA}" \
      io.ledgerstitch.src-sha="${LEDGERSTITCH_SRC_SHA}" \
      io.ledgerstitch.base-pin="${LEDGERSTITCH_BASE_PIN}"

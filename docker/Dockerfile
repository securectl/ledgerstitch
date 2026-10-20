# SPDX-License-Identifier: AGPL-3.0-only
# LedgerStitch Community, self-hosted server mode.
#
#   docker build -f docker/Dockerfile -t ledgerstitch:community .
#   docker compose -f docker/docker-compose.yml up -d        # with Caddy for automatic HTTPS
#
# The container runs `ledgerstitch --server`: admin-password sign-in, data in the /data volume
# (vaults stay encrypted at rest with their own passphrases). It refuses plain HTTP unless
# LEDGERSTITCH_ALLOW_HTTP=1, which the compose file sets because Caddy terminates TLS.
# See docs/INSTALL.md ("Self-host with Docker").

# ---------------------------------------------------------------- build
FROM python:3.14-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml LICENSE ./
COPY ledgerstitch ./ledgerstitch
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install .
# Optional extra packages (e.g. add-on wheels copied into the build context):
#   docker build --build-arg EXTRA_PIP="./wheels/<addon>.whl" ...
ARG EXTRA_PIP=""
RUN if [ -n "$EXTRA_PIP" ]; then /opt/venv/bin/pip install $EXTRA_PIP; fi

# ---------------------------------------------------------------- runtime
FROM python:3.14-slim
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
    LEDGERSTITCH_DATA_DIR=/data \
    LEDGERSTITCH_HOST=0.0.0.0 \
    LEDGERSTITCH_PORT=8080
VOLUME ["/data"]
USER ledgerstitch
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-c", "import os, ssl, urllib.request; s = 'https' if os.environ.get('LEDGERSTITCH_TLS_CERT') else 'http'; c = ssl._create_unverified_context(); urllib.request.urlopen(f\"{s}://127.0.0.1:{os.environ.get('LEDGERSTITCH_PORT', '8080')}/healthz\", timeout=4, context=c if s == 'https' else None)"]
CMD ["ledgerstitch", "--server", "--no-browser"]

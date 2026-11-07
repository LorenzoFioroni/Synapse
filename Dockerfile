# syntax=docker/dockerfile:1
# Synapse in a container, behind a tunnel such as Cloudflare Tunnel: see docs/docker.md.
#   docker compose up -d --build

# The web app's scripts are TypeScript, compiled here once: running Synapse needs no Node.js.
FROM node:22-trixie-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


FROM python:3.13-slim-trixie
COPY --from=rclone/rclone:1.76.0 /usr/local/bin/rclone /usr/local/bin/rclone
COPY docker/rclone-LICENSE /usr/local/share/licenses/rclone/LICENSE
# git keeps the notes history; tini passes stop signals on to Python and reaps the programs it
# starts (git, esbuild, pip).
RUN apt-get update \
    && apt-get install -y --no-install-recommends git tini ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.txt
COPY . .
COPY --from=frontend /app/frontend/js frontend/js
# Plugins' TypeScript frontends are bundled with esbuild when they start (backend/plugins/build.py).
# npm ci leaves esbuild's own executable here, which runs without Node.js.
COPY --from=frontend /app/frontend/node_modules/esbuild/bin/esbuild /usr/local/bin/esbuild
# The app folder belongs to root and stays read-only to Synapse; readable whatever the checkout's umask.
RUN chmod -R a+rX,go-w /app && esbuild --version

# The commit this image is built from, for Settings → Updates (the updater passes it, docs/updates.md).
ARG SYNAPSE_COMMIT=
ENV SYNAPSE_COMMIT=${SYNAPSE_COMMIT} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SYNAPSE_DATA_DIR=/data \
    HOME=/tmp
# compose.yaml runs it as the owner of the data folder on the host (SYNAPSE_UID / SYNAPSE_GID).
USER 1000:1000
EXPOSE 8000
# Loopback inside the container is "this computer": the web app manifest answers without a sign-in.
HEALTHCHECK --interval=30s --timeout=5s --start-period=2m --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/manifest.webmanifest', timeout=4)"]
# `docker stop` stops it as Ctrl+C does in a terminal.
STOPSIGNAL SIGINT
# One process, Werkzeug's threaded server, as server.py runs it: passkey challenges, confirmations,
# the change feed and the plugins' jobs live in its memory.
ENTRYPOINT ["tini", "--", "python", "server.py", "--container"]

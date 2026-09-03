# syntax=docker/dockerfile:1

# ---- frontend stage: build the React client ----
FROM node:22-alpine AS frontend
WORKDIR /ui
# Install from the lockfile first so dependency layers cache across code edits.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- runtime stage ----
FROM python:3.12-slim AS runtime

# ffmpeg: mux Pinterest video pins. tini: clean signal handling.
# gosu: drop from root to the app user in the entrypoint after fixing perms.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tini gosu \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PINCHIVE_DATA_DIR=/data

# Links the GHCR package to the repo (README + provenance on the package page).
LABEL org.opencontainers.image.source="https://github.com/aroxu/pinchive" \
      org.opencontainers.image.description="Self-hosted Pinterest board archiver" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

# The built SPA. app/main.py serves it from frontend/dist (index.html plus the
# hashed assets/ bundle), with an index.html fallback for client-side routes.
COPY --from=frontend /ui/dist ./frontend/dist

# Resolve `import app` to the /app source tree (next to frontend/dist)
# rather than the copy pip put in site-packages, so BASE_DIR points at /app.
ENV PYTHONPATH=/app

# App user. The entrypoint runs as root only long enough to fix ownership of
# the /data mount (a host bind mount arrives with the host's uid), then drops
# to this user via gosu — so the app process itself is never root.
RUN useradd -m -u 10001 pinchive \
    && mkdir -p /data \
    && chown -R pinchive:pinchive /app /data
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

VOLUME ["/data"]
EXPOSE 8000

# tini as PID 1 -> entrypoint (root: fix perms, drop priv) -> CMD as pinchive.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
# Default command runs the web server. The worker overrides command in compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

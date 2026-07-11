# syntax=docker/dockerfile:1

# ---- assets stage: fetch vendored JS + fonts (best-effort) ----
FROM alpine:3.20 AS assets
RUN apk add --no-cache curl bash
WORKDIR /assets
COPY scripts/fetch_assets.sh ./fetch_assets.sh
RUN bash fetch_assets.sh /assets

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

# Project static + templates first...
COPY static ./static
COPY templates ./templates

# ...then overlay vendored assets fetched in the assets stage so they win.
COPY --from=assets /assets/htmx.min.js ./static/js/htmx.min.js
COPY --from=assets /assets/idiomorph-ext.min.js ./static/js/idiomorph-ext.min.js
COPY --from=assets /assets/fonts/ ./static/fonts/

# Resolve `import app` to the /app source tree (next to templates/ + static/)
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

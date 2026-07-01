# syntax=docker/dockerfile:1

# ---- assets stage: fetch vendored JS + fonts (best-effort) ----
FROM alpine:3.20 AS assets
RUN apk add --no-cache curl bash
WORKDIR /assets
COPY scripts/fetch_assets.sh ./fetch_assets.sh
RUN bash fetch_assets.sh /assets

# ---- runtime stage ----
FROM python:3.12-slim AS runtime

# ffmpeg: needed by gallery-dl to mux Pinterest video pins.
# tini: clean signal handling for the worker/web processes.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PINCHIVE_DATA_DIR=/data

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

# Optional Playwright fallback: bake in the `refresh` extra + chromium (with its
# OS deps) only when requested, so the default image stays slim. Browsers go to
# a shared path readable by the non-root runtime user.
ARG INSTALL_PLAYWRIGHT=false
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN mkdir -p /ms-playwright \
    && if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then \
         pip install --no-cache-dir ".[refresh]" \
         && python -m playwright install --with-deps chromium ; \
       fi

# Project static + templates first...
COPY static ./static
COPY templates ./templates

# ...then overlay vendored assets fetched in the assets stage so they win.
COPY --from=assets /assets/htmx.min.js ./static/js/htmx.min.js
COPY --from=assets /assets/fonts/ ./static/fonts/

# Resolve `import app` to the /app source tree (next to templates/ + static/)
# rather than the copy pip put in site-packages, so BASE_DIR points at /app.
ENV PYTHONPATH=/app

# Non-root.
RUN useradd -m -u 10001 pinchive \
    && mkdir -p /data \
    && chown -R pinchive:pinchive /app /data /ms-playwright
USER pinchive

VOLUME ["/data"]
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
# Default command runs the web server. The worker overrides command in compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

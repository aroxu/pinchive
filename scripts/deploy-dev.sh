#!/usr/bin/env bash
# Deploy the LOCAL working tree (uncommitted changes and all) to the dev server
# and rebuild the stack there — so you can test on dev BEFORE committing.
#
#   bash scripts/deploy-dev.sh
#
# It ships the source over SSH (tar, no rsync needed) into the dev checkout and
# runs `docker compose -f docker-compose.yml up --build -d`, which builds the
# image from the synced source (compose.yaml is the pull-only default, so the
# build file is named explicitly). The dev server's own `.env` (and its data/)
# are preserved — never
# overwritten — so its PINCHIVE_IMAGE config stays intact.
#
# After you're happy: commit + push as usual. To realign dev with the pushed
# image later:  ssh $HOST 'cd ~/$DIR && git reset --hard origin/main &&
#                          docker compose pull && docker compose up -d'
set -euo pipefail

HOST="${PINCHIVE_DEV_HOST:-aroxu@dev}"
DIR="${PINCHIVE_DEV_DIR:-pinchive}"

echo ">> syncing working tree -> $HOST:~/$DIR (preserving remote .env + data/)"
tar czf - \
  --exclude=.git \
  --exclude=.env \
  --exclude=data \
  --exclude=.venv \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude=.claude \
  --exclude=.ruff_cache \
  --exclude=.pytest_cache \
  --exclude=static/fonts \
  --exclude=static/js/htmx.min.js \
  --exclude=static/js/idiomorph-ext.min.js \
  . | ssh "$HOST" "mkdir -p ~/$DIR && tar xzf - -C ~/$DIR"

echo ">> building + starting on dev (docker compose -f docker-compose.yml up --build -d)"
ssh "$HOST" "cd ~/$DIR && docker compose -f docker-compose.yml up --build -d"

echo ">> health"
ssh "$HOST" "sleep 4; curl -s http://localhost:8000/healthz; echo; \
             docker compose -f ~/$DIR/docker-compose.yml ps --format '{{.Name}} {{.Status}}' 2>/dev/null | grep pinchive || true"
echo ">> deployed local working tree to dev."

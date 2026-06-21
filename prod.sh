#!/usr/bin/env bash
# Deploy the supps app to OrbStack/Docker behind the Caddy reverse proxy.
#
# Rebuilds the image and recreates the `supps` container. It does NOT touch
# the `supps-data` volume, so application data (kuzu DB, abstracts, claims)
# persists untouched across deploys.
#
# This script assumes the data volume has already been seeded by the initial
# one-off deploy (see DEPLOY.md). For the very first deploy, follow DEPLOY.md
# instead of running this.
#
# Caddy is not reloaded here: the route uses dynamic upstreams (refresh 10s),
# so it re-resolves the container automatically after recreation. The Caddyfile
# only needs editing when the subdomain/container name changes.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Building image and recreating container (supps-data volume untouched)…"
docker compose up -d --build

echo "==> Connecting to the web-routing network (idempotent)…"
docker network connect web-routing supps 2>/dev/null || true

echo
echo "==> Deployed. https://supps.teleosis.ai"
docker compose ps

# Deploying the supps app

The app runs as a Docker container (`supps`) on OrbStack, behind the shared
Caddy reverse proxy, and is served at **https://supps.teleosis.ai**.

- Routing: Caddy (`../caddy/Caddyfile`) routes the subdomain to the `supps`
  container over the external `web-routing` network using dynamic upstreams.
- Data: lives in the `supps-data` named volume mounted at `/app/data`
  (kuzu DB, abstracts, claims, json). It is **decoupled from the image** and
  persists across rebuilds.

## Routine deploys

```bash
./prod.sh
```

Rebuilds the image and recreates the container. The `supps-data` volume is
left untouched, so application data is preserved. Caddy needs no reload —
dynamic upstreams re-resolve the container automatically.

## First-time / one-off deploy (seeds the data volume)

Run from the project root. This is the only time `data/` is copied in.

```bash
# 1. Build the image
docker compose build

# 2. Create the data volume and seed it from local ./data (kuzu DB + everything)
docker volume create supps-data
docker run --rm \
  -v supps-data:/dest \
  -v "$PWD/data":/src:ro \
  alpine sh -c 'cp -a /src/. /dest/ && ls -la /dest'

# 3. Start the container (attaches to web-routing via docker-compose.yml)
docker compose up -d

# 4. Add the route to ../caddy/Caddyfile (already done) and hot-reload Caddy
docker exec proxy caddy reload --config /etc/caddy/Caddyfile
```

Verify: `curl -s http://supps.teleosis.ai` (or open it in a browser).

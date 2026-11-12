---
title: store-infra: repo, Compose stack skeleton and runbook stub
repo: store-infra
slice: 1
type: AFK
status: closed
assignee:
blocked-by: []
---

## Scope

- Create the `store-infra` repo with a Compose file for `caddy`, `postgres`, `redis`, `api`, `web`, `worker`, `beat` on a private network; only Caddy publishes ports.
- Caddyfile: one public origin, auto-TLS, routes `/` to `web`, serves `/media/*` from a shared volume with `Cache-Control: public, max-age=31536000, immutable`, maintenance page when `web` is down.
- Env templates per service (`.env.example`), documented secrets handling (mode 600, nothing committed), runbook stub with DNS and SPF/DKIM/DMARC section headings.
- Dev profile that runs the same stack locally with images built from sibling checkouts.

## Acceptance

- [x] `docker compose up` brings the stack up locally with placeholder app images and Caddy answers on the public port.
- [x] Postgres and Redis are not reachable from the host.
- [x] Stopping `web` makes Caddy serve the maintenance page.

## Spec

- [Architecture section 1](../../spec/architecture.md)
- [Architecture section 4](../../spec/architecture.md)

## Resolution

Repo: `~/Projects/Python/store-infra` (not yet under git). Verified by the Owner on a local Docker daemon: `docker compose up` brings the stack up with placeholder images and Caddy answers on :8080; Postgres and Redis publish no host ports; stopping `web` serves the 503 maintenance page and starting it restores the placeholder.

- `compose.yaml` is the production topology; a bare `docker compose up` also loads `compose.override.yaml` (placeholders, plain HTTP). Production runs `-f compose.yaml` only. `compose.dev.yaml` builds from the sibling checkouts.
- Networks: `edge` (caddy, web), `app` (web, api), `data` (internal: postgres, redis, plus api/worker/beat).
- `DJANGO_ALLOWED_HOSTS` must include `api`, because `web` calls `http://api:5000`.
- Open: the `media` volume's ownership for the Django user is unchecked until the first real deploy. `OWNER` in the default image names is a placeholder.

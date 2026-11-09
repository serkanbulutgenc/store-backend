---
title: Grill: deployment, CI/CD, backups and observability
label: wayfinder:grilling
status: closed
assignee: claude
blocked-by: []
parent: map.md
---

## Question

Decide the single-VPS Docker Compose topology for both repos, environments (dev/staging/prod), CI/CD pipeline, database and media backups with restore testing, secrets, logging/error tracking/uptime monitoring, and the testing strategy each repo needs in CI.

## Resolution

Decided in a two-round grilling; all recommendations accepted.

**Topology and environments**
- A small `store-infra` repo holds the Compose file, Caddyfile, env templates, deploy script and runbook; the two app repos only build and push images. Environments: `dev` (local Compose) and `prod`; no permanent staging, but the same Compose file can run on the VPS under another project name and domain for rehearsals.
- Services: `caddy`, `web` (TanStack Start Node), `api` (Django, gunicorn), `worker` (Celery: email outbox, image derivatives, 24 h / 48 h bank-transfer jobs), `beat`, `postgres` (container, named volume), `redis` (broker only; the Postgres outbox is the source of truth for messages). Only Caddy is public; everything else is on a private Compose network.

**CI/CD and testing**
- GitHub Actions per repo; on merge to `main` build and push a versioned image to GHCR. Deploy is a manual one-command script in `store-infra` (pull tags, migrate, restart); rollback = re-run with the previous tag. Migrations must be backwards-compatible with the previous release for one deploy.
- Backend CI: pytest on real Postgres, ruff, `makemigrations --check`, OpenAPI spec drift check. Frontend CI: Biome, strict `tsc`, Vitest, spec-sync check. Plus one Playwright smoke test of the guest COD checkout against the Compose stack (it also exercises the SSR cookie hop the spike left unverified). Admin screens get component and API tests, not end-to-end.

**Backups and restore**
- Nightly compressed `pg_dump`, nightly incremental media sync (files are immutable and hashed), weekly provider snapshot; all encrypted client-side and sent off-site to a different provider or region. Retention 14 daily / 8 weekly / 6 monthly. Accepted loss window is up to 24 hours of orders: a figure to confirm with the Owner.
- Scripted restore into a scratch Compose project monthly (manual, logged in the runbook); one successful restore is required before go-live; alert if the nightly backup has not succeeded in 26 hours.

**Secrets, TLS, DNS**
- Env files on the VPS (mode 600, deploy user), created from templates, never committed; backup encryption keys kept off the VPS in the Owner's password manager; CI secrets in GitHub Actions secrets; manual rotation listed in the runbook. No secrets manager.
- Caddy auto-TLS (Let's Encrypt) for apex and `www` redirecting to one canonical host; HSTS after the first stable week; the Owner controls DNS, with A records and SPF/DKIM/DMARC documented in the runbook.

**Observability and server**
- JSON logs to stdout with Docker rotation (about 7 days of 50 MB); hosted error tracking for Django and Start with PII scrubbing (vendor goes on the KVKK launch checklist). The Dashboard audit log stays separate.
- External uptime check on the storefront and an API health endpoint that checks database and Redis; alerts by email to Owner and developer plus a push channel for downtime; internal alerts for backup age, any outbox message failing after all retries, and disk over 80%. No metrics stack.
- VPS at least 2 vCPU, 4 GB RAM, 40 GB SSD in an EU region near Turkey (transfer-abroad review on the launch checklist). SSH keys only, no root login, firewall allowing 80/443/SSH, unattended security updates, fail2ban, Docker daemon not exposed, Postgres and Redis not published.
- Brief downtime at deploy is accepted (no blue-green); deploys happen outside trading hours; Caddy serves a maintenance page when the app is down.

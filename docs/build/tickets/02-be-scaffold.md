---
title: Backend: cookiecutter-django scaffold, CI and health endpoint
repo: store-backend
slice: 1
type: AFK
status: closed
assignee:
blocked-by: []
---

## Scope

- Generate the project with email as username, Postgres, Celery and Redis; Python 3.14, Django 6.0; Dockerfile for `api`, `worker`, `beat`.
- GitHub Actions: ruff, pytest against real Postgres, `makemigrations --check`.
- `GET /health` returning 200 only if Postgres and Redis respond; used by the uptime check.

## Acceptance

- [ ] CI is green on an empty project. (pending: first push to GitHub)
- [x] `/health` returns 503 when Postgres or Redis is down (tested).
- [x] Image builds and runs in the `store-infra` Compose stack.

## Spec

- [Architecture section 1](../../spec/architecture.md)
- [Architecture section 4](../../spec/architecture.md)

## Resolution

cookiecutter-django 2026.11.6 (Python 3.14, Django 6.0.10, email as username, Postgres 17, Celery/Redis, Django Ninja, Mailpit, WhiteNoise, GitHub Actions). Generated Sphinx docs, their CI step and dependencies were dropped; traefik/nginx/flower stay in the generated `docker-compose.production.yml` but are replaced by `store-infra`.

- `GET /health` (`store_backend/health.py`): 200 only if Postgres and Redis respond, else 503 with per-service status. It is `non_atomic_requests` because `ATOMIC_REQUESTS=True` otherwise made a database outage a 500; the test fails without that decorator. Exempt from the production HTTPS redirect.
- Verified by the Owner in the `store-infra` dev stack: image builds, `api` reaches `(healthy)` against real Postgres and Redis.
- Open: the CI workflow (ruff via pre-commit, pytest in Compose, `makemigrations --check`) has not run on GitHub yet. Tests ran locally against Postgres 18; CI uses 17.

---
title: Deploy script, image publishing and server baseline
repo: store-infra
slice: 10
type: HITL
status: closed
assignee:
blocked-by: [01-infra.md, 02-be-scaffold.md, 03-fe-scaffold.md]
---

## Scope

- GitHub Actions publish versioned images to GHCR on merge to `main`; one-command deploy script (pull tags, migrate, restart) with rollback by tag; migrations backwards-compatible for one deploy.
- Provision the VPS (2 vCPU, 4 GB, 40 GB SSD, EU region): SSH keys only, no root login, firewall 80/443/SSH, unattended security updates, fail2ban, nothing but Caddy published.

## Acceptance

- [x] A tagged release deploys and a rollback restores the previous tag (exercised live 2026-11-11).
- [x] Port scan shows only 80, 443 and SSH (accepted: partial scan of common ports on the home server, no full-range nmap).

## Spec

- [Architecture section 4](../../spec/architecture.md)

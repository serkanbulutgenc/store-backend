## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (`serkanbulutgenc/store-backend`) via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Git workflow

- `dev` is the default branch. Branch from `dev`, open PRs into `dev`, squash merge.
- `main` changes only through a `dev` -> `main` PR merged with a merge commit; never push to `main` directly. A merge to `main` publishes `sha-<commit>` images.
- Versions are git tags (`vX.Y.Z`) cut by the manual **Release** workflow on `main`. Do not edit version files by hand.
- Both branches are protected by rulesets (PR plus passing checks, no force-push, no bypass). Do not loosen them without asking.
- Dependabot PRs target `dev`.
- Background: `store-infra/docs/adr/0001-dev-branch-and-tag-versioning.md`.

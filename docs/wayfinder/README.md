# Wayfinder tracker (local markdown)

- `map.md` is the map issue (label `wayfinder:map`).
- `tickets/NN-slug.md` are child issues. Frontmatter: `title`, `label` (wayfinder:<type>), `status` (open|closed), `assignee` (empty = unclaimed), `blocked-by` (list of ticket filenames).
- Frontier = status open, assignee empty, every blocked-by ticket closed.
- Claim = set `assignee`. Resolve = append `## Resolution` to the ticket, set `status: closed`, add a line to map "Decisions so far".
- Research output lands in `research/<ticket-slug>.md` and is linked from the ticket.

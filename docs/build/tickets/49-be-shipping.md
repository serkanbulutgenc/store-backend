---
title: Backend: carriers, zones, desi and rate tables
repo: store-backend
slice: 13
type: AFK
status: closed
assignee:
blocked-by: [48-be-checkout-split.md]
---

## Scope

- Models: Carrier (Yurtiçi, Aras, PTT), Zone, province-to-zone mapping for all 81 provinces, rate rows (carrier x zone x bands 0-2, 3-5, 6-10 desi + per extra desi), a desi value on Product class and an optional override on Product.
- `services.shipping.fee_for(desi, province)` returning the cheapest carrier's KDV-inclusive fee and the carrier chosen; `None` when the province or desi is unknown.
- Seed the dummy sheet from the spec appendix and default desi per class (migration with data).

## Acceptance

- [ ] Cheapest-carrier selection is tested for each band edge (2, 3, 5, 6, 10, 11 desi) and each zone.
- [ ] Turkish province names match regardless of case and dotted/dotless i.

## Spec

- [Phase 2 section 4](../../spec/phase-2.md)

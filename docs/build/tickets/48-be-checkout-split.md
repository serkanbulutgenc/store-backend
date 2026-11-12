---
title: Backend: split checkout into quote and place
repo: store-backend
slice: 13
type: AFK
status: closed
assignee:
blocked-by: []
---

## Scope

- Split `place_order` into `quote(basket, method, destination) -> Quote` (pure pricing, no writes) and `place(quote, form, caller)`; move `clean_phone`, `clean_address` and `payment_method_of` out of `checkout.py` into modules their other callers import.
- Wrap Oscar order creation behind one function so the rest of checkout stays Oscar-free.

## Acceptance

- [x] Behaviour is unchanged: the existing checkout, concurrency and order tests pass untouched.
- [x] Pricing tests run against `quote` without a database write.

## Spec

- [Phase 2 section 6](../../spec/phase-2.md)

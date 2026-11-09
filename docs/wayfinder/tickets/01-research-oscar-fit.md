---
title: Research: django-oscar fit for a headless Turkish store
label: wayfinder:research
status: closed
assignee:
blocked-by: []
parent: map.md
---

## Question

Against Oscar's current release and docs, establish facts: (1) supported Django/Python versions and compatibility with a current cookiecutter-django project; (2) how to use Oscar as a headless engine with no templates/dashboard (apps to install/skip); (3) single-currency TRY and tax-inclusive pricing (strategies, per-product tax rates for KDV); (4) anonymous basket + guest checkout support; (5) variants (parent/child products, attributes), stock records, allocation; (6) payment sources/offline payments and order status pipeline customisation; (7) extension patterns for Supplier and Brand (partner vs custom models); (8) any known pitfalls. Write findings to research/01-oscar-fit.md with source links.

## Resolution

Findings: `../research/01-oscar-fit.md`.

- Oscar 4.2.1 (2026-09-29) supports Django 4.2/5.2/6.0 and Python 3.8-3.14; cookiecutter-django pins Python 3.14, unpinned Django.
- No official headless guide; OrderCreator, strategies and models are view-independent, but checkout views, cookie-based basket middleware, Haystack and sites/flatpages are required. django-oscar-api targets Oscar 3.2 / Django 3.2.
- Built-in `FixedRateTax` treats the stored price as tax-exclusive; per-product rate via `get_rate`. Anonymous basket and guest checkout (`Order.guest_email`) are native.
- Variants (parent/child), stockrecords with allocation, payment Sources and order status pipelines are native; Brand has no model, Supplier maps to Partner. Open items listed in the findings.

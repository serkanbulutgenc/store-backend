---
title: Grill: catalog model (products, variants, categories, brands, suppliers)
label: wayfinder:grilling
status: closed
assignee: claude-session
blocked-by: [01-research-oscar-fit.md, 12-spike-headless-oscar-and-ssr-cookies.md]
parent: map.md
---

## Question

Decide the catalog domain model for women's clothing/shoes/underwear/accessories: product classes and attributes (size, colour, material), variant structure, category tree, Brand and Supplier entities and their relationships to products/stock, SKU/barcode scheme, how prices/KDV rate are held. Update CONTEXT.md.

## Resolution

Glossary updated in `/CONTEXT.md`.

- **Shape**: one Product per model; Variants are colour x size, each with own stock. Photos tagged by colour (storage itself still in the fog).
- **Vocabularies**: sizes are fixed lists per Product class; colours one shared Owner-managed list with swatch hex.
- **Categories**: two levels, one primary Category per Product; "new"/"on sale" are flags/dates, not categories.
- **Brand**: custom optional model (name, slug, logo); Storefront shows only brands with products.
- **Supplier**: one per Product, with supplier code and cost price on the Product; no purchase orders or stock-in history in phase 1; stock changes are manual adjustments with a reason.
- **Price/KDV**: staff enter KDV-inclusive shelf price on the Product, Variants inherit (optional override); KDV rate from Product class (10% clothing/footwear/underwear, 20% default, accessories chosen per Product pending accountant); optional old price; tax-exclusive amount derived.
- **SKU/barcode**: unique auto-generated editable SKU (e.g. `AUR-000123-KRM-38`) plus optional barcode; dashboard search/scan by either; label printing out of phase 1.
- **Visibility**: draft / published / archived; never hard-delete Products with orders; zero-stock Variants shown as "tukendi" with add-to-basket disabled; all-zero Products stay listed as sold out.
- **Attributes**: optional material (text), season (short list), care notes (text); only colour, size, brand, category filterable in phase 1.
- **Stock**: per-Variant quantity; store-wide low-stock threshold default 2, per-Product override; Dashboard flags low stock; Storefront shows "son N adet" only at or below threshold.

# Attributes, Favorites and Reviews are our own models, not Oscar's

Product Attributes (and their values), Favorites and Reviews are modelled in `store_backend` with small purpose-built tables. Oscar's `ProductAttribute`/`ProductAttributeValue`, `wishlists` and `catalogue.reviews` apps stay in `INSTALLED_APPS` so Oscar's own models and migrations keep working, but nothing in the store reads or writes them.

Oscar is an engine only here (basket, order, stock, categories). Its attribute EAV is shaped for its own dashboard and templates, which we do not use: it has no option lists we can order and filter on, one value type per class-wide attribute, and no clean way to count facets per option. Its wishlists are named, shareable lists, and its reviews carry a required title, vote counts and a status flow of their own; we want a single heart per Shopper and Product, and stars plus an optional moderated body. Building three small models keeps the Store API simple and the code consistent with Brand, Colour and the other store-owned data.

We rejected adapting Oscar's models behind our own API: every constraint we need (single or multiple values, filterable options, one review per Shopper, moderation state) would be fighting Oscar's schema, and a future Oscar upgrade could change it under us.

Consequences: the Oscar apps' tables stay empty; deleting them from `INSTALLED_APPS` is a separate decision, because it would also unmount their migrations.

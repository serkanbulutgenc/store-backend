"""Oscar class loader that swaps in this project's strategy `Selector`.

Oscar looks `partner.strategy.Selector` up through `get_class`. Forking the whole
`partner` app just to replace one class would copy its migrations, so this wraps the
default loader instead (`OSCAR_DYNAMIC_CLASS_LOADER`).
"""

from __future__ import annotations

from oscar.core.loading import default_class_loader

OVERRIDES = {
    ("partner.strategy", "Selector"): "store_backend.catalog.strategy.Selector",
}


def class_loader(module_label, classnames, module_prefix):
    from django.utils.module_loading import import_string  # noqa: PLC0415

    classes = default_class_loader(module_label, classnames, module_prefix)
    return [
        import_string(OVERRIDES[module_label, name])
        if (module_label, name) in OVERRIDES
        else cls
        for name, cls in zip(classnames, classes, strict=True)
    ]

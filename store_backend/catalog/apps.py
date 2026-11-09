from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "store_backend.catalog"
    label = "store_catalog"
    verbose_name = "Store catalog"

    def ready(self) -> None:
        from store_backend.catalog import signals  # noqa: F401, PLC0415
        from store_backend.services import media  # noqa: F401, PLC0415 (publish rule)

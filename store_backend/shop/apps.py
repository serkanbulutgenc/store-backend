from django.apps import AppConfig


class ShopConfig(AppConfig):
    name = "store_backend.shop"
    label = "shop"
    verbose_name = "Store settings"

    def ready(self) -> None:
        from store_backend.shop import signals  # noqa: F401, PLC0415

from django.apps import AppConfig


class StaffConfig(AppConfig):
    name = "store_backend.staff"
    label = "staff"
    verbose_name = "Staff and audit"

    def ready(self) -> None:
        from store_backend.staff import signals  # noqa: F401, PLC0415

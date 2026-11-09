import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from store_backend.api.admin import admin_api
from store_backend.api.store import store_api

APIS = {"store": store_api, "admin": admin_api}


def render(api) -> str:
    # Deterministic output so the committed spec only changes with the code.
    return json.dumps(api.get_openapi_schema(), indent=2, sort_keys=True) + "\n"


class Command(BaseCommand):
    help = (
        "Write openapi-store.json and openapi-admin.json, or --check they are current."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--check", action="store_true")
        parser.add_argument("--output-dir", default=str(settings.BASE_DIR))

    def handle(self, *args, check: bool, output_dir: str, **options) -> None:
        stale = []
        for name, api in APIS.items():
            path = Path(output_dir) / f"openapi-{name}.json"
            content = render(api)
            if check:
                if not path.exists() or path.read_text() != content:
                    stale.append(path.name)
            else:
                path.write_text(content)
                self.stdout.write(f"wrote {path}")
        if stale:
            msg = (
                f"OpenAPI spec is stale: {', '.join(stale)}. "
                "Run `python manage.py export_openapi` and commit the result."
            )
            raise CommandError(msg)

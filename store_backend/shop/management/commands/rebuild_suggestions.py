"""Rebuild the Search suggestion index from the database."""

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from elasticsearch.helpers import BulkIndexError

from store_backend.api.errors import ServiceUnavailableError
from store_backend.services import suggestions


class Command(BaseCommand):
    help = (
        "Index every Search suggestion afresh. The current suggestions stay available "
        "until the new index is complete."
    )

    def handle(self, *args, **options):
        if not suggestions.enabled():
            msg = "Search suggestions are off: set ELASTICSEARCH_URL."
            raise CommandError(msg)
        try:
            written = suggestions.rebuild()
        except suggestions.IndexBusyError:
            msg = "A refresh or rebuild is already running; try again in a minute."
            raise CommandError(msg) from None
        except ServiceUnavailableError as error:
            msg = f"Elasticsearch cannot be reached: {error.__cause__}"
            raise CommandError(msg) from None
        except BulkIndexError as error:
            msg = f"Elasticsearch refused the documents: {error}"
            raise CommandError(msg) from None
        self.stdout.write(f"Indexed {written} suggestions.")

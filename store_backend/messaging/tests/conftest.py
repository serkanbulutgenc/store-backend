import pytest

from store_backend.messaging import transport as transports


@pytest.fixture
def transport():
    """An in-memory relay in place of SMTP for the test."""
    with transports.using(transports.InMemoryTransport()) as relay:
        yield relay

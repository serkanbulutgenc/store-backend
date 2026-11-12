"""Who kicks off delivery of a stored message: the outbox's scheduler seam.

`CeleryScheduler` hands the message to a worker. `InlineScheduler` delivers right away
in the calling process, for setups without a broker. A scheduler that fails must not
undo the caller; the beat sweep (`outbox.dispatch_due`) picks up what was missed.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING
from typing import Protocol

if TYPE_CHECKING:
    from collections.abc import Iterator


class Scheduler(Protocol):
    def kick(self, message_id: int) -> None:
        """Arrange one delivery attempt for the message."""


class CeleryScheduler:
    def kick(self, message_id: int) -> None:
        from store_backend.messaging import tasks  # noqa: PLC0415

        tasks.deliver_message.delay(message_id)


class InlineScheduler:
    def kick(self, message_id: int) -> None:
        from store_backend.services import outbox  # noqa: PLC0415

        outbox.deliver(message_id)


_current: Scheduler = CeleryScheduler()


def get() -> Scheduler:
    return _current


@contextmanager
def using(scheduler: Scheduler) -> Iterator[Scheduler]:
    global _current  # noqa: PLW0603
    previous, _current = _current, scheduler
    try:
        yield scheduler
    finally:
        _current = previous

"""The Owner's Homepage layout API: read it for editing, save the whole list."""

from __future__ import annotations

from ninja import Router
from ninja import Schema

from store_backend.api.permissions import requires
from store_backend.services import homepage
from store_backend.services.homepage import Section  # noqa: TC001 (pydantic needs it)
from store_backend.staff.permissions import Perm

router = Router(tags=["homepage"])


class HomepageLayoutIn(Schema):
    """The sections in page order. Hidden ones are kept, with their settings."""

    sections: list[Section]


class HomepageLayoutOut(Schema):
    sections: list[Section]


@router.get(
    "/homepage",
    auth=requires(Perm.MANAGE_HOMEPAGE),
    operation_id="homepage_get_for_editing",
    response=HomepageLayoutOut,
)
def get_homepage(request):
    """Every section of the layout, hidden ones included, with its settings."""
    return homepage.for_editing()


@router.put(
    "/homepage",
    auth=requires(Perm.MANAGE_HOMEPAGE),
    operation_id="homepage_save",
    response=HomepageLayoutOut,
)
def save_homepage(request, data: HomepageLayoutIn):
    """Replace the whole layout atomically. Live at once; written to the Audit log.
    Section ids must be unique and there are at most 20 sections."""
    return homepage.save(request.auth, data.model_dump()["sections"])

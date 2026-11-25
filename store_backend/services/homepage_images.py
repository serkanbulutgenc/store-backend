"""Images that belong to one Homepage section (store-infra#168).

The Owner or Staff upload them in the Vitrin editor. Each one is a Banner in the
private `layout` Slot: it reuses the Banner image pipeline (checks, derivatives, mobile
image, alt text) and the sections' `banner_id` / `banner_ids` settings, but it is never
listed with the Slots and never public on its own. It shows on the Storefront only when
an approved layout refers to it, so a Staff upload waits for the Owner with the layout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.db import transaction
from django.utils import timezone

from store_backend.api.errors import NotFoundError
from store_backend.catalog.models import Banner
from store_backend.services import audit
from store_backend.services import banners
from store_backend.staff.models import ApprovalStatus

if TYPE_CHECKING:
    from django.core.files.uploadedfile import UploadedFile

    from store_backend.users.models import User

MAX_LOOKUP = 50


def _own() -> Any:
    return Banner.objects.filter(slot=Banner.Slot.LAYOUT)


def _locked(image_id: int) -> Banner:
    image = _own().select_for_update().filter(pk=image_id).first()
    if image is None:
        raise NotFoundError
    return image


def view(image: Banner) -> dict[str, Any]:
    """What the editor shows for one image: its link, alt text and both pictures."""
    return {
        "id": image.pk,
        "link": image.link,
        "alt_text": image.image_alt,
        "image": banners.image_view(image, "image"),
        "mobile_image": banners.image_view(image, "mobile"),
    }


def listing(ids: list[int]) -> list[dict[str, Any]]:
    """The Vitrin images among `ids`, in the order given; any other id is left out."""
    found = {i.pk: i for i in _own().filter(pk__in=ids[:MAX_LOOKUP])}
    return [view(found[i]) for i in ids[:MAX_LOOKUP] if i in found]


def _record(actor: User, image: Banner, summary: str) -> None:
    audit.record(
        actor,
        audit.Action.APPROVAL_EDITED,
        target=image,
        summary=f"{summary}: Vitrin görseli #{image.pk}",
    )


@transaction.atomic
def upload(  # noqa: PLR0913
    actor: User,
    file: UploadedFile,
    *,
    image_id: int | None = None,
    which: str = "image",
    alt_text: str | None = None,
    link: str | None = None,
) -> dict[str, Any]:
    """Store `file` as the main (or mobile) image of a Vitrin image: a new one, or the
    existing `image_id` replaced. The mobile image needs an image to belong to."""
    if image_id is None:
        if which != "image":
            raise NotFoundError
        image = Banner.objects.create(
            slot=Banner.Slot.LAYOUT,
            status=ApprovalStatus.PUBLISHED,
            published_at=timezone.now(),
            link=banners._clean_link(link or ""),  # noqa: SLF001
        )
    else:
        image = _locked(image_id)
        if link is not None:
            image.link = banners._clean_link(link)  # noqa: SLF001
    banners.store_image(actor, image, which, file, alt_text)
    return view(image)


@transaction.atomic
def update(
    actor: User,
    image_id: int,
    *,
    alt_text: str | None = None,
    link: str | None = None,
) -> dict[str, Any]:
    """Change the alt text (it covers both pictures) or the link."""
    image = _locked(image_id)
    if alt_text is not None:
        image.image_alt = banners._clean_alt(alt_text)  # noqa: SLF001
    if link is not None:
        image.link = banners._clean_link(link)  # noqa: SLF001
    image.save()
    _record(actor, image, "Vitrin görseli değişti")
    return view(image)


@transaction.atomic
def remove_mobile(actor: User, image_id: int) -> dict[str, Any]:
    """Drop the mobile picture; the main one stays. Taking an image out of a section is
    done by editing the section, not by deleting the image."""
    image = _locked(image_id)
    banners.remove_image(actor, image_id, "mobile", banner=image)
    return view(image)

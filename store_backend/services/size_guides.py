"""Size guides: one per Product class, with an optional override on a Product."""

from __future__ import annotations

from typing import TYPE_CHECKING

from store_backend.api.errors import NotFoundError
from store_backend.api.errors import UnprocessableError
from store_backend.catalog.models import ProductClassProfile
from store_backend.catalog.sizes import SizeScheme
from store_backend.services import audit

if TYPE_CHECKING:
    from oscar.apps.catalogue.models import Product

    from store_backend.users.models import User


def get_class_guide(class_id: int) -> str:
    return _profile(class_id).size_guide


def set_class_guide(actor: User, class_id: int, text: str) -> str:
    profile = _profile(class_id)
    before, after = profile.size_guide, clean(text, "text")
    if before != after:
        profile.size_guide = after
        profile.save(update_fields=["size_guide"])
        audit.record(
            actor,
            audit.Action.SIZE_GUIDE_EDITED,
            target=profile.product_class,
            summary=f"Beden tablosu: {profile.product_class.name} sınıfı",
            before={"text": before},
            after={"text": after},
        )
    return after


def audit_product_edits(
    actor: User,
    product: Product,
    *,
    guide: tuple[str, str],
    fit_note: tuple[str, str],
) -> None:
    """Record a Product's Size guide override and Fit note edits, if they changed."""
    if guide[0] != guide[1]:
        audit.record(
            actor,
            audit.Action.SIZE_GUIDE_EDITED,
            target=product,
            summary=f"Beden tablosu: {product.title}",
            before={"text": guide[0]},
            after={"text": guide[1]},
        )
    if fit_note[0] != fit_note[1]:
        audit.record(
            actor,
            audit.Action.FIT_NOTE_EDITED,
            target=product,
            summary=f"Kalıp notu: {product.title}",
            before={"text": fit_note[0]},
            after={"text": fit_note[1]},
        )


def _profile(class_id: int) -> ProductClassProfile:
    try:
        return ProductClassProfile.objects.get(product_class_id=class_id)
    except ProductClassProfile.DoesNotExist:
        raise NotFoundError from None


# The Description's cap, kept equal: a guide is Markdown through the same editor, and
# a realistic table with images and tips takes about half of it.
GUIDE_MAX = 5000


def clean(value: object, field: str) -> str:
    """A Size guide: Markdown kept as written, within the length cap."""
    text = str(value or "")
    if len(text) > GUIDE_MAX:
        msg = f"Beden tablosu en fazla {GUIDE_MAX} karakter olabilir."
        raise UnprocessableError(msg, fields={field: [msg]})
    return text


FIT_NOTE_MAX = 120


def clean_fit_note(value: object) -> str:
    """A Fit note is one line of plain text."""
    note = str(value or "").strip()
    if "\n" in note or "\r" in note or len(note) > FIT_NOTE_MAX:
        msg = f"Kalıp notu tek satır ve en fazla {FIT_NOTE_MAX} karakter olmalı."
        raise UnprocessableError(msg, fields={"fit_note": [msg]})
    return note


def effective_guide(profile) -> str | None:
    """The Product's override, else its class's guide, else none.

    A One-size Product has nothing to measure, so it never shows a guide.
    """
    class_profile = profile.product.product_class.profile
    if class_profile.size_scheme == SizeScheme.ONE_SIZE:
        return None
    return profile.size_guide or class_profile.size_guide or None

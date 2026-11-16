"""Public Menus: the Header and Footer menus the Storefront renders."""

from __future__ import annotations

from ninja import Router
from ninja import Schema

from store_backend.services import menus

router = Router(tags=["menus"])


class MenuNode(Schema):
    label: str
    url: str | None
    external: bool
    image_url: str | None
    children: list[MenuNode]


class MenusOut(Schema):
    header: list[MenuNode]
    footer: list[MenuNode]


@router.get("/menus", auth=None, operation_id="menus_get_public", response=MenusOut)
def get_menus(request):
    """Both menus, resolved: hidden and non-public items are left out."""
    return menus.public()

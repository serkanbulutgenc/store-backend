# ruff: noqa: EM101
"""Names, phone numbers and Turkish postal addresses as Shoppers type them.

Shared by checkout, the address book and Staff order edits, so one rule decides what a
valid phone or address is.
"""

from __future__ import annotations

from dataclasses import dataclass

import phonenumbers

from store_backend.api.errors import UnprocessableError

MIN_NAME_PARTS = 2
ADDRESS_PARTS = ("il", "ilce", "mahalle", "street")


@dataclass(frozen=True)
class Address:
    il: str
    ilce: str
    mahalle: str
    street: str
    postal_code: str = ""


def invalid(field: str, message: str) -> UnprocessableError:
    return UnprocessableError(message, fields={field: [message]})


def clean_phone(raw: str) -> str:
    msg = "Geçerli bir telefon numarası girin."
    try:
        parsed = phonenumbers.parse(raw.strip(), "TR")
    except phonenumbers.NumberParseException:
        raise invalid("phone", msg) from None
    if not phonenumbers.is_valid_number(parsed):
        raise invalid("phone", msg)
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def clean_address(prefix: str, address: Address) -> Address:
    parts = {part: getattr(address, part).strip() for part in ADDRESS_PARTS}
    errors = {
        f"{prefix}.{part}": ["Bu alan zorunludur."]
        for part, value in parts.items()
        if not value
    }
    if errors:
        raise UnprocessableError(fields=errors)
    return Address(**parts, postal_code=address.postal_code.strip())

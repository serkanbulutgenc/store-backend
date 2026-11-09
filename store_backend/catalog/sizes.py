"""Fixed size lists per scheme; a Variant's size must come from its class's list."""

from __future__ import annotations

from enum import StrEnum


class SizeScheme(StrEnum):
    LETTERS = "letters"
    NUMBERS = "numbers"
    SHOES = "shoes"
    BRA = "bra"
    ONE_SIZE = "one_size"


ONE_SIZE_LABEL = "Tek Ebat"

SIZES: dict[str, list[str]] = {
    SizeScheme.LETTERS: ["XS", "S", "M", "L", "XL", "XXL"],
    SizeScheme.NUMBERS: [str(n) for n in range(34, 52, 2)],
    SizeScheme.SHOES: [str(n) for n in range(35, 43)],
    SizeScheme.BRA: [f"{band}{cup}" for band in range(70, 105, 5) for cup in "ABCD"],
    SizeScheme.ONE_SIZE: [ONE_SIZE_LABEL],
}


def sizes_for(scheme: str) -> list[str]:
    return SIZES[scheme]

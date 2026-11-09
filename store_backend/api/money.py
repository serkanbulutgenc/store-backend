from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer
from pydantic import WithJsonSchema

_CENT = Decimal("0.01")

# Money is TRY, KDV-inclusive, serialised as a decimal string: "349.90".
Money = Annotated[
    Decimal,
    PlainSerializer(lambda d: str(d.quantize(_CENT)), return_type=str),
    WithJsonSchema(
        {"type": "string", "pattern": r"^-?\d+\.\d{2}$", "examples": ["349.90"]},
    ),
]

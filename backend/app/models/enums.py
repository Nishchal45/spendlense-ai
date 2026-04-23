from collections.abc import Callable
from enum import Enum, StrEnum
from typing import TypeVar

from sqlalchemy import Enum as SAEnum

E = TypeVar("E", bound=Enum)


def pg_enum(enum_cls: type[E], name: str) -> SAEnum:
    """Build a Postgres ``ENUM`` column bound to the Python enum's *values*.

    SQLAlchemy defaults to sending ``.name`` (e.g. ``FOOD_DINING``), but
    our migration created the type with lowercase labels
    (``food_dining``) to keep the wire format and DB contract
    lowercase. ``values_callable`` tells SA to use ``.value`` so the two
    stay in sync. Without this, every insert that touches an enum
    column explodes at runtime with ``InvalidTextRepresentationError``.
    """

    def _values(e: type[Enum]) -> list[str]:
        return [str(member.value) for member in e]

    # ``Callable`` cast keeps mypy --strict happy about the callable's shape.
    vc: Callable[[type[Enum]], list[str]] = _values
    return SAEnum(enum_cls, name=name, values_callable=vc)


class ExpenseCategory(StrEnum):
    FOOD_DINING = "food_dining"
    GROCERIES = "groceries"
    TRANSPORTATION = "transportation"
    SHOPPING = "shopping"
    ENTERTAINMENT = "entertainment"
    UTILITIES = "utilities"
    HEALTHCARE = "healthcare"
    HOUSING = "housing"
    TRAVEL = "travel"
    EDUCATION = "education"
    PERSONAL = "personal"
    OTHER = "other"


class ExpenseSource(StrEnum):
    MANUAL = "manual"
    RECEIPT = "receipt"
    IMPORT = "import"


class ReceiptStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PARSED = "parsed"
    CATEGORISED = "categorised"
    FAILED = "failed"


class OcrMethod(StrEnum):
    TESSERACT = "tesseract"
    GPT4V = "gpt4v"


class BudgetPeriod(StrEnum):
    MONTHLY = "monthly"

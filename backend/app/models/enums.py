from enum import StrEnum


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

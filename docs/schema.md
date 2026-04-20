# Database schema

Designed around the queries the app actually runs, not around abstract data
modelling. Every index below traces back to a query on this page.

## Entities

```
User ─┬─< Expense ─< LineItem
      ├─< Budget
      ├─< Receipt (─ Expense, 1:1 when processed)
      └─< CategoryCorrection
```

## Tables

### users

| column          | type          | notes                         |
| --------------- | ------------- | ----------------------------- |
| id              | uuid PK       |                               |
| email           | text UNIQUE   | lower-cased on write          |
| password_hash   | text          | bcrypt                        |
| created_at      | timestamptz   | default `now()`               |
| updated_at      | timestamptz   |                               |

### expenses

| column          | type          | notes                                         |
| --------------- | ------------- | --------------------------------------------- |
| id              | uuid PK       |                                               |
| user_id         | uuid FK users | on delete cascade                             |
| merchant_name   | text          | indexed for correction lookups                |
| amount          | numeric(12,2) | store cents precision — never float for money |
| currency        | char(3)       | default 'USD'                                 |
| category        | text enum     | see `ExpenseCategory`                         |
| expense_date    | date          |                                               |
| description     | text          | nullable                                      |
| receipt_id      | uuid FK       | nullable                                      |
| source          | text enum     | `manual`, `receipt`, `import`                 |
| created_at      | timestamptz   |                                               |
| updated_at      | timestamptz   |                                               |

Indexes:
- `(user_id, expense_date DESC)` — drives the expense list + monthly windows
- `(user_id, category)` — drives category rollups
- `(merchant_name)` — used by the corrections cache lookup

### line_items

| column          | type          | notes                      |
| --------------- | ------------- | -------------------------- |
| id              | uuid PK       |                            |
| expense_id      | uuid FK       | on delete cascade          |
| description     | text          |                            |
| quantity        | numeric(10,2) | default 1                  |
| unit_price      | numeric(12,2) | nullable                   |
| total_price     | numeric(12,2) |                            |

### receipts

| column              | type          | notes                                                         |
| ------------------- | ------------- | ------------------------------------------------------------- |
| id                  | uuid PK       |                                                               |
| user_id             | uuid FK       | on delete cascade                                             |
| storage_key         | text          | S3 key: `{user_id}/{uuid}_{filename}`                         |
| mime_type           | text          |                                                               |
| file_size_bytes     | integer       |                                                               |
| status              | text enum     | `uploaded`, `processing`, `parsed`, `categorised`, `failed` |
| ocr_method          | text enum     | `tesseract`, `gpt4v`                                          |
| ocr_confidence      | numeric(5,2)  | 0–100                                                         |
| raw_text            | text          | OCR output, nullable                                          |
| parsed_payload      | jsonb         | parsed fields before write                                    |
| error_message       | text          | set when status = failed                                      |
| created_at          | timestamptz   |                                                               |
| processed_at        | timestamptz   | nullable                                                      |

Indexes:
- `(user_id, created_at DESC)` — receipts list
- `(status)` — worker queries by status (e.g. retry stuck receipts)

### budgets

| column              | type          | notes                                        |
| ------------------- | ------------- | -------------------------------------------- |
| id                  | uuid PK       |                                              |
| user_id             | uuid FK       | on delete cascade                            |
| category            | text enum     |                                              |
| amount              | numeric(12,2) |                                              |
| period              | text enum     | `monthly` for now                            |
| alert_threshold_pct | integer       | default 80                                   |
| active              | boolean       | default true                                 |
| created_at          | timestamptz   |                                              |

Unique on `(user_id, category, period)` — one active budget per category per
period.

### category_corrections

| column          | type          | notes                              |
| --------------- | ------------- | ---------------------------------- |
| id              | uuid PK       |                                    |
| user_id         | uuid FK       |                                    |
| merchant_name   | text          | lower-cased on write               |
| category        | text enum     | the user-corrected category        |
| occurrence_count| integer       | incremented on repeat corrections  |
| last_applied_at | timestamptz   |                                    |

Unique on `(user_id, merchant_name)` — one correction per merchant per user.
Lookup index is the same unique key.

## Enum: ExpenseCategory

Keep it small. Too many categories defeat the point of auto-categorisation.

```
food_dining
groceries
transportation
shopping
entertainment
utilities
healthcare
housing
travel
education
personal
other
```

## Queries that drove the indexes

1. **Monthly breakdown by category** — `SELECT category, SUM(amount) FROM
   expenses WHERE user_id = $1 AND expense_date >= $2 AND expense_date < $3
   GROUP BY category` → `(user_id, expense_date)` + category fits in row.
2. **Expense list with pagination** — `SELECT * FROM expenses WHERE user_id
   = $1 ORDER BY expense_date DESC, id DESC LIMIT $n OFFSET $m` → same index.
3. **Correction cache hit** — `SELECT category FROM category_corrections
   WHERE user_id = $1 AND merchant_name = $2` → unique index.
4. **Category rollup across all time** — `(user_id, category)` composite.
5. **Budget check** — `SELECT amount FROM budgets WHERE user_id = $1 AND
   category = $2 AND period = 'monthly' AND active` → unique index.

## Money representation

`numeric(12,2)` everywhere for monetary amounts. Never float. The two decimal
digits map directly to cents / paise. The 12-digit limit comfortably covers
any realistic personal spend and leaves room for JPY-style currencies later.

## Soft deletes

Not doing them. If a user deletes an expense, it's gone. Receipts are
deleted with the user via cascade. Recovery is a backup concern, not a model
concern.

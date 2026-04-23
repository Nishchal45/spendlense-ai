# Architecture Decision Records

ADRs capture *why* we built it this way, so six months from now the
answer to "why did we pick bcrypt" isn't "nobody remembers".

Every non-trivial decision lands here before or alongside the PR that
implements it. Use `0000-template.md` as the starting point.

| ID | Title | Status |
| -- | ----- | ------ |
| [0001](0001-jwt-auth-over-sessions.md) | JWT access tokens over server-side sessions | Accepted |
| [0002](0002-receipt-ingestion-channels.md) | Zero-touch receipt ingestion channels (email + Gmail OAuth + share sheet) | Accepted |
| [0003](0003-expenses-api.md) | Expenses CRUD shape (keyset pagination, ETag/If-Match, 404 on cross-tenant) | Accepted |

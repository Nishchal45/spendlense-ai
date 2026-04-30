# ADR-0008: Forward-to-email ingestion

- **Status**: Accepted
- **Date**: 2026-04-30
- **Deciders**: backend, product

## Context

ADR-0002 picked **forward-to-email** as the first zero-touch
ingestion channel. This ADR records the load-bearing decisions
made shipping it (PRs #33–#35). Five questions had to land
together:

1. **Address scheme.** What does the forward-to-email address
   actually look like, and what does the local-part suffix
   contain?
2. **Webhook auth.** The handler isn't behind the JWT (it talks to
   a managed provider, not a logged-in user). What proves a
   delivery is genuine?
3. **Replay protection.** Providers retry; users with naive
   forward rules can loop. How do we avoid creating the same
   receipt twice?
4. **Provider portability.** Postmark / SES Inbound / Mailgun
   Routes each send different JSON. Do we lock to one or stay
   neutral?
5. **What to do with body-only emails.** Many digital receipts
   *are* the body, not an attachment. Does this PR handle that?

We're shipping inside the same constraints: self-hosted-first,
Postgres-only, single dev, deferred complexity.

## Decision

**Per-user 128-bit token in the local-part suffix**
(`receipts+<32-hex>@<configured-domain>`), validated via a
**Stripe-style HMAC-SHA256 signature** on every webhook delivery,
deduped on a **per-user `external_message_id`** with a partial
unique index, against a **provider-agnostic canonical payload**
that adapter modules (out of scope here) translate into. Body-
only emails are explicitly deferred.

Every choice in detail:

### Address scheme: 128-bit hex token, not derived

The address is `receipts+<token>@<inbox_email_domain>`. The
token is a `secrets.token_hex(16)` minted at signup — 128 bits of
entropy, not derived from the user id.

Three properties this gives us:

- **Leaked addresses don't leak identity.** A spam dump that
  contains the address can't be reversed into a user id.
- **Rotatable.** A compromised token can be regenerated without
  churning the user id (Phase 6+ adds the rotate UI).
- **Single index lookup.** The webhook resolves a token to a user
  in one round trip — `String(32) UNIQUE` index on
  `users.inbox_token`.

The local-part uses Gmail's `+suffix` convention so a single
configured MX (`receipts@inbox.spendlens.app`) routes every
user's mail to the same provider mailbox. The provider parses
the suffix and includes it in the webhook payload's `to` field;
our regex on `receipts+<32-hex>@` does the resolution.

### Webhook auth: Stripe-style versioned HMAC

Header: `X-SpendLens-Signature: t=<unix>,v1=<hex>`. The signature
is `HMAC-SHA256(secret, "<timestamp>.<raw body>")` with
`INBOUND_EMAIL_SECRET` shared between us and the provider.

Why Stripe's pattern specifically:

- **Versioned algorithm.** `v1=` lets us rotate to Ed25519
  (or whatever the next decade's HMAC-vs-PQ trade looks like)
  without a parser break. Old deliveries reject; new ones don't
  need a release.
- **Timestamp inside the signed string.** Including `<ts>.`
  before the body in the signing input means an attacker can't
  swap `t=` to dodge the replay check.
- **Constant-time compare.** `hmac.compare_digest` runs in time
  proportional to the input length, not the matching prefix —
  closes the obvious side channel.

5-minute replay window. The window alone only narrows
"replay-from-the-same-bytes"; the per-user dedup on
`external_message_id` is what makes retries / forward-rule loops
truly idempotent.

Rejected: Basic Auth at the proxy (Postmark's default). Two
problems: (a) it's a static secret with no per-message integrity
guarantee, so a compromised shared secret is replayable forever
against any payload; (b) Basic Auth credentials in URLs leak
into logs.

Rejected: signed JWTs from the provider. Same outcome (per-
message integrity) with more crypto and a key-rotation story we
don't need at this scale.

### Dedup: per-user partial unique index on `external_message_id`

`receipts.external_message_id` is `String(255), nullable`. A
**partial unique index** —
`(user_id, external_message_id) WHERE external_message_id IS NOT NULL` —
enforces uniqueness only on email-sourced rows; manual uploads
leave the column NULL and are unaffected.

Why per-user, not global:

- **Provider message IDs aren't globally unique.** Gmail's
  `Message-ID` is "globally unique" in theory but in practice
  collisions across mailboxes happen (auto-generated headers,
  same forwarder feeding multiple users). Per-user dedup
  avoids cross-user blocking.
- **Cross-user collisions would be a denial-of-service vector.**
  An attacker who knows another user's `Message-ID` could spoof
  it on their own forward to block legitimate ingestion.

The dedup happens before the row is inserted, so a re-delivery
returns 202 with `deduped: true` rather than wasting an S3 PUT
on bytes we already stored.

### Provider portability: canonical payload + adapters

The route receives a `InboundEmail` Pydantic model with the
fields we need:

```
{ message_id, to, sender, subject, body_plain, attachments[] }
```

Each provider sends different JSON. Postmark wraps everything in
`{From, To, Subject, MessageID, Attachments}`; SES Inbound
arrives via SNS with a multipart S3 reference; Mailgun POSTs
URL-encoded form data. **Translating each provider's shape into
our canonical one is an adapter's job** — out of scope for this
phase. The route stays clean: signature → validate → process.

Inline base64 attachments rather than URL references. Two
reasons:

- **Tests.** A URL means a second network hop in the worker that
  has to be mocked or proxied through localstack.
- **Reproducibility.** A captured webhook payload is fully
  self-describing; replaying it doesn't depend on the
  provider's URL still resolving.

The adapter layer can fetch URLs and inline them before calling
the canonical handler; the size cap (10 MiB per attachment,
mirrored from direct upload) keeps the payload bounded.

### Status code map: each path tells a different story

- **`202`** with `deduped` flag. Always 202 on a "successful
  delivery" — provider retries respect that and stop pushing.
- **`400`** for malformed JSON post-signature. The signature
  passed; the bytes are ours; the shape isn't.
- **`401`** for any signature failure (missing header, malformed
  header, stale timestamp, mismatched HMAC). One reason code so
  an attacker can't fingerprint which step tripped them.
- **`404`** for unknown `receipts+<token>`. The post is
  syntactically fine but semantically points at no user.
- **`503`** when `INBOUND_EMAIL_SECRET` is unset. Visible
  misconfiguration vs. silent 401s on every delivery.

### Body-only receipts: explicitly deferred

Many email receipts (Uber, DoorDash, Amazon) are HTML in the
body, not a PDF attachment. Phase 5.5 only processes
attachments. Phase 6+ adds:

- HTML-to-PDF rendering of the body
- Or direct LLM extraction from `body_plain` / `body_html`

Keeping body parsing out of this PR means one less LLM dep on the
ingestion path and one less failure mode — attachment-only
receipts (Lyft, Airlines, most B2B tools) cover the high-value
cases.

## Consequences

### Positive

- **Killer "set up once" UX.** Gmail filter → forward to
  `receipts+<token>@…` → SpendLens does the rest. The user
  never copies a receipt again.
- **Works on every email provider.** Not just Gmail. Outlook /
  iCloud / Fastmail / Proton all support forwarding rules.
- **iOS SMS workaround.** A user long-presses an SMS receipt,
  taps Share → Mail → forwards to their address. Same channel,
  no platform review.
- **Auth doesn't depend on the user's session.** The provider
  posts hours after the user closed the app; the HMAC secret is
  what proves the delivery is real.
- **Provider-portable.** Three vendors compatible without code
  duplication — adapters are thin enough to live in one file
  each.
- **Idempotent retries.** A flaky provider that delivers the
  same email three times produces one receipt.

### Negative

- **The token is a long-lived bearer.** A user who pastes
  their address into a public Slack channel exposes a write
  surface. Mitigated by: rotation (future), the narrow
  privilege (write-only, no read-back), and the dedup (an
  attacker firing the same payload twice doesn't multiply
  damage).
- **Body-only receipts don't ingest yet.** Uber email receipts
  attach a PDF, but DoorDash and many B2B tools don't.
  Workaround: forward those manually or wait for Phase 6+.
- **Adapter layer not shipped.** A self-hoster who wants
  Postmark today has to write their own adapter (60 lines).
  ADR follow-up will land canonical examples for the three
  major providers.
- **Per-message size cap inherited from direct upload.** A
  10 MiB ceiling on each attachment is fine for receipts but
  excludes some long-form expense reports. Configurable, not
  rewritten.
- **No anti-abuse rate limit.** A hostile sender who knows a
  token could fire 100k receipts in a minute. Mitigated only
  by provider-side rate limits; an explicit per-user cap is a
  Phase 8 hardening item.

### Follow-ups

- **Token rotation UI.** "Rotate my forwarding address" button
  on the receipts page; old token grace period of N days so
  Gmail filters can be updated.
- **Body-only receipt support** via HTML→PDF rasterisation +
  the existing OCR path.
- **Adapter modules** for Postmark, SES Inbound, Mailgun
  Routes. Each is ~60 lines that translates the provider's
  JSON into `InboundEmail`.
- **Per-user delivery rate limiting** as Phase 8 abuse
  hardening.
- **Inbound DKIM / SPF passthrough.** Surface the verification
  status in the parsed payload so the dashboard can flag
  "this receipt was forwarded from an unverified sender" —
  guards against spoofed receipts.

## Alternatives considered

### Token derived from `user_id` (HMAC, not random)

Rejected. A leaked address would be reversible into the user id
(an attacker with the secret could walk the index), and rotation
would either change the user id or require a separate "valid
tokens for user X" table. The 128-bit random hex skips all of
that.

### Address shape: `<token>@receipts.spendlens.app` (no plus-suffix)

Considered. The plus-suffix lets us run one configured mailbox
on the provider; per-token addresses would mean either a
catch-all (provider quirk) or a per-user provider call to mint
a real address. Plus-suffix wins on simplicity.

### Webhook auth via the JWT

Rejected. The provider doesn't have access to the user's JWT —
it doesn't know who the user is. Even if we minted a per-user
provider credential, that's a second secret store with no
material benefit over a single shared HMAC.

### Global `Message-ID` UNIQUE constraint

Rejected. Two users can plausibly receive the same `Message-ID`
(forwarders that don't rewrite headers). A global UNIQUE would
turn one user's spoofed `Message-ID` into a denial of service
against the legitimate other user.

### One Receipt per email body, attachments inlined

Rejected. Receipts are usually one PDF / one image per file. A
single email with three attachments is three independent
receipts in our domain model, not one composite. Splitting
matches the OCR pipeline's per-row contract and simplifies
retry semantics.

### Stripe's full multi-version signature header (`v1=…,v2=…`)

Considered. The full header supports rolling rotations of the
HMAC algorithm. We ship `v1` only — a real key rotation is rare
enough to handle in a small follow-up PR rather than baking
unused parser branches into Phase 5.5.

### Locking the route to one provider (Postmark)

Rejected. Self-hosted users who use AWS will lean toward SES
Inbound for free-tier volume. Mailgun is the cheapest paid path
in low-volume territory. A canonical payload + adapter pattern
keeps deploy choice open.

## References

- Stripe webhook signing:
  <https://docs.stripe.com/webhooks#verify-official-libraries>
- ADR-0002 (Channel A — forward-to-email):
  [`adr/0002-receipt-ingestion-channels.md`](0002-receipt-ingestion-channels.md)
- `secrets.token_hex` semantics:
  <https://docs.python.org/3/library/secrets.html#secrets.token_hex>
- Postgres partial unique indexes:
  <https://www.postgresql.org/docs/current/indexes-partial.html>
- `backend/app/services/inbound_email_service.py`,
  `backend/app/api/v1/endpoints/inbound.py`,
  `backend/app/schemas/inbound_email.py`,
  `backend/alembic/versions/0002_user_inbox_token.py`,
  `backend/alembic/versions/0003_receipts_external_message_id.py`,
  `frontend/src/components/receipts/InboxAddressCard.tsx`.

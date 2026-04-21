# ADR-0002: Zero-touch receipt ingestion channels

- **Status**: Accepted
- **Date**: 2026-04-21
- **Deciders**: backend, product

## Context

The core friction in every expense tracker is manual entry. Mint, YNAB,
and Copilot all lose users at the "add another expense" step. Most
modern receipts already arrive digitally — Uber, Amazon, DoorDash,
airlines, and many sit-down restaurants email a receipt; Indian and
South-East-Asian banks SMS transaction alerts.

We want SpendLens to ingest those digital receipts without the user
doing anything per-transaction. The product instinct is "pull from
Gmail and SMS". The platform reality is more constrained:

- **iOS does not allow third-party apps to read SMS.** No entitlement,
  no workaround, no roadmap item from Apple to change that.
- **Android allows it**, but Play Store's Restricted Permissions policy
  (READ_SMS) limits the permission to apps whose core function is
  messaging. A finance app will likely be rejected or have to justify
  the permission at each release.
- **Gmail API access** is available via OAuth 2.0, but any app with
  `gmail.readonly` scope needs to pass Google's security and privacy
  verification before public use — a 4-to-8-week process.

The decision has to balance "real user value" against "what the
platforms actually let us ship".

## Decision

Ship **three converging ingestion channels** that all feed the same OCR
and categorisation pipeline (ADR-0003 will cover that pipeline):

### Channel A — Forward-to-email (Phase 5.5, first to ship)

Each user gets a unique forwarding address minted on signup, of the
shape:

```
receipts+<opaque-token>@inbox.spendlens.app
```

The token is a 128-bit random string, not derived from the user id —
so a leaked address can't be reversed into a user identity and can be
rotated without creating a new account.

Inbound email hits a managed provider (Postmark, SES Inbound, or
Mailgun Routes) which POSTs a parsed JSON webhook to
`POST /api/v1/inbound/email`. The handler validates the provider's
signature, resolves the token to a user, and enqueues the message
(body + attachments) to the ingestion queue.

Why this channel first:

- Works on **every** email provider (Gmail, Outlook, iCloud, Fastmail,
  self-hosted).
- Works as the **SMS fallback on iOS** — the user long-presses an SMS,
  taps Share, picks Mail, sends it to their forwarding address.
- Zero OAuth, zero platform review. Ships in about a week.
- One implementation covers both the web and future mobile clients.

### Channel B — Gmail OAuth watcher (Phase 5.6)

For users who don't want to maintain a forward-rule, we offer a
one-time OAuth consent. Scope requested is the narrowest that works:

```
https://www.googleapis.com/auth/gmail.readonly
```

We use Gmail's push-notification model (Cloud Pub/Sub), not polling:

1. User grants consent → we store refresh token encrypted at rest.
2. We call `users.watch` with a label filter (e.g. `receipts` label or
   from-header patterns) and subscribe our Pub/Sub topic.
3. Gmail pushes a notification on each matching message; we pull the
   full message via `users.messages.get` and enqueue it to the same
   ingestion queue as Channel A.

Why push not poll: no quota waste, no polling-interval vs. latency
trade-off, and the per-user cost is bounded by their email volume.

The same backend will speak **generic IMAP IDLE** for Outlook / iCloud
/ Fastmail users — the protocol differs, the downstream pipeline
doesn't.

### Channel C — Mobile share sheet (Phase 7 web, Phase 8 native)

On the web, register a PWA `share_target` in the manifest. On iOS and
Android native, ship a share extension. All three hit the same
`POST /api/v1/receipts` upload endpoint that manual uploads use.

This covers every app that produces receipts outside email: SMS,
WhatsApp, Venmo, Zelle, bank push notifications. **This is how we
"support SMS on iOS"** without claiming a platform capability we don't
have.

### Optional Channel D — Android READ_SMS (Phase 8, flagged)

Only if Play Store approves the permission justification, and only
behind an explicit "I understand" toggle in settings. Parses the user's
inbox for known bank/merchant SMS patterns (per-region rule packs).
Never shipped to iOS.

## Consequences

### Positive

- **Platform-honest.** No feature promises Apple won't let us keep.
- **One pipeline, many mouths.** OCR + categorisation don't care where
  a receipt came from.
- **Incremental rollout.** Channel A ships without OAuth review. B
  ships after verification. C ships with the frontend.
- **Privacy story is clear.** The forwarding address is a capability
  URL; the Gmail scope is read-only; SMS stays on-device for Android.

### Negative

- Channel A depends on a transactional-email provider (Postmark/SES).
  Adds an external dep but not a hard one — self-hosters can point at
  their own Postfix with a webhook bridge.
- Gmail OAuth verification gates Channel B for public launch. We can
  ship to a small allowlist during review.
- SMS-via-share-sheet requires one user tap per receipt. Not truly
  zero-touch on iOS. Documented up front.

### Follow-ups

- ADR-0003: OCR + categorisation pipeline (consumes all three channels).
- ADR-0004: inbound-email webhook signing and replay protection.
- Per-user domain allow/deny list — e.g. "ignore mail from
  newsletters@..." to prevent marketing emails from becoming expenses.
- Rate limiting on `/api/v1/inbound/email` — a runaway mail loop could
  flood the queue.
- Dedup strategy: (user_id, message_id_hash) index so re-delivery of
  the same email doesn't create two expenses.

## Alternatives considered

### "Just poll Gmail every 15 minutes"

Rejected. Uses API quota for every user regardless of mail volume,
introduces a 0-15 minute latency floor, and doesn't scale past a few
hundred users without engineering around quota limits. Push is
strictly better once you've done the Pub/Sub setup.

### Use Plaid / bank transaction feeds instead of receipts

Rejected — out of scope for SpendLens. The product promise is
"receipts in, categorised spending out, data stays on your box". Bank
credential feeds break the third promise and change the product.

### Ship SMS reading on iOS via a workaround

There is no workaround. Apple's sandbox prevents it and rejections are
mechanical. Ruled out at the planning stage so we don't build toward a
wall.

### Skip email forwarding, do only OAuth

Rejected. Forwarding is the lowest-friction path for non-Gmail users,
and doubles as our SMS workaround on iOS. Dropping it would cut our
addressable users roughly in half (iCloud, Outlook, and corporate-mail
users) and make the iOS SMS story significantly worse.

### Ship raw SMTP ingest instead of a provider webhook

Rejected for Phase 5.5. Running an SMTP listener on a public droplet
is a spam-filtering and TLS-certificate rabbit hole. Provider webhooks
sidestep it for a few dollars a month. Self-hosters who object get a
documented recipe for Postfix + a tiny bridge script — same webhook
contract either way.

## References

- Gmail API: https://developers.google.com/gmail/api/guides/push
- Play Store Restricted Permissions:
  https://support.google.com/googleplay/android-developer/answer/9047303
- Apple — no public API for reading SMS; official messaging
  extensibility is limited to `MessageFilterExtension` for spam filters
  only.
- PWA share target: https://web.dev/web-share-target/
- `docs/roadmap.md` Phases 5.5, 5.6, 7, 8.

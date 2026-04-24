# ADR-0004: Receipt upload, storage, and download

- **Status**: Accepted
- **Date**: 2026-04-23
- **Deciders**: backend

## Context

Phase 4 is the first feature that writes **blobs**, not rows. The OCR
pipeline in Phase 5 reads from whatever this phase lands, so a handful
of decisions here lock in for the rest of the product:

1. **Where do receipt bytes live?** Inside Postgres as `bytea`, on
   local disk, or in an object store?
2. **What goes on the wire to name them?** A user-visible URL, a UUID,
   or an opaque key?
3. **Two systems, one write.** Every upload is an S3 PUT *and* a
   Postgres INSERT. Either can fail independently — we need a story
   for partial writes.
4. **How do clients download?** Proxied through the API, or handed a
   direct URL?
5. **What content do we accept, and how do we enforce it?** Browsers
   send whatever `Content-Type` the user's OS guesses; a malicious
   client sends whatever it wants.
6. **Per-request size cap.** An unbounded upload pins an API worker
   and fills the bucket.

Constraints we're working inside:

- Self-hosted-first: everything has to work against MinIO on a laptop
  and against real S3 in prod without a code change.
- Phase 5 queue will read these blobs from a worker — the storage
  abstraction can't be web-only.
- Users upload from phones: 2–5 MiB JPEGs, occasionally HEIC, rarely
  PDF.

## Decision

Ship an S3-compatible object store behind a narrow async wrapper, name
blobs with HMAC-prefixed opaque keys, write S3-first and delete-on-DB-fail,
serve downloads via short-lived signed URLs, validate content by
magic-byte sniffing, and cap uploads at 10 MiB.

### Object store: S3-compatible via aioboto3

MinIO locally, real S3 (or any S3-compatible: R2, B2, DigitalOcean
Spaces) in prod. The client is `aioboto3` — native async, same API as
`boto3`, so moving off MinIO is a config change, not a code change.

Rejected: `bytea` in Postgres (blows up the WAL, makes every backup
painful, destroys streaming downloads); local disk (doesn't survive a
container restart, forks into a bespoke GC problem in Phase 8 when we
deploy behind a load balancer).

### Object-key scheme: HMAC prefix + month shard + UUID leaf

```
receipts/<16-hex HMAC>/<yyyy>/<mm>/<uuid>.<ext>
```

- **`HMAC(user_id, secret)[:16]`** — deterministic per user (so all of
  one user's receipts share a prefix for cheap listing) but opaque on
  the wire (the user id isn't recoverable from the key).
- **`<yyyy>/<mm>`** — month shard. Keeps bucket listings tractable and
  makes cold-storage lifecycle rules trivial when we need them.
- **`<uuid>`** — collision-free leaf; even if two uploads hit the same
  millisecond, UUID4 doesn't collide.
- **`.<ext>`** — derived from MIME, *not* the client-supplied filename.
  The client-supplied name never hits the key.

Storage keys are **not** exposed on the wire. `ReceiptOut` omits the
field; downloads flow through `GET /receipts/{id}/url` instead. That
lets us rotate prefixes, change buckets, or swap backends without
breaking clients.

The HMAC secret rides on `JWT_SECRET`. Rotating it means new uploads
get new prefixes; old uploads keep working because the key is stored
verbatim in the row.

### Write ordering: S3 first, then DB, with cleanup on DB failure

Two external systems, one logical write. Two orderings were on the
table:

- **DB first, then S3.** Attractive: DB rollback covers the "S3 put
  failed" case cleanly. Fatal: if the S3 put succeeds and we crash
  before flushing the transaction, the blob is orphaned and we never
  know it existed.
- **S3 first, then DB.** If the DB insert fails, we have a known key
  and can issue a cleanup `DELETE`. If we crash between the two, we
  have a known orphan that a Phase 6+ sweeper can reconcile by
  diffing the bucket against the `receipts` table.

We picked S3-first. The explicit cleanup lives in `create_receipt()`:
on any exception in the flush/commit, we `await delete_object(key=key)`
and re-raise. S3 delete is idempotent by contract, so the cleanup
itself is safe to retry.

### Downloads: signed URLs, not proxying through the API

`GET /receipts/{id}/url` returns a 5-minute signed URL. The client
does the actual fetch against the object store directly.

Why not proxy bytes through the API:

- Proxying pins an API worker on the wire for the duration of the
  download — a slow phone connection ties up a connection for
  minutes.
- Signed URLs get CDN caching for free in prod.
- S3's own access control is battle-tested; rebuilding it in-app
  would be a downgrade.

Trade-offs we accepted:

- The URL is a bearer token. Five minutes of exposure is the blast
  radius if it leaks (shoulder-surfed, logged, screenshotted). We
  think the UX cost of a shorter TTL (downloads that fail on a slow
  mobile connection) is worse than the security cost of 300 seconds.
- MinIO in local dev signs URLs against the internal hostname
  (`http://minio:9000`). The test suite talks to MinIO on the same
  compose network, so it works. A browser on the host would need the
  `127.0.0.1:9000` form; we'll reconcile that in Phase 7 when the SPA
  lands (either via a reverse-proxy alias or a signed-URL rewrite
  middleware).

### Content validation: sniff magic bytes, don't trust Content-Type

`Content-Type` is client-supplied and therefore worthless as a gate.
We sniff the first 32 bytes of the body against a hard-coded magic-byte
table:

- JPEG — `FF D8 FF`
- PNG — `89 50 4E 47 0D 0A 1A 0A`
- PDF — `%PDF-`
- WEBP / HEIC — ISO BMFF `ftyp` box at bytes 4..8, brand at 8..12

Anything else is `415 Unsupported Media Type`. Empty body is also
`415` (the service distinguishes internally via the exception
message, but the wire contract stays simple).

Rejected: `python-magic` / `libmagic`. Adds a native dep for five
file types. We know exactly what we accept.

### Size cap: 10 MiB

Modern phone JPEGs are 2–5 MiB, HEIC is smaller, PDFs rarely crack
10 MiB for a single receipt. The cap lives in the service
(`MAX_UPLOAD_BYTES`); oversized bodies return `413 Payload Too Large`.

We read the full body into memory before checking — acceptable at
10 MiB. If we ever raise the cap we switch to streaming multipart
parsing and check the length on the content-length header before
reading bytes.

### Ownership and error codes: 404 across the board

Same rule as Phase 3: cross-user access returns `404 Not Found`, not
`403`. Existence of another user's receipts is not probable through
this API.

## Consequences

### Positive

- **Backend-agnostic storage.** Swapping MinIO for real S3, R2, or
  B2 is a `.env` change.
- **No user id in keys.** A leaked key doesn't tell an attacker whose
  receipt it was.
- **Known-orphan recovery.** The S3-first ordering means every failure
  leaves data we can find and clean up, not data we can't see.
- **Downloads scale for free.** Signed URLs push bytes directly from
  the object store to the client; the API worker is off the critical
  path.
- **Content validation can't be bypassed by a hostile client.** Magic
  bytes don't lie.

### Negative

- **Explicit blob cleanup on DB failure is cross-system code.** If
  the cleanup itself crashes (S3 unreachable at exactly the wrong
  moment), we strand a blob. The Phase 6+ sweeper handles that tail
  case by reconciling the bucket against the `receipts` table.
- **Signed URLs are bearer credentials.** A link shared in a Slack
  thread is live for five minutes.
- **Presigned URLs encode the internal hostname in local dev.** Works
  fine for the test suite and the API-to-API call path; the SPA
  story gets resolved in Phase 7.
- **10 MiB cap is a product decision, not a physics one.** Some legit
  receipts (multi-page PDFs from B2B vendors) will exceed it. We
  revisit when a user complains.

### Follow-ups

- **Virus scanning.** ClamAV in the OCR worker — receipts are still
  executable in a browser if the client ignores `Content-Disposition`.
  Not a Phase 4 blocker because the only consumer of these blobs
  today is our own OCR pipeline.
- **Bucket lifecycle policy.** Move blobs older than N months to
  cheaper storage when we get to Phase 8 prod deploy.
- **Sweeper for orphaned keys.** Nightly cron that lists the bucket
  and left-anti-joins with `receipts.storage_key`, deletes the
  delta. Phase 6+.
- **Per-user upload rate limiting.** Trivial to spam-upload 10 000
  valid JPEGs. Deferred to Phase 8 alongside public-launch hardening.
- **`Content-Disposition: attachment` on signed URLs.** Belt-and-
  braces against browser-executed HTML smuggled inside a JPEG. Add
  when the SPA lands; needs `ResponseContentDisposition` on the
  presign call.

## Alternatives considered

### Proxy downloads through the API

Rejected. API workers are the most expensive thing in the stack;
binding them to a slow phone download for 30 seconds is a terrible
use of that capacity. Signed URLs let S3 do what S3 is good at.

### `user_id` in the object key

Rejected. Even though the bucket isn't world-listable, keys leak in
logs, error messages, and third-party CDNs. A stable user prefix is
still a linkable identifier across uploads.

### DB-first write ordering

Rejected. Orphaned blobs that *exist* are discoverable; blobs that
have a DB row but never got to S3 surface as 404s on download with no
easy recovery path.

### `python-magic` / `libmagic`

Rejected. Four lines of magic-byte table covers every format we
accept. Adding a native dep for that is a net loss.

### Long-lived signed URLs (hours)

Rejected. Longer TTL doesn't improve UX meaningfully for a mobile
download (the re-fetch on expiry is a trivial client retry) but
dramatically widens the leak window.

### Per-upload cryptographic key

Rejected as overkill for Phase 4. A single static HMAC secret gives
us opaque keys without the operational cost of per-object key
management. Revisit if we ever need true end-to-end encryption.

## References

- RFC 9110 §15.5.13 (`413 Content Too Large`),
  §15.5.15 (`415 Unsupported Media Type`).
- AWS S3 presigned URL docs:
  https://docs.aws.amazon.com/AmazonS3/latest/userguide/ShareObjectPreSignedURL.html
- ISO/IEC 14496-12 (ISO Base Media File Format) — `ftyp` box layout.
- `backend/app/services/receipt_service.py`,
  `backend/app/core/storage.py`,
  `backend/app/core/storage_keys.py`,
  `backend/app/api/v1/endpoints/receipts.py`.

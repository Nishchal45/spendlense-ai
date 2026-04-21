# ADR-0001: JWT access tokens over server-side sessions

- **Status**: Accepted
- **Date**: 2026-04-21
- **Deciders**: backend

## Context

SpendLens needs a way to identify the user on every authenticated
request. The product will eventually have:

1. A web SPA (React, hitting the REST API from a browser).
2. A mobile app (iOS + Android, same REST API).
3. A small number of background jobs that will need to call the API on
   behalf of a user (e.g. re-categorise corrections).

Constraints that shaped the choice:

- Self-hosted deployment — no external identity provider, no Cognito.
- Single-node Postgres in Phase 0; horizontal scale later.
- No requirement for forced logout / session revocation at the product
  level yet.

## Decision

Issue short-lived JWT access tokens (HS256, 24h TTL, 32-char-minimum
shared secret) on login. Clients send them in the `Authorization:
Bearer …` header. The server validates the signature and `exp` on every
request — no session row in the database.

Shape of the implementation:

- `app/core/security.py` owns the primitives (`create_access_token`,
  `decode_access_token`, `hash_password`, `verify_password`).
- Every authenticated route takes a `CurrentUser` FastAPI dependency
  which pulls the token out, decodes it, and loads the user row.
- Tokens carry a `type: "access"` claim so a future refresh-token
  flow can issue a different token kind without risking cross-use.
- Password hashing is bcrypt (cost 12). Passwords are capped at 72
  bytes at the Pydantic layer to match bcrypt's truncation boundary.

## Consequences

### Positive

- Stateless auth: no session store, no cache invalidation, no extra
  service to run. Scales horizontally from day one.
- Mobile clients can persist the token in the OS keychain and hit the
  API directly — no cookies, no CSRF surface.
- Clear mental model for interviewers: "JWT in the Authorization
  header, decoded on every request, user loaded from DB".

### Negative

- No server-side revocation. If a token leaks, it's valid until `exp`.
  Mitigation today: short TTL. Follow-up: a denylist or a
  `token_version` column on `users` if/when we need force-logout.
- Token size is larger than a session cookie. Not a real concern at
  our traffic volume.
- Clock skew between API nodes can cause false expiry; negligible on
  single-node.

### Follow-ups

- ADR-0002 will cover the refresh-token strategy once we ship a
  "stay logged in" mobile UX. Access-only is fine for web MVP.
- Add rate limiting to `/auth/login` before public beta.
- Switch HS256 → RS256 if we ever split token issuance and validation
  across services.

## Alternatives considered

### Server-side sessions (cookie + Redis)

Rejected. Adds a mandatory dependency on Redis for request authentication
(beyond its caching role), complicates mobile clients (which don't want
to deal with cookies), and forces CSRF tokens on the web client.

### OAuth 2.0 with an external IdP (Auth0, Cognito, Keycloak)

Rejected for the portfolio milestone. Pulls in a hosted dep or a
Keycloak container, obscures the actual auth mechanics I want to be
able to walk an interviewer through, and adds compliance surface for a
single-user app.

### Argon2id instead of bcrypt

Rejected for now. Argon2id is the newer standard and arguably stronger,
but bcrypt is in FIPS-eligible OpenSSL, ubiquitous across language
ecosystems, and cost-12 bcrypt is the OWASP 2024 recommendation. Not
worth pulling in a `cffi`-heavy dep today.

## References

- OWASP ASVS v4.0.3 §6.2 (Password storage).
- RFC 7519 (JSON Web Token).
- RFC 6750 (Bearer token usage).
- `app/core/security.py`, `app/api/v1/endpoints/auth.py`.

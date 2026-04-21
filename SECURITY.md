# Security Policy

## Reporting a vulnerability

Please do **not** open a public GitHub issue for security problems.

Email the maintainer directly with:

- A clear description of the issue.
- Steps to reproduce, including any proof-of-concept.
- The impact you believe it has.

You should get an acknowledgment within 48 hours. Once the issue is
confirmed, a fix will be prepared on a private branch and released
before the details are disclosed publicly.

## Supported versions

SpendLens is pre-1.0. Only the latest commit on `main` receives
security fixes — there are no long-term-support branches yet.

## Hardening defaults

- Passwords are hashed with bcrypt (cost factor 12).
- JWTs are HS256 with a secret that must be at least 32 characters.
- All host-side Docker ports bind to `127.0.0.1`, not `0.0.0.0`.
- `.env` is gitignored; the repo only ships `.env.example`.
- Third-party API keys (OpenAI, S3) are optional; the app degrades
  gracefully without them.

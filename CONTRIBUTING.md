# Contributing to SpendLens

Thanks for taking a look. This doc is short on purpose — the goal is to
keep the tree healthy without bikeshedding.

## Development setup

```bash
# One-time
make install              # pip install -e ".[dev]" inside backend/
pre-commit install        # wire up the git hook

# Day-to-day
make up                   # bring up postgres, redis, minio, api
make migrate              # run alembic upgrade head
make test                 # run pytest inside the api container
```

## Branching

`main` is protected: linear history, passing CI, no force push. All work
happens on a branch and lands through a PR.

Branch names follow `<type>/<short-slug>`:

| Type     | Use for                                 |
| -------- | --------------------------------------- |
| `feat/`  | User-facing features                    |
| `fix/`   | Bug fixes                               |
| `chore/` | Build, tooling, deps                    |
| `docs/`  | Docs and ADRs                           |
| `ci/`    | GitHub Actions and release plumbing     |
| `refactor/` | No-behavior-change cleanup           |

Keep branches short-lived (hours, not weeks). Rebase on `main` before
merging.

## Commits

Conventional Commits, imperative mood, no period at the end of the
subject line:

```
feat(auth): add JWT login endpoint
fix(infra): shift host ports off defaults to avoid local clashes
chore(deps): bump fastapi to 0.115.5
docs(adr): record JWT vs session decision
```

One logical change per commit. If you're fixing a typo in a feature you
just shipped, it's a new commit — no `--amend` after push.

## Pull requests

- Title matches the commit style (conventional, imperative, short).
- Body has a `## Summary` and a `## Test plan` checklist.
- CI must be green. No merging a red PR.
- Squash-merge by default; the PR title becomes the commit on `main`.
- Address review comments by adding commits, not by force-pushing. Once
  approved, a squash-merge collapses the noise.

## Code style

- Python 3.12, full type hints, `mypy --strict` passes.
- Formatting and lint are `ruff`. Don't argue with it; fix it.
- Tests live next to the package they cover under `backend/tests/`.
- Business logic goes in `services/`; routers stay thin.
- Never commit secrets. `.env` is gitignored — use `.env.example` as the
  source of truth for required variables.

## Reporting issues

Bug reports should include the steps to reproduce, the observed
behavior, and the expected behavior. If it's a security issue, see
`SECURITY.md` — don't open a public issue.

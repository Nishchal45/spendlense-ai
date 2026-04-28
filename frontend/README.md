# SpendLens frontend

React + TypeScript + Vite client for the SpendLens API.

This is the dashboard scaffold landed in Phase 7 PR #A. Auth, expenses,
receipts, and the PWA share-target manifest land in subsequent PRs.

## Stack

| Layer         | Pick                               |
| ------------- | ---------------------------------- |
| Build / dev   | Vite 6 + React 18 + TypeScript 5   |
| Server state  | TanStack Query v5                  |
| Routing       | React Router DOM v6                |
| Styling       | Tailwind CSS v3                    |
| Tests         | Vitest + Testing Library + jsdom   |
| Lint / format | ESLint v9 (flat config) + Prettier |

## Local dev

The frontend is a service in the root `docker-compose.yml`. From the repo
root:

```bash
docker compose up frontend
# → http://localhost:3000
```

Edits on the host hot-reload in the browser. The dev server proxies
`/api/*` to the `api` container on the compose network, so the
browser sees a single origin and CORS is a non-issue in dev.

## Scripts

Run inside the container (`docker compose exec frontend …`) or, if you
have Node 22+ on the host, directly in `frontend/`:

| Command                | Does                             |
| ---------------------- | -------------------------------- |
| `npm run dev`          | Vite dev server with HMR         |
| `npm run build`        | Type-check (`tsc -b`) and bundle |
| `npm run lint`         | ESLint over `src/`               |
| `npm run format`       | Prettier write                   |
| `npm run format:check` | Prettier verify (CI uses this)   |
| `npm run test`         | Vitest one-shot                  |
| `npm run test:watch`   | Vitest in watch mode             |
| `npm run typecheck`    | `tsc -b --noEmit`                |

## Layout

```
frontend/
├── src/
│   ├── api/         API client + React Query hooks
│   ├── auth/        (PR #B) auth context + token storage
│   ├── components/  Reusable UI components
│   ├── pages/       Route components
│   ├── lib/         Generic utilities
│   ├── App.tsx      Top-level shell
│   ├── main.tsx     Vite entry point
│   └── index.css    Tailwind directives
├── public/          Static assets served as-is
├── index.html       Vite HTML entry
└── vite.config.ts
```

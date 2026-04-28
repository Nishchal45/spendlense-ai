# ADR-0006: Frontend architecture

- **Status**: Accepted
- **Date**: 2026-04-28
- **Deciders**: frontend

## Context

Phase 7 turns SpendLens from an API-and-task-runner skeleton into a
demo-able product. Ten questions had to land together, and most of
the decisions chain into each other:

1. **Build / runtime stack.** Vite vs. webpack vs. Next? TS strict
   vs. relaxed? React vs. SolidJS vs. anything else?
2. **Server state.** Redux + thunks? Zustand? TanStack Query? Plain
   `useEffect`?
3. **Auth state.** Where does the bearer token live, and how does
   the API client read it?
4. **Token persistence.** localStorage (XSS risk), httpOnly cookies
   (needs backend), in-memory (refresh logs out)?
5. **Routing.** React Router? TanStack Router? Just a `useState`?
6. **Styling.** Tailwind, vanilla CSS, CSS-in-JS, or a design
   system?
7. **Forms.** React Hook Form? Formik? Controlled inputs?
8. **Modals.** Headless UI? Radix? Native `<dialog>`?
9. **Pagination on the expenses list.** Auto-scroll-fetch or
   explicit "Load more"?
10. **PWA share_target.** Service worker plumbing, file stash
    location, who calls the API.

Constraints we're working inside:

- **Self-hosted-first.** The whole frontend has to work behind
  `docker compose up` with no third-party CDN runtime, no analytics,
  no marketing scripts.
- **Backend already exists.** Phases 0–6 shipped a production-grade
  REST API with cursor pagination, ETags, async pipeline, OCR, LLM.
  The frontend reads that surface; it doesn't reshape it.
- **One developer, one phase.** Keep the dependency footprint small
  enough that the project stays understandable a year from now.

## Decision

Vite + React 18 + TypeScript 5 strict, **TanStack Query** for server
state, **React Router DOM v6** for routing, **Tailwind v3** for
styling, **Vitest + Testing Library** for tests, **ESLint v9 flat +
Prettier** for code health. Auth lives in an in-memory **pub/sub
token store** that React context mirrors. The API client is a 50-
line `fetch` wrapper. Modals use the native `<dialog>` element. PWA
`share_target` runs through a service worker that stashes files in
**Cache Storage**, leaving auth in the page.

Every choice in detail:

### Build / runtime: Vite + React 18 + TypeScript strict

- **Vite 6** for dev + build. Fast HMR, native ESM, minimal config.
  Webpack is heavier than we need; Next.js would force routing /
  RSC opinions we don't want for a SPA-against-an-API product.
- **React 18.** Stable, broad ecosystem, the team-of-one default.
  SolidJS / Svelte are interesting but the Query / Router /
  Testing Library ecosystems we lean on are React-native.
- **TypeScript strict + four extras.** `noUnusedLocals`,
  `noUnusedParameters`, `exactOptionalPropertyTypes`,
  `verbatimModuleSyntax`. Mirrors the backend's `mypy --strict`
  ethos. The `exactOptionalPropertyTypes` flag in particular forces
  a precise distinction between "field omitted" and "field set to
  `undefined`" — it caught real bugs in PR #C's filter shape and
  PR #D's optimistic patch helper.

### Server state: TanStack Query, not Redux / Zustand

The app's state is overwhelmingly server state — expenses,
receipts, the auth user. TanStack Query is purpose-built for that:
caching, deduping, background refetch, optimistic updates with
rollback, infinite scroll over a cursor-paginated API.

A Redux-flavoured solution would re-implement the same primitives
worse. Zustand is great for small client-side stores (shopping cart
state) but has nothing to say about cache lifecycle, invalidation,
or background polling — exactly the things receipt status updates
need.

### Auth state: pub/sub store, not React context as source of truth

Two consumers need "what's the current bearer token?":

1. **React components** — re-render on change.
2. **The API client** (`apiFetch`) — runs *outside* React in
   TanStack Query's query functions; can't reach `useContext`.

A small `authStore` module with `getAuthToken` / `setAuthToken` /
`subscribeAuth` is the source of truth. The `AuthContext`
provider mirrors store changes into React via `useEffect(subscribe,
[])`. The API client reads the store directly.

This sidesteps a class of bug where context updates don't propagate
to functions that already captured a stale token.

### Token persistence: in-memory only

Three options were on the table:

- **localStorage** — survives reload, vulnerable to XSS (any script
  on the page can `localStorage.getItem('token')`).
- **httpOnly cookie** — XSS-safe and persistent, but requires
  backend cookie support that Phase 2's JWT auth doesn't have.
- **In-memory only** — XSS-safe by construction (no script can read
  a closure), but the user is logged out on every refresh.

We picked **in-memory only** for Phase 7. The UX cost (re-login on
refresh) is mild for a self-hosted finance app. Phase 8 graduates
to httpOnly cookies + silent refresh; the consumer-side surface
(`useAuth`) stays the same.

### API client: thin `fetch` wrapper, no axios

`apiFetch` is ~50 lines. Covers JSON in / JSON out, bearer auth,
204 short-circuit, FormData / Blob bypass on `Content-Type`, and a
proper `ApiError` subclass for status-aware branching. Auth is
attached *per request* by reading from the store — logout
invalidates the next call immediately.

axios would add a runtime dependency for what `fetch` already does.
A custom 50-line wrapper is also cheaper to read than axios's
interceptor model.

### Routing: React Router DOM v6

Standard, broad docs, easy story for the protected-route wrapper.
TanStack Router has a more elegant typed-link story but pulls in
significantly more code. React Router's
`createBrowserRouter` + nested route objects is the right shape for
a "shell + login + register + protected branch" tree.

### Styling: Tailwind v3

Tailwind v3 (not v4 — still settling at the time of writing). One
brand colour scale (`brand.50` … `brand.700`), everything else
straight off Tailwind's defaults. No CSS-in-JS, no PostCSS plugin
zoo. The single `index.css` file holds the three Tailwind
directives and nothing else.

A design-system dep (Radix, MUI, shadcn) is overkill for a 6-page
product. Tailwind utilities + native `<dialog>` cover everything we
need.

### Forms: controlled inputs, no React Hook Form

Two- and four-field forms. React Hook Form's value (re-render
performance, schema validation integration) is real on a 30-field
checkout, invisible at this scale. Controlled inputs + `useState`
read the same as the rest of the codebase.

The submit-disabled-until-valid pattern (e.g. password ≥ 8 chars on
the login form) is enforced inline. Backend Pydantic validation is
the actual gate; client-side checks just save a 422 round-trip.

### Modals: native `<dialog>`

`<dialog>.showModal()` gives us focus trap, backdrop, escape-to-
close, and scroll lock for free. Custom modal layers (Radix Dialog,
headless-ui Dialog) all reinvent these and tend to ship a11y debt.

The expense form dialog and the receipt-delete confirm both use
`<dialog>`. Browser support is ≥ Chrome 37, Safari 15.4, Firefox
98 — fine for our target.

### Pagination: explicit "Load more", not auto-scroll-fetch

`useInfiniteQuery` mirrors the backend's keyset cursor. The button
is intentional: a finance UI is something users actively scan.
Auto-fetching as they scroll fires when they're trying to read
what's already on screen; an explicit affordance respects the user's
actual intent.

If a real user reports the click is annoying, we'll add an
intersection-observer trigger to the same hook.

### Receipt status polling: two-tier

The receipts page subscribes to two query layers:

- **List query** refreshes every 5 s. Picks up new uploads,
  surfaces terminal-state transitions.
- **Per-row status query** polls every 2 s *while the row is in
  flight*, stops at terminal. A categorised receipt costs zero
  requests at rest.

Both layers share an `isInFlight(status)` helper so polling cadence
and badge visuals never disagree on what "still moving" means.

### PWA `share_target`: service worker stash, page uploads

The manifest declares `share_target` so the OS share sheet lists
SpendLens as a destination. The browser POSTs the shared file to
`/share-target` — a path a static SPA can't serve.

The service worker (`public/sw.js`) intercepts the POST, stashes the
file in **Cache Storage** at `/__shared-receipt`, and 303-redirects
to `/share-target` as a GET. The React route at that path reads
the cache, reconstructs a `File`, and runs it through the normal
authed `useUploadReceipt`.

**Auth stays in the page, not the SW.** The SW would need access to
the in-memory bearer token; passing it via `postMessage` races the
SW lifecycle. Stashing the file and letting the page handle the
authed upload sidesteps the timing problem entirely.

Cache Storage (not IndexedDB) because `cache.put(url, response)` /
`caches.match(url)` is a one-line round-trip with no schema.

## Consequences

### Positive

- **The product looks like a product.** Login → expense list with
  inline category edit → receipt upload with status polling →
  share-from-camera-roll → categorised expense in the dashboard.
  Recruiters opening the repo see a working app, not just an API.
- **Strict TypeScript end-to-end.** ~80 source + test files under
  `tsc --strict` with the four extras. Type drift between backend
  and frontend wire shapes is caught at the IDE.
- **Cache Storage / SW handle the share-target round trip
  correctly.** The OS share sheet talks to the manifest, the SW
  catches the POST, the page does the authed upload — exactly the
  layered design that mobile-PWA share-target was specced for.
- **Optimistic mutations feel instant.** Inline category edit on
  the expenses dashboard updates the cache immediately, rolls back
  on error. The "click and wait" feel that motivated this PR is
  gone.
- **One CI lane per side of the repo.** Backend and frontend jobs
  run in parallel; frontend lint + format + typecheck + test +
  build lands in 25–30 s.

### Negative

- **Refresh logs you out.** In-memory tokens are the right call for
  Phase 7's threat model but a real annoyance during dev. Phase 8
  fixes this with httpOnly cookies + silent refresh.
- **No offline shell.** The service worker is share-target-only.
  Loading the app requires a network. Phase 8 brings a precache /
  offline-first story when we deploy behind a CDN.
- **Single SVG icon at every size.** Modern Chrome and iOS 18+
  accept SVG; older Safari falls back to a screenshot of the page.
  Phase 8 rasterises PNG variants for full coverage.
- **Auto-scroll-fetch deferred.** "Load more" is two clicks for a
  user with 200 expenses. We'll instrument and revisit.
- **Tailwind v3, not v4.** v4 is still settling at the time of this
  ADR. Migration is a Phase 8+ project; nothing in v3 is blocking.

### Follow-ups

- **httpOnly cookies + silent refresh** (Phase 8). New flow:
  `POST /auth/login` sets an httpOnly refresh cookie, returns an
  access token; access expires every 15 min and the SPA hits
  `POST /auth/refresh` from a silent fetch. The current
  `useAuth` surface stays unchanged.
- **Offline shell.** Precache the JS bundle + index.html, fall back
  to a "you're offline, queued upload" state for new uploads.
- **Rasterised PNG icons.** 192×192 + 512×512 + maskable variants.
  Build-time generation from the SVG so we don't have to maintain
  three files.
- **URL state for filters.** Today the expense filters live in
  React state; reload loses them. `useSearchParams` would round-
  trip them through the URL.
- **Receipt thumbnails on the cards.** Right now the parsed
  payload renders without a preview of the source image. The
  signed-URL endpoint is one line away — deferred to keep PR #D
  focused.
- **i18n.** All copy is English-only. `react-intl` lands when a
  real non-English user shows up.
- **End-to-end test pass.** Vitest + Testing Library cover unit /
  integration; a Playwright pass over the live stack would catch
  regressions across the front+back boundary.

## Alternatives considered

### Next.js / Remix instead of SPA + Vite

Rejected. SSR doesn't buy us anything for an authenticated finance
dashboard — there's no public content to crawl, no SEO target, no
shared cache to warm. The framework would force routing / loader /
RSC opinions we don't want, and would couple the frontend deploy
to Node-server hosting instead of a static-asset CDN.

### Redux Toolkit + RTK Query

Rejected. RTK Query covers the server-state slice cleanly, but the
Redux store / slices / selectors / dispatch ceremony adds layers
we don't need for client state we don't have. TanStack Query
focuses on the same problem with less surface area.

### Zustand for auth state

Rejected. Zustand would handle the React side of auth fine, but
the API client still needs a non-React way to read the token. A
single-purpose pub/sub module (`authStore`) is smaller than
adopting a state library for one piece of state.

### React Hook Form

Rejected at this scale. The performance argument matters at 30+
fields; our biggest form has six. Pulling in RHF + Zod for that
is more code than the form itself.

### Headless UI / Radix Dialog

Rejected. The native `<dialog>` element handles focus trap, backdrop,
escape, and scroll lock — all the things headless dialog libraries
reinvent. Custom modal layers are a known source of a11y regressions.

### `vite-plugin-pwa`

Rejected for now. The plugin is a fine choice when we want a full
PWA story (precache, runtime caching strategies, update prompts).
For Phase 7 we needed *only* `share_target` handling — 60 lines of
hand-rolled service worker is smaller than the plugin's surface
area. We may adopt it in Phase 8 alongside the offline shell.

### localStorage for the bearer token

Rejected. Any script that runs on the page (a third-party
analytics tag, a compromised npm package shipping a sneaky
postinstall) can `localStorage.getItem` the token. The convenience
of "stay logged in across refresh" doesn't justify the XSS
exposure.

### Auto-scroll-fetch on the expenses list

Rejected by design. Finance UI is read actively, not passively;
firing a fetch when the user is trying to *read* the bottom of a
page is the wrong default. Explicit "Load more" stays.

### Service worker authenticates the upload

Rejected. The SW can intercept POST and read FormData, but it
doesn't have ergonomic access to the in-memory bearer token. Page-
side upload is simpler and has the auth context already.

## References

- TanStack Query infinite queries:
  <https://tanstack.com/query/latest/docs/framework/react/guides/infinite-queries>
- W3C Web App Manifest `share_target`:
  <https://w3c.github.io/web-share-target/>
- WICG explainer for share_target with files:
  <https://github.com/WICG/web-share-target/blob/main/level-2.md>
- `<dialog>` element (MDN):
  <https://developer.mozilla.org/en-US/docs/Web/HTML/Element/dialog>
- `frontend/src/api/client.ts`,
  `frontend/src/auth/authStore.ts`,
  `frontend/src/api/receipts.ts`,
  `frontend/public/sw.js`,
  `frontend/src/pages/ShareTargetPage.tsx`.

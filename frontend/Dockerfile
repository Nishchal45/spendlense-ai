# Dev-mode Dockerfile. The frontend container runs ``vite dev`` with
# HMR enabled so edits on the host show up in the browser instantly.
# The container does NOT bake the source code — docker-compose mounts
# ``./frontend`` over ``/app``. This Dockerfile only owns the
# Node version + ``node_modules`` cache layer.
#
# A production multi-stage Dockerfile (``vite build`` → nginx) lands
# with the deploy story in Phase 8.

FROM node:22-bookworm-slim

ENV NODE_ENV=development \
    CI=false

WORKDIR /app

# Copy only the manifests first so ``npm install`` is layer-cached on
# anything that doesn't touch dependencies.
COPY package.json package-lock.json* ./

# ``npm ci`` requires a lockfile; fall back to ``install`` for the
# very first build before the lockfile is committed. Once the
# lockfile lands ``ci`` is the only path.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# Source is bind-mounted at runtime; this COPY is a backstop for
# ``docker run`` without a volume mount.
COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

# The SPA: built once, then served as static files.
#
# Node is in the build stage only. The runtime has no Node, no npm and no source —
# nothing to execute, because a single-page application is just files on disk by the
# time a browser sees it.

# ── build ───────────────────────────────────────────────────────────────────
FROM node:22-alpine AS builder

WORKDIR /build

# npm ci, not npm install: it installs exactly the lockfile and fails if
# package.json has drifted from it, which is the whole point of committing a lock.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# tsc -b runs first via the build script, so a type error fails the image rather
# than shipping.
RUN npm run build

# ── runtime ─────────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/index.html || exit 1

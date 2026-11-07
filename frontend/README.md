# Frontend — Employee Salary Management

React 19 · TypeScript · Vite · Vitest

## Running it

In the stack, as nginx serves it in production:

```bash
cd devops && docker compose up -d --wait   # http://localhost:5173
```

For hot reload, stop that container and run Vite:

```bash
cd devops && docker compose stop web
cd ../frontend && npm install && npm run dev
```

Both serve on :5173 and both proxy `/api` and `/auth` to the BFF — Vite via
`server.proxy`, nginx via `nginx.conf`. Keeping the two in step matters: a path the
dev server proxies and the production config does not works locally and 404s once
built.

```bash
npm run lint        # oxlint
npm run typecheck   # tsc on the app project
npm run test:run    # vitest
npm run build       # tsc -b across every project, then vite build
```

`typecheck` and `build` are not the same check. `typecheck` runs against
`tsconfig.app.json`, which excludes `vite.config.ts`; `build` runs `tsc -b` across
every project and compiles it. CI runs both.

## It knows nothing about OIDC

No client id, no PKCE, no authorization URL, no token. The SPA calls `/auth/me`; if
that returns 401 it carries a `login_url`, and the app navigates the whole window
there. Swapping identity provider changes nothing in this tree.

The session lives in an httpOnly cookie the BFF sets, which JavaScript cannot read —
so there is no token in `localStorage` for a cross-site scripting bug to steal. What
the browser does read is the CSRF cookie, echoed back as `X-CSRF-Token` on unsafe
methods.

| State | What renders |
|---|---|
| `checking` | nothing yet — `/auth/me` is in flight |
| `authenticated` | the app |
| `anonymous` | a sign-in prompt pointing at `login_url` |
| `unreachable` | a retry |

`unreachable` is a separate state on purpose. Telling someone to sign in because the
server is down sends them round a loop they cannot exit.

`RequireAuth` is a convenience, not a control. It decides what renders; the BFF
decides what is authenticated; the salary service decides what is permitted.

## Layout

```
src/
  api/client.ts      fetch wrapper: credentials, CSRF, ApiError
  auth/              AuthContext, AuthProvider, useAuth, RequireAuth
  pages/             route components
  test/setup.ts      jsdom + jest-dom
```

# BFF — Backend for Frontend

The only thing the browser talks to. Two jobs: authenticate, and forward.

```
browser ──cookie──▶ BFF ──Bearer──▶ salary API ──▶ Postgres
                     │
                     └──────────▶ Keycloak (code exchange, refresh)
```

## Why this layer exists

**The browser never holds a token.** Without a BFF, an SPA keeps an access token in
memory or storage, and any successful XSS walks away with a working credential. Here the
browser holds an encrypted `httpOnly` cookie it cannot read and JavaScript cannot reach.

Three things follow from being a server rather than a script in a page:

| | SPA alone | With the BFF |
|---|---|---|
| Keycloak client | public — cannot keep a secret | **confidential**, secret never leaves the server |
| Token in the browser | yes | **no** |
| Token refresh | SPA logic | invisible to the SPA |
| OIDC knowledge in the SPA | client id, PKCE, authorization URL | **none** — it follows a `login_url` |

**It does not replace the API's own checks.** The salary API verifies signature, issuer,
audience and expiry on every request it receives. If the BFF were the only check,
anything reaching the API by another route would bypass authentication entirely. This is
an additional layer, which is the only kind worth adding.

## The cost, stated plainly

**Cookies reintroduce CSRF.** Browsers attach cookies to cross-site requests
automatically and do not attach `Authorization` headers — so the thing that made bearer
tokens immune is exactly what this layer gives up. Paid for, not hoped away:

- `SameSite=Lax` on the session cookie
- a double-submit token on every `POST`, `PUT`, `PATCH`, `DELETE`: the BFF issues a
  readable `salary_csrf` cookie, and the SPA echoes it in `X-CSRF-Token`. A cross-site
  page can cause the cookie to be sent but cannot read it to build the header.

A BFF without CSRF protection is a net security loss, not a gain.

**An extra hop and an extra deployable.** Accepted: it buys a browser with no credential
in it.

## Session storage

The session lives in the cookie itself, Fernet-encrypted — no Redis, no table, no server
state. It survives a restart and scales horizontally for free.

The risk was the 4 KB cookie limit, since the session carries both tokens. **Measured at
2788 bytes** with a real Keycloak token, leaving around 1.3 KB of headroom. If a future
claim pushes it past the limit, the fix is a `bff_session` table in its own database —
the same shape Keycloak already uses here.

Fernet authenticates as well as encrypts, so an edited cookie fails to decode rather than
decoding into something an attacker chose. Rotating `SESSION_SECRET` logs everyone out,
which is the correct behaviour: old cookies become undecodable rather than merely
distrusted.

## Running it

In the container, with the rest of the stack:

```bash
cd devops && docker compose up -d --wait
```

Or on the host, against a Keycloak that compose is running:

```bash
cd bff
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8001
```

Either way the salary API must be running — see `backend/README.md`.

### Two addresses for one provider

Keycloak signs a fixed `iss` into every token, so the realm pins its advertised
hostname to `http://localhost:8080`. That is the address the browser resolves, and
inside a container network it resolves to the container itself. So the BFF takes a
second setting:

| Setting | Meaning |
|---|---|
| `OIDC_ISSUER` | What the browser is sent to, and the only `iss` a token may claim |
| `OIDC_INTERNAL_ISSUER` | Where this process reaches Keycloak; empty off-Docker |

When the second is set, the token, logout and JWKS URLs are rewritten to that origin.
The authorization URL is not — the browser is the one that follows it. Verification
still demands the public issuer: the rewrite is routing, not trust.

```bash
pytest
ruff check . && ruff format --check .
```

## Endpoints

| | |
|---|---|
| `GET /auth/login?return_to=` | 307 to Keycloak. Stores the PKCE verifier and state in a short-lived encrypted cookie |
| `GET /auth/callback` | Verifies state, exchanges the code using the client secret, sets the session cookie |
| `GET /auth/me` | Who is logged in, or 401 with `login_url` |
| `POST /auth/logout` | Ends the Keycloak session and clears both cookies |
| `/api/{path}` | Proxied to the salary API with a Bearer token |
| `/chat/{path}` | Proxied to the chat service |

**`/auth/me` returns 401 with a `login_url` rather than redirecting.** A `fetch()` that
follows a redirect to Keycloak fails CORS and surfaces as an unrelated-looking error; the
SPA needs to navigate the whole window, and only it knows when that is acceptable.

## Authentication here, authorization there

**This service is the authentication boundary.** Every token it forwards has been
verified: signature against the provider's published JWKS, plus `iss`, `aud`, `exp`,
`iat` and `sub`. Answered once, here, for every service behind.

**It does not authorize.** Whether `hr_manager` may call `POST /fx-rates` is the salary
service's decision, because only it knows what its endpoints mean. Centralising that
would mean this layer holding a copy of every service's rules, and the copy drifting.

| | Decides | Knows |
|---|---|---|
| BFF | is this really who they say they are | the issuer, the audience, the keys |
| Salary service | may they do this | its own endpoints and roles |

Verified at three points: when a token is first issued at the callback, after every
refresh, and **on every proxied request**. The last one matters most — it is what makes
the session cookie a convenience rather than a trust anchor. If the Fernet key ever
leaked, a forged cookie would still carry a token that fails signature verification, so
one leaked key does not become every service behind.

Cost is a cached RSA verification: tens of microseconds, no network call. Keys are
refetched when they expire, and immediately when a token arrives signed by a key id the
cache does not recognise — which is what rotation looks like from here, and the
alternative is every user failing until the TTL lapses.

**One exception, and it is not a credential check**: an unreachable JWKS endpoint
returns 502, not 401. An outage must not read as everyone's password being wrong.

## Security posture

Mapped to the OWASP Top 10, with what each control actually stops.

| | Control | What it prevents |
|---|---|---|
| **A01** Access control | `require_session` is a dependency, not a check inside each handler | "every proxied route is authenticated" is true by construction, not by review |
| **A01** | `safe_return_path` on `return_to` | **Open redirect.** A link to the real domain that lands on an attacker's site after a genuine login |
| **A02** Crypto | Fernet-encrypted, `httpOnly` session cookie; `Secure` when served over TLS | A token in the browser; a cookie edited into something chosen |
| **A03** Injection | Request headers forwarded by **allowlist** | An upstream trusting `X-Forwarded-For` or `X-User-Id` ends up trusting the client |
| **A02** | **Full JWT verification**: RS256 pinned, signature, `iss`, `aud`, `exp` required | A forged or edited token; `alg: none`; a token minted for another client; one that never expires because it has no `exp` |
| **A04** Insecure design | Authentication centralised here, authorization left to each service | One layer holding a stale copy of every service's rules |
| **A05** Misconfiguration | `nosniff`, `X-Frame-Options`, CSP `frame-ancestors`, `Referrer-Policy`, `no-store` on authenticated responses, docs off when deployed | Clickjacking, MIME confusion, cached salary data, a published map of the services behind |
| **A05** | Generic error bodies; upstream host and exception type stay in the log | Internal detail in a response |
| **A07** Auth failures | Absolute session lifetime independent of refresh; rate limit on login; fresh CSRF secret per login | A refreshing session that never ends; credential stuffing; a pre-auth CSRF value surviving login |
| **A08** Integrity | Fernet authenticates as well as encrypts; `compare_digest` for state and CSRF | A forged cookie; a timing oracle on comparison |
| **A09** Logging | Structured JSON, redaction **in the formatter**, request id on every line and every response | A token in a log file. Redaction at call sites depends on everyone remembering |
| **A10** SSRF | `safe_upstream_path` rejects traversal, absolute paths and encoded variants | A client choosing which URL the server requests |

Two more, not in the list but load-bearing:

- **Body size limit**, checked against the accumulated stream rather than only
  `Content-Length`, which can lie or be absent. An unbounded proxy body is a
  memory-exhaustion primitive.
- **`follow_redirects=False`** on the upstream client. Following a 302 can send the
  `Authorization` header to wherever `Location` points.

## Structure

One responsibility per module, so a change has one obvious home:

```
app/
  main.py              wiring only - owns nothing
  config.py            settings
  errors.py            typed failures, and the one place they become responses
  logging_setup.py     structured logging + redaction
  deps.py              shared dependencies
  auth/routes.py       the only code that knows OIDC exists
  proxy/routes.py      upstream registry + per-request policy
  proxy/forwarder.py   forwarding mechanics, no knowledge of which upstream
  clients/keycloak.py  TokenIssuer protocol + the Keycloak implementation
  security/
    session.py         the encrypted cookie
    csrf.py            double-submit policy
    redirects.py       where the browser may be sent
    paths.py           what the proxy may forward
    headers.py         response headers
    rate_limit.py      login throttle
```

The service depends on the `TokenIssuer` **protocol**, not on Keycloak. A test
substitutes a fake without patching imports, and swapping identity provider touches one
file.

Upstreams live in a registry rather than a function per service, so adding one is a line
of configuration — and the auth, CSRF and refresh rules cannot drift between them,
because there is one copy.

## What the proxy deliberately does

- **Strips hop-by-hop headers** (`connection`, `transfer-encoding`, `host`, …) — they
  belong to one connection and forwarding them corrupts the next.
- **Does not forward the session cookie.** A service that can read it can mint requests
  as that user.
- **Passes upstream status codes through unchanged.** A 403 from the API stays a 403;
  flattening it to 500 would hide the one thing the user needs to know.
- **Refreshes an expired access token before forwarding**, and re-issues the cookie. A
  failed refresh ends the session rather than looping.
- **Propagates `X-Request-ID`**, so one id ties the BFF log, the API log and the audit
  row together.

# ADR-0006: The salary service never sees a token

**Status:** Accepted · **Date:** 2026-11-05

## Context

A browser, a BFF, and a salary service. The question is where the access token is
verified, and how far it travels.

The default pattern — the SPA holds the token and attaches `Authorization: Bearer` to every
call, which the API verifies — has two properties this system should not have:

- **The token is in the browser.** In `localStorage` it is readable by any XSS; in memory
  it is lost on refresh and the app re-authenticates constantly.
- **Token verification is duplicated.** Both the BFF and the API need JWKS fetching, issuer,
  audience and expiry checks, clock-skew tolerance and key rotation. Two implementations of
  the same security-critical logic drift, and the drift is invisible until it isn't.

## Decision

**Authentication happens exactly once, at the BFF. The salary service checks roles only.**

- The browser holds **no token**. It holds an encrypted, httpOnly, SameSite session cookie.
  The BFF keeps the access and refresh tokens server-side and refreshes them itself, which
  is why the SPA has no refresh interceptor — there is nothing for it to intercept.
- The BFF verifies the JWT on every request: signature against JWKS, `iss`, `aud`, `exp`.
- The BFF then calls the salary service with the **decoded claims as headers** —
  `X-User-Sub`, `X-User-Email`, `X-User-Name`, `X-User-Roles`, plus `X-Request-ID`.
  **`Authorization` is stripped and does not cross the hop.**
- The salary service reads those headers into a `Principal` and answers one question:
  does this person's role permit this operation (`access.require_role`)?

Trusting a header is only safe if nothing else can set it, so the trust boundary is made of
two things, and both are required:

1. the `api` container **publishes no host port** — it is reachable only over the compose
   network, from the BFF;
2. every request carries **`X-Gateway-Key`**, checked in middleware before routing
   (`app/gateway.py`), with `secrets.compare_digest` and **fail-closed** if the key is
   unset — an unset key must never read as "no check configured".

`gateway.py` and `access.py` are separate files on purpose. One answers "is the caller the
BFF" (a question about the network hop, which changes when the topology changes); the other
answers "is this person allowed" (a question about the business, which changes when the
business does). One file doing both would change for either reason.

## Consequences

**Good.** No token in the browser, so XSS cannot steal one. One implementation of token
verification, in one place, with one set of tests. The salary service has no JWKS client,
no clock-skew configuration and no key-rotation concern; its security surface is a
constant-time string comparison and a role check. Session revocation is a server-side
cookie invalidation rather than a token blacklist.

**Bad.** The BFF is a single point of failure and a single point of compromise. A
compromised BFF can impersonate anyone, because the salary service believes its headers by
construction. This is the cost of the design, stated plainly: the blast radius of the BFF
is total.

**Bad.** The salary service cannot be exposed directly — not to a mobile client, not to a
partner integration, not to a cron job — without building a second authentication path.
Accepted, because there is one client.

**Bad.** The gateway key is shared secret material in an environment variable. Rotating it
means a coordinated restart of both services. For a deployment of this size that is
acceptable; past it, mTLS between the two is the upgrade.

**Operationally sharp edge.** Middleware registration order is load-bearing — Starlette runs
middleware in reverse registration order, and getting it wrong silently drops `request_id`
from gateway rejections, which are exactly the log lines someone will be reading at 2am.
That is why registration lives in one function (`app/middleware.py`) rather than being
spread across `main.py`.

## Rejected

- **Verify the token at both layers.** Defence in depth in name; in practice two
  implementations to keep in sync, for a service that is not reachable from outside the
  compose network anyway.
- **Forward the `Authorization` header unchanged.** Convenient, and it makes the API's trust
  model ambiguous — it would have a token it is not verifying, which invites someone to
  start trusting it.
- **mTLS between BFF and API instead of a shared key.** Stronger, and correct at a larger
  size. Rejected for a take-home deployment: certificate issuance and rotation is more
  moving parts than the threat model here justifies.
- **A signed/JWT claims header instead of plain headers.** Would survive the API being
  exposed directly, which is the case this design has already ruled out. Reconsider the day
  requirement (1) above stops holding.

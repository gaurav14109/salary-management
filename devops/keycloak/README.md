# Keycloak realm

`realm-salary.json` is imported on first start by `--import-realm`, so a fresh clone
gets the same realm, clients, roles and test users instead of a page of click-through
instructions.

## What it defines

| | |
|---|---|
| Realm | `salary` |
| Realm roles | `admin`, `hr_manager` — the BFF reads `realm_access.roles` and forwards them as `X-User-Roles`; the salary service's `require_role` reads that |
| `salary-bff` | confidential client, authorization code + PKCE (S256). The secret lives in the BFF, never in the browser |
| `salary-api` | bearer-only. Exists so tokens carry `aud=salary-api` |

## Two settings that look odd and are deliberate

**`accessTokenLifespan: 300`.** The BFF validates tokens against Keycloak's cached public
keys rather than introspecting on every request, so a token stays usable until it expires
even after an account is disabled. Five minutes keeps that window smaller than most
incident response. Introspection would close it entirely, at the cost of a network round
trip on every request and Keycloak becoming a hard dependency of every page load.

**`directAccessGrantsEnabled: true`.** Local development only — it lets a test or a `curl`
obtain a token without driving a browser. A deployed realm turns this off; the SPA uses
the authorization code flow and never needs it.

**The audience mapper is the point of `salary-api`.** Without it a token minted for any
other client in this realm would validate here. The BFF checks `aud`, so it refuses one
that was not issued for this application. The salary service never sees the token at all -
it receives the decoded claims as headers and decides only whether the role is allowed.

## Test users

| Username | Password | Roles |
|---|---|---|
| `hr` | `hr` | `hr_manager` |
| `admin-user` | `admin` | `admin`, `hr_manager` |

Local credentials for a local realm. They are not secrets, and nothing here is reused
anywhere real.

## Adding a user

`--import-realm` runs only when the realm does not already exist, so editing the JSON
does nothing to a realm that is already up. To add someone to a running realm:

```bash
cd devops
python3 create_user.py anita --password s3cret --roles hr_manager \
    --email anita@acme.test --first Anita --last Rao

python3 create_user.py --list          # who exists, and with which roles
```

Idempotent on username: a second run updates the profile and resets the password rather
than failing. Roles are checked against the realm *before* the user is written, so a
misspelled `--roles` creates nothing - an account that authenticates and is refused by
every endpoint looks like an application bug, not a typo.

The script is standard library only and talks to the admin API, so it needs no
virtualenv. For a user who should outlive `down -v`, add them to `realm-salary.json`
as well.

## Getting a token by hand

```bash
curl -s -X POST http://localhost:8080/realms/salary/protocol/openid-connect/token \
  -d grant_type=password -d client_id=salary-bff \
  -d client_secret=local-dev-bff-secret-not-for-production \
  -d username=hr -d password=hr | jq -r .access_token
```

`salary-bff` is a confidential client, so the secret is required even for this grant. It
is the local-development secret committed in the realm JSON, not one that means anything.

## Redirect URIs

Two are registered for `salary-bff`, and they are not interchangeable:

| URI | When it is used |
|---|---|
| `http://localhost:5173/auth/callback` | the normal path — nginx fronts the BFF, so the browser stays on one origin |
| `http://localhost:8001/auth/callback` | the BFF addressed directly, for host-run development |

Keycloak does not supply this value. The BFF sends it as `redirect_uri`, built from
`BFF_BASE_URL`, and Keycloak refuses anything not on this list — which is what stops
an attacker pointing the authorization code at a host they control.

So the two move together. Change `BFF_BASE_URL` without registering the new URI and
login fails with `Invalid parameter: redirect_uri`; register one without changing
`BFF_BASE_URL` and nothing happens at all.

## Changing the realm

Edit the JSON. `--import-realm` only imports when the realm does not already exist, so
re-importing needs the data wiped:

```bash
cd devops && docker compose down -v && docker compose up -d --wait
```

That also wipes the salary database, since both live in the same Postgres volume locally.

To change a running realm without wiping it, patch it through the admin API and make
the same edit in the JSON, so a fresh clone gets the same realm:

```bash
TOKEN=$(curl -s -d client_id=admin-cli -d username=admin -d password=admin \
  -d grant_type=password \
  http://localhost:8080/realms/master/protocol/openid-connect/token \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8080/admin/realms/salary/clients?clientId=salary-bff" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)[0]['id'])")

curl -X PUT -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"redirectUris":["http://localhost:5173/auth/callback","http://localhost:8001/auth/callback"]}' \
  "http://localhost:8080/admin/realms/salary/clients/$ID"
```

Patching only the running instance leaves the two out of step, and the next person to
run `down -v` loses the change without being told.

#!/usr/bin/env python3
"""Create or update a Keycloak user, from the command line.

`realm-salary.json` seeds two test users on a fresh import, which covers a clone and
nothing else: the import runs only when the realm does not exist, so adding a third
person means either wiping the realm or clicking through the admin console. This does
it against a running realm instead.

Idempotent on username. Running it twice is a no-op plus a password reset, so it is
safe in a setup script and safe to re-run after a typo.

Standard library only, deliberately - this has to work before any virtualenv exists.

    python3 create_user.py anita --password s3cret --roles hr_manager \
        --email anita@acme.test --first Anita --last Rao

    python3 create_user.py --list

Environment (all optional, defaults match docker-compose.yml):
    KEYCLOAK_URL             http://localhost:8080
    KEYCLOAK_REALM           salary
    KEYCLOAK_ADMIN           admin
    KEYCLOAK_ADMIN_PASSWORD  admin
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("KEYCLOAK_URL", "http://localhost:8080").rstrip("/")
REALM = os.environ.get("KEYCLOAK_REALM", "salary")
ADMIN_USER = os.environ.get("KEYCLOAK_ADMIN", "admin")
ADMIN_PASSWORD = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "admin")

TIMEOUT = 15


class KeycloakError(RuntimeError):
    """Anything Keycloak refused. Carries its message, not a stack trace."""


def _request(method: str, path: str, token: str | None = None, body: dict | None = None):
    """One HTTP call. Returns parsed JSON, or None for an empty body."""
    url = path if path.startswith("http") else f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace").strip()
        raise KeycloakError(f"{method} {url} -> {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        # The common failure by far: the stack is not up. Say that, rather than
        # letting a socket error surface as if the script were broken.
        raise KeycloakError(
            f"cannot reach Keycloak at {BASE} ({exc.reason}). "
            "Is it running?  cd devops && docker compose up -d --wait"
        ) from exc


def admin_token() -> str:
    """A token for the master realm's admin-cli client, via password grant."""
    form = urllib.parse.urlencode(
        {
            "client_id": "admin-cli",
            "grant_type": "password",
            "username": ADMIN_USER,
            "password": ADMIN_PASSWORD,
        }
    ).encode()
    req = urllib.request.Request(
        f"{BASE}/realms/master/protocol/openid-connect/token", data=form, method="POST"
    )
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return json.load(response)["access_token"]
    except urllib.error.HTTPError as exc:
        raise KeycloakError(
            f"admin login failed for '{ADMIN_USER}' ({exc.code}). "
            "Check KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD."
        ) from exc
    except urllib.error.URLError as exc:
        raise KeycloakError(
            f"cannot reach Keycloak at {BASE} ({exc.reason}). "
            "Is it running?  cd devops && docker compose up -d --wait"
        ) from exc


def find_user(token: str, username: str) -> dict | None:
    """Exact match. The search endpoint is a prefix match by default, so 'hr' would
    otherwise also return 'hr2' and the script would update the wrong account."""
    query = urllib.parse.urlencode({"username": username, "exact": "true"})
    found = _request("GET", f"/admin/realms/{REALM}/users?{query}", token) or []
    return found[0] if found else None


def realm_roles(token: str) -> dict[str, dict]:
    roles = _request("GET", f"/admin/realms/{REALM}/roles", token) or []
    return {role["name"]: role for role in roles}


def resolve_roles(token: str, wanted: list[str]) -> dict[str, dict]:
    """Look the roles up before anything is written.

    A role that does not exist is fatal rather than skipped: an account with a
    misspelled role authenticates and can do nothing, which reads as a broken
    application rather than a typo. Checked here, before the user is created, so a
    failure leaves nothing behind to clean up.
    """
    available = realm_roles(token)
    unknown = [name for name in wanted if name not in available]
    if unknown:
        raise KeycloakError(
            f"no such realm role: {', '.join(unknown)}. Known roles: {', '.join(sorted(available))}"
        )
    return {name: available[name] for name in wanted}


def assign_roles(token: str, user_id: str, resolved: dict[str, dict]) -> list[str]:
    """Add the realm roles the user does not already have. Returns the ones added."""
    mapped = _request("GET", f"/admin/realms/{REALM}/users/{user_id}/role-mappings/realm", token)
    current = {role["name"] for role in mapped or []}
    missing = [name for name in resolved if name not in current]
    if missing:
        _request(
            "POST",
            f"/admin/realms/{REALM}/users/{user_id}/role-mappings/realm",
            token,
            [resolved[name] for name in missing],
        )
    return missing


def set_password(token: str, user_id: str, password: str, temporary: bool) -> None:
    _request(
        "PUT",
        f"/admin/realms/{REALM}/users/{user_id}/reset-password",
        token,
        {"type": "password", "value": password, "temporary": temporary},
    )


def upsert(args: argparse.Namespace) -> int:
    token = admin_token()

    # Before the write, not after. Otherwise a typo in --roles leaves a created,
    # roleless account behind and the script exits non-zero, so the operator has to
    # guess whether a retry is safe.
    resolved = resolve_roles(token, args.roles)

    profile = {
        "username": args.username,
        "enabled": True,
        "emailVerified": True,
        "email": args.email,
        "firstName": args.first,
        "lastName": args.last,
    }
    profile = {key: value for key, value in profile.items() if value is not None}

    existing = find_user(token, args.username)
    if existing:
        user_id = existing["id"]
        _request("PUT", f"/admin/realms/{REALM}/users/{user_id}", token, profile)
        print(f"user '{args.username}' already existed, updated")
    else:
        _request("POST", f"/admin/realms/{REALM}/users", token, profile)
        created = find_user(token, args.username)
        if not created:
            raise KeycloakError(f"created '{args.username}' but cannot read it back")
        user_id = created["id"]
        print(f"user '{args.username}' created")

    set_password(token, user_id, args.password, args.temporary)
    print(f"  password set ({'temporary' if args.temporary else 'permanent'})")

    if resolved:
        added = assign_roles(token, user_id, resolved)
        print(f"  roles added: {', '.join(added) or 'none (already assigned)'}")
    else:
        # Worth saying out loud. A roleless account logs in and is refused by every
        # endpoint, which looks like an authorization bug from the outside.
        print("  no roles requested - this account can authenticate and nothing else")

    return 0


def list_users(_: argparse.Namespace) -> int:
    token = admin_token()
    users = _request("GET", f"/admin/realms/{REALM}/users?max=200", token) or []
    if not users:
        print(f"no users in realm '{REALM}'")
        return 0

    print(f"{'username':<16} {'email':<24} roles")
    for user in sorted(users, key=lambda u: u["username"]):
        mapped = (
            _request("GET", f"/admin/realms/{REALM}/users/{user['id']}/role-mappings/realm", token)
            or []
        )
        # default-roles-<realm> is Keycloak's own composite, not something anyone set.
        names = sorted(r["name"] for r in mapped if not r["name"].startswith("default-roles"))
        print(f"{user['username']:<16} {user.get('email', '-'):<24} {', '.join(names) or '-'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or update a user in the Keycloak salary realm.",
        epilog=f"Target: {BASE}, realm '{REALM}'. Override with KEYCLOAK_URL / KEYCLOAK_REALM.",
    )
    parser.add_argument("username", nargs="?", help="username to create or update")
    parser.add_argument("--password", help="password to set (required unless --list)")
    parser.add_argument(
        "--roles",
        default="",
        help="comma-separated realm roles, e.g. hr_manager or admin,hr_manager",
    )
    parser.add_argument("--email")
    parser.add_argument("--first", help="first name")
    parser.add_argument("--last", help="last name")
    parser.add_argument(
        "--temporary",
        action="store_true",
        help="force a password change at first login",
    )
    parser.add_argument("--list", action="store_true", help="list users and their roles, then exit")

    args = parser.parse_args()
    args.roles = [role.strip() for role in args.roles.split(",") if role.strip()]

    try:
        if args.list:
            return list_users(args)
        if not args.username or not args.password:
            parser.error("username and --password are required (or pass --list)")
        return upsert(args)
    except KeycloakError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

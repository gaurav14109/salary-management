#!/usr/bin/env bash
#
# Seed the salary realm's users into a *running* Keycloak.
#
# Why this exists: --import-realm only applies realm-salary.json when the realm does
# not already exist, and users are persisted to Postgres. So on any stack that has
# started once, editing that file changes nothing - and the file and the running
# realm drift apart silently. This script is how the file's intent gets applied to a
# Keycloak that is already up.
#
# Idempotent on purpose. Run it as often as you like: it creates what is missing,
# updates what has changed, grants roles that should be there and takes away roles
# that should not. Running it twice leaves the same state as running it once, which
# is what makes it safe to run when you are not sure what the current state is.
#
#   ./seed-users.sh                 # apply the users below
#   ./seed-users.sh --list          # show what Keycloak has right now, change nothing
#
# Passwords here are dev credentials for a local stack, matching realm-salary.json.
# Override them with HR_PASSWORD / ADMIN_PASSWORD if you would rather not have the
# defaults. Nothing here is appropriate for a deployed Keycloak.

set -euo pipefail

cd "$(dirname "$0")"

REALM="${REALM:-salary}"
KC_ADMIN="${KC_ADMIN:-admin}"
KC_ADMIN_PASSWORD="${KC_ADMIN_PASSWORD:-admin}"

HR_PASSWORD="${HR_PASSWORD:-hr}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin}"

# username|email|first|last|password|roles (comma separated)
#
# These are the two personas the product has. admin holds `admin` alone: it is a
# read-only role that sees the whole organisation and changes none of it, so giving
# it hr_manager as well - which the realm used to do - makes "can an admin write?"
# answer yes for the wrong reason.
USERS=(
  "hr-manager|hr@acme.test|Priya|Sharma|${HR_PASSWORD}|hr_manager"
  "admin-user|admin@acme.test|Rahul|Verma|${ADMIN_PASSWORD}|admin"
)

# Only these are managed. Keycloak gives every user composite roles of its own
# (default-roles-salary, offline_access); removing those would break the account.
MANAGED_ROLES="admin hr_manager"

if ! docker compose ps --status running --services 2>/dev/null | grep -qx keycloak; then
  echo "Keycloak is not running. Start it first:" >&2
  echo "  cd devops && docker compose up -d --wait keycloak" >&2
  exit 1
fi

# Everything runs in one exec rather than one per command: kcadm's login token is
# short-lived, and re-authenticating for every role change is both slower and a way
# to fail halfway through.
docker compose exec -T \
  -e REALM="$REALM" \
  -e KC_ADMIN="$KC_ADMIN" \
  -e KC_ADMIN_PASSWORD="$KC_ADMIN_PASSWORD" \
  -e USERS="$(printf '%s\n' "${USERS[@]}")" \
  -e MANAGED_ROLES="$MANAGED_ROLES" \
  -e LIST_ONLY="${1:-}" \
  keycloak sh -s <<'INSIDE'
set -eu

KC=/opt/keycloak/bin/kcadm.sh

$KC config credentials --server http://localhost:8080 \
  --realm master --user "$KC_ADMIN" --password "$KC_ADMIN_PASSWORD" >/dev/null

user_id() {
  # exact=true matters: without it a query for "hr" also matches "hr-manager", and
  # the script would happily edit the wrong account.
  $KC get users -r "$REALM" -q "exact=true" -q "username=$1" \
    --fields id --format csv --noquotes 2>/dev/null | tr -d '\r' | grep -v '^$' | head -1
}

user_id_by_email() {
  $KC get users -r "$REALM" -q "exact=true" -q "email=$1" \
    --fields id --format csv --noquotes 2>/dev/null | tr -d '\r' | grep -v '^$' | head -1
}

realm_roles() {
  $KC get "users/$1/role-mappings/realm" -r "$REALM" \
    --fields name --format csv --noquotes 2>/dev/null | tr -d '\r' | grep -v '^$'
}

report() {
  echo
  echo "username        roles"
  echo "--------------- ------------------------"
  $KC get users -r "$REALM" --fields username --format csv --noquotes 2>/dev/null |
    tr -d '\r' | grep -v '^$' | while read -r name; do
      id=$(user_id "$name")
      mine=$(realm_roles "$id" | grep -x -E "$(echo "$MANAGED_ROLES" | tr ' ' '|')" | tr '\n' ' ')
      printf '%-15s %s\n' "$name" "${mine:-<none>}"
    done
}

if [ "${LIST_ONLY:-}" = "--list" ]; then
  report
  exit 0
fi

echo "$USERS" | while IFS='|' read -r username email first last password roles; do
  [ -n "$username" ] || continue

  id=$(user_id "$username")

  if [ -z "$id" ]; then
    # Same person, different username - the realm file renamed hr to hr-manager
    # after Keycloak already had the account. Email is the stable identity here, so
    # rename rather than create: a second account would hold the same email (which
    # Keycloak refuses) or split one person's audit trail across two subs.
    id=$(user_id_by_email "$email")
    if [ -n "$id" ]; then
      # Keycloak refuses to change a username unless the realm allows it
      # (editUsernameAllowed, off by default - a username is an identity, and
      # letting it move is a decision a realm has to opt into). Turned on for the
      # rename and put back exactly as it was, so this script does not quietly
      # leave the realm more permissive than it found it.
      was=$($KC get "realms/$REALM" --fields editUsernameAllowed \
              --format csv --noquotes 2>/dev/null | tr -d '\r' | head -1)
      $KC update "realms/$REALM" -s editUsernameAllowed=true >/dev/null
      $KC update "users/$id" -r "$REALM" -s "username=$username" >/dev/null
      $KC update "realms/$REALM" -s "editUsernameAllowed=${was:-false}" >/dev/null
      echo "renamed  -> $username"
    fi
  fi

  if [ -z "$id" ]; then
    # emailVerified and a non-temporary password both matter: without them the
    # first sign-in lands on a Keycloak account screen that the BFF's redirect
    # flow has nowhere to put.
    $KC create users -r "$REALM" \
      -s "username=$username" -s "email=$email" \
      -s "firstName=$first" -s "lastName=$last" \
      -s enabled=true -s emailVerified=true >/dev/null
    id=$(user_id "$username")
    echo "created  $username"
  else
    $KC update "users/$id" -r "$REALM" \
      -s "email=$email" -s "firstName=$first" -s "lastName=$last" \
      -s enabled=true -s emailVerified=true >/dev/null
    echo "exists   $username"
  fi

  $KC set-password -r "$REALM" --username "$username" \
    --new-password "$password" >/dev/null 2>&1 || true

  have=$(realm_roles "$id")
  want=$(echo "$roles" | tr ',' '\n' | grep -v '^$')

  for role in $want; do
    if ! echo "$have" | grep -qx "$role"; then
      $KC add-roles -r "$REALM" --uusername "$username" --rolename "$role" >/dev/null
      echo "  + $role"
    fi
  done

  # Take away managed roles this user should not hold. Scoped to MANAGED_ROLES so
  # Keycloak's own composite roles are left alone - stripping default-roles-salary
  # leaves an account that cannot sign in.
  for role in $MANAGED_ROLES; do
    if echo "$have" | grep -qx "$role" && ! echo "$want" | grep -qx "$role"; then
      $KC remove-roles -r "$REALM" --uusername "$username" --rolename "$role" >/dev/null
      echo "  - $role"
    fi
  done
done

report
INSIDE

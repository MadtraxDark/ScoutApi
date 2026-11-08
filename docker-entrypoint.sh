#!/bin/sh
set -eu

# Host shell env often overrides Compose .env with localhost; rewrite loopback
# to the Compose Postgres service hostname when the API runs in-container.
# Pattern is *@localhost: (user:password@host), not *:@localhost:.
if [ -n "${DATABASE_URL:-}" ]; then
  case "$DATABASE_URL" in
    *@localhost:*|*@127.0.0.1:*|*@localhost/*|*@127.0.0.1/*)
      DATABASE_URL=$(
        printf '%s' "$DATABASE_URL" \
          | sed \
            -e 's/@localhost:/@postgres:/g' \
            -e 's/@127.0.0.1:/@postgres:/g' \
            -e 's/@localhost\//@postgres\//g' \
            -e 's/@127.0.0.1\//@postgres\//g'
      )
      export DATABASE_URL
      ;;
  esac
fi

PROFILE_DIR="${CAMOUFOX_USER_DATA_DIR:-/home/app/.cache/scout-api/camoufox-profiles/default}"
PROFILE_ROOT="$(dirname "$PROFILE_DIR")"

mkdir -p "$PROFILE_DIR"
# Sibling used by direct (non-proxy) Camoufox sessions.
mkdir -p "${PROFILE_ROOT}/direct"

# Ownership: do NOT recursively chown the profile tree. On Docker Desktop
# Windows bind mounts, `chown -R` over tens of thousands of Firefox cache
# files produces Input/output errors and can leave the tree inconsistent.
# Named Linux volumes (root-owned) may set CAMOUFOX_CHOWN_PROFILES=root-only
# to chown only the profile root directory (non-recursive).
_chown_mode="${CAMOUFOX_CHOWN_PROFILES:-off}"
case "$_chown_mode" in
  root-only|ROOT-ONLY)
    chown app:app "$PROFILE_ROOT" 2>/dev/null || true
    chown app:app "$PROFILE_DIR" 2>/dev/null || true
    chown app:app "${PROFILE_ROOT}/direct" 2>/dev/null || true
    ;;
esac

# The entrypoint runs as root, so the directories created above are root-owned
# (0755). The API/workers run as `app` and must be able to create the per-locale
# profile dirs (``direct/locale_*``) inside them; otherwise every Camoufox
# launch fails with EACCES and each store fetch surfaces as a block/cooldown.
# Fix only the top-level directories (non-recursive, cheap, safe on bind mounts):
# prefer chown, fall back to a permissive mode when chown is a no-op (Docker
# Desktop bind mounts).
for _dir in "$PROFILE_ROOT" "$PROFILE_DIR" "${PROFILE_ROOT}/direct"; do
  if ! runuser -u app -- test -w "$_dir" 2>/dev/null; then
    chown app:app "$_dir" 2>/dev/null || chmod a+rwx "$_dir" 2>/dev/null || true
    if ! runuser -u app -- test -w "$_dir" 2>/dev/null; then
      echo "camoufox_profile_dir_not_writable: $_dir" >&2
    fi
  fi
done

# Disposable Firefox caches are safe to recreate. Purge them on startup so a
# previously corrupted cache2 (e.g. after failed recursive chown) cannot hang
# launch_persistent_context. Session cookies / prefs / logins are preserved.
_purge="${CAMOUFOX_PURGE_DISPOSABLE_CACHE:-true}"
case "$_purge" in
  1|true|True|TRUE|yes|Yes|YES|on|On|ON)
    if [ -d "$PROFILE_ROOT" ]; then
      find "$PROFILE_ROOT" -type d \( \
          -name cache2 -o -name startupCache -o -name thumbnails \
        \) -print0 2>/dev/null | xargs -0 -r rm -rf 2>/dev/null || true
      find "$PROFILE_ROOT" -type f \( \
          -name '.parentlock' -o -name 'lock' \
        \) -delete 2>/dev/null || true
      echo "camoufox_disposable_cache_purged: root=$PROFILE_ROOT"
    fi
    ;;
esac

# Apply pending schema migrations before API/workers start.
# Concurrent container starts are safe (Alembic Postgres advisory lock).
# Disable with AUTO_MIGRATE=false when a one-shot migrate job already ran.
_should_migrate=1
case "${AUTO_MIGRATE:-true}" in
  0|false|False|FALSE|no|No|NO|off|Off|OFF) _should_migrate=0 ;;
esac
if [ "$_should_migrate" -eq 1 ] && [ -n "${DATABASE_URL:-}" ]; then
  echo "alembic_upgrade_head: starting"
  runuser -u app -- alembic upgrade head
  echo "alembic_upgrade_head: done"
fi

exec runuser -u app -- "$@"

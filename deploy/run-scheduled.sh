#!/bin/sh
# Install this wrapper at an absolute path before adding the Cron entry.
set -eu
umask 077
# The optional trusted file exports shell variables and must stay outside Git.
if [ -n "${PLAYSTORE_ENV_FILE:-}" ]; then
    . "$PLAYSTORE_ENV_FILE"
fi
: "${PLAYSTORE_CONFIG:?Set the absolute JSON configuration path}"
: "${PLAYSTORE_BIN:?Set the absolute installed executable path}"
case "$PLAYSTORE_CONFIG" in /*) ;; *) echo 'PLAYSTORE_CONFIG must be absolute' >&2; exit 2;; esac
case "$PLAYSTORE_BIN" in /*) ;; *) echo 'PLAYSTORE_BIN must be absolute' >&2; exit 2;; esac
exec "$PLAYSTORE_BIN" run --config "$PLAYSTORE_CONFIG"

#!/bin/sh
# Installer cette copie hors du dépôt avec un chemin absolu dans Cron.
set -eu
umask 077
# Le fichier facultatif contient uniquement des exports shell et reste hors de Git.
if [ -n "${PLAYSTORE_ENV_FILE:-}" ]; then
    . "$PLAYSTORE_ENV_FILE"
fi
: "${PLAYSTORE_CONFIG:?Configurer le chemin absolu du fichier JSON}"
: "${PLAYSTORE_BIN:?Configurer le chemin absolu de l'exécutable installé}"
case "$PLAYSTORE_CONFIG" in /*) ;; *) echo 'PLAYSTORE_CONFIG doit être absolu' >&2; exit 2;; esac
case "$PLAYSTORE_BIN" in /*) ;; *) echo 'PLAYSTORE_BIN doit être absolu' >&2; exit 2;; esac
exec "$PLAYSTORE_BIN" run --config "$PLAYSTORE_CONFIG"

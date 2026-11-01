# PlayStoreGetReviews — V2, phase 1

Collecte des avis publics Google Play, stockage SQLite par application et export CSV/JSON. Cette branche réalise la **phase 1** de [doc/specifications.md](doc/specifications.md). L'analyse IA, les rapports HTML, les emails et Cron appartiennent aux phases suivantes.

## Installation et tests

Python 3.11 ou supérieur :

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest
cp config.example.json config.local.json
```

Adapter le JSON : applications, pays/langue, seuil négatif et limite par application. `--config` prime sur la variable exportée `PLAYSTORE_CONFIG`. Les chemins de données sont relatifs au répertoire de lancement. Sans fichier de configuration, les anciens chemins `reviews.db` et `exports` et l'application historique sont conservés. `.env.example` documente les variables ; `.env` n'est pas chargé automatiquement.

```sh
playstoregetreviews collect --config config.local.json
playstoregetreviews export --config config.local.json
# Entrées historiques conservées après installation :
python collect_negative_reviews.py --config config.local.json
python export_reviews.py --config config.local.json
```

Le JSON peut aussi définir `retries` (2), `pause_seconds` (0.6) et `timeout_seconds` (20). La collecte parcourt les pages jusqu'à la limite, reprend les erreurs puis renvoie un code non nul en cas d'échec. Une application en échec n'empêche pas les autres de s'exécuter. Les avis déjà synchronisés avant une erreur de validation restent enregistrés ; un nouveau passage est idempotent. Les logs JSON donnent les compteurs `new`, `modified`, `unchanged`, `ignored`, sans contenu d'avis ni erreurs externes. L'adaptateur réutilise le parseur interne de la version épinglée du scraper pour laisser remonter les erreurs (sa fonction publique les masque). Il rétablit aussi la vérification TLS désactivée par cette dépendance à l'import. Une montée de version exige de revalider cet adaptateur. Une collecte vide peut être légitime ; elle ne prouve pas à elle seule la présence d'avis.

## Stockage et migration

SQLAlchemy gère SQLite. La clé `(application_id, review_id)` évite les doublons. Un hash de la note, du contenu et de la date détecte les modifications. Première collecte conservée, dernière synchronisation actualisée, état d'analyse remis à `pending` après modification. Les avis nouveaux au-dessus du seuil sont ignorés ; les avis déjà connus restent actualisés même si leur note remonte. Les avis disparus de la collecte ne sont pas supprimés.

Une base historique n'identifie pas son application : la migration exige donc une attribution explicite. Sauvegarder les données et choisir une **destination inexistante** dans la configuration. La source reste intacte :

```sh
playstoregetreviews migrate --source reviews.db --package com.visa.cemea.visapay --config config.local.json
```

Les exports conservent les colonnes `score,content`, avec des noms horodatés distincts, et regroupent les applications stockées. Le webhook historique reste facultatif via `REVIEW_WEBHOOK_URL` ; il reçoit uniquement les nouveaux avis négatifs. Son échec est journalisé sans exposer l'URL ; il n'y a pas de reprise durable de notification en phase 1.

## Données et accès externes

Aucun secret n'est requis pour les avis publics. Autoriser HTTPS vers `play.google.com`, et vers le domaine du webhook uniquement si utilisé. Vérifier les conditions applicables de Google Play et les limites de collecte avant exécution ; aucune validation juridique n'est présumée.

Bases, exports, rapports, `.env` et configuration locale sont exclus de Git. Les données historiques déjà présentes dans Git sont retirées du suivi sur cette branche tout en restant sur disque. Cela n'efface pas l'historique Git : ne pas republier ces anciennes données et traiter séparément toute suppression d'historique nécessaire. Ne pas joindre d'avis privés aux logs, tests ou demandes de revue. Les tests utilisent exclusivement des données synthétiques et ne contactent aucun service externe.

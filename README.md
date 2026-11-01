# PlayStoreGetReviews — V2, phases 1 et 2

Collecte des avis publics Google Play, stockage SQLite par application et export CSV/JSON. Cette branche réalise les **phases 1 et 2** de [doc/specifications.md](doc/specifications.md). L'analyse IA OpenAI est disponible. Les rapports HTML, les emails et Cron appartiennent aux phases suivantes.

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


## Phase 2 — Analyse IA OpenAI

Installer le SDK optionnel (`test` l'inclut aussi pour les tests hors réseau) :

```sh
python -m pip install -e '.[llm]'
playstoregetreviews analyze --config config.local.json
# Facultatif : une seule application configurée
playstoregetreviews analyze --config config.local.json --package com.visa.cemea.visapay
```

Fournir `PLAYSTORE_LLM_API_KEY` par les paramètres sécurisés de l'environnement ou un gestionnaire de secrets ; la variable existante `OPENAI_API_KEY` est reconnue en secours. Ne jamais coller une clé dans un fichier suivi ou une commande enregistrée. `.env` n'est pas chargé automatiquement. L'API HTTPS `api.openai.com` doit être autorisée. Les analyses impliquent l'envoi des textes préparés au fournisseur : ne les lancer que sur des données dont cet usage est autorisé.

Le premier fournisseur est OpenAI, via le SDK épinglé et les sorties JSON structurées (`json_schema`, mode strict). Le modèle par défaut est `gpt-4o-mini`, configurable ; choisir un modèle compatible avec les sorties structurées et `max_completion_tokens`. Aucun appel n'est fait sans avis admissible. L'initialisation du SDK est différée : collecte et export fonctionnent sans la dépendance IA ni une clé.

### Préparation et validation

- Seuls les avis `pending` à note inférieure ou égale au seuil de l'application sont candidats. Les avis déjà analysés ne sont pas renvoyés après un simple changement de modèle ; un changement de note, texte ou date les remet en attente.
- Les textes vides, sans lettres exploitables après masquage, ou trop longs pour un lot sont comptés `skipped` et restent en attente. Aucun texte n'est tronqué. Un dépassement du quota de lots est compté `deferred` ; relancer pour traiter la suite.
- Les emails, numéros de téléphone et URLs sont masqués. Les noms d'auteur, identifiants réels et dates des avis ne sont pas envoyés. Ce masquage par règles n'est pas une garantie d'anonymisation : il ne détecte pas tous les noms, adresses ou autres informations personnelles en texte libre. Vérifier la pertinence des données avant envoi. Les noms/descriptions d'applications doivent aussi être non sensibles.
- Les textes préparés identiques de même note sont regroupés dans chaque lot, avec des références opaques. Un problème cité pour un groupe est relié à toutes ses sources ; ses fréquences sont calculées localement, sans compter deux fois un avis. Des problèmes différents peuvent citer les mêmes avis ; leurs comptes ne s'additionnent pas nécessairement.
- Le prompt traite avis et métadonnées comme des données non fiables, sans outils ni exécution. Il demande de distinguer symptômes signalés et causes supposées, avec confiance et limites. Cela ne garantit pas l'exactitude factuelle des conclusions du modèle : une revue humaine reste nécessaire.
- Pydantic rejette propriétés supplémentaires, catégories/priorités invalides, références inventées ou répétées, refus et réponses tronquées. Les comptes ne sont pas demandés au modèle. Une sortie invalide n'est pas conservée ni marquée analysée ; la consommation connue reste enregistrée.

### Limites et consommation

La section `analysis` du JSON expose :

| Paramètre | Défaut | Effet |
|---|---|---|
| `model` | `gpt-4o-mini` | Modèle OpenAI compatible |
| `max_reviews_per_batch` | 40 | Nombre de sources par lot, doublons compris |
| `max_batches_per_run` | 5 | Maximum de tentatives par commande, partagé entre applications |
| `max_input_chars` | 24000 | Taille des messages par lot, métadonnées et consigne incluses |
| `max_output_tokens` | 3000 | Limite de sortie de chaque appel |
| `timeout_seconds` | 60 | Délai du SDK |
| `input_cost_per_million` | non défini | Tarif USD / million de tokens d'entrée |
| `output_cost_per_million` | non défini | Tarif USD / million de tokens de sortie |

Le schéma JSON transmis au fournisseur ajoute un coût d'entrée aux messages. Ces limites bornent le volume envoyé et le nombre d'appels, sans constituer un plafond monétaire. Renseigner les **deux** tarifs courants du modèle pour obtenir un coût estimé à partir des tokens rapportés. Sans tarifs ou sans usage retourné, le coût reste `null`, jamais zéro par hypothèse. La facture fournisseur reste la référence ; remises et tarifs spéciaux ne sont pas calculés.

Il n'y a pas de reprise automatique d'appel LLM payant. Après l'échec d'un lot, les suivants de cette application ne sont pas appelés. Une nouvelle commande pourra réessayer les avis encore en attente ; un timeout peut avoir été facturé même si son usage n'est pas connu.

### Historique et reprise

Les tables `analyses` et `analysis_reviews` sont ajoutées à la base V2 existante sans modifier les avis. L'historique conserve statut, modèle/fournisseur, version du prompt, dates, résultat validé, tokens, coût estimé et hash de chaque version d'avis. Les résultats restent locaux dans SQLite ; la génération de fichiers de rapports arrivera en phase 3. L'identifiant de l'analyse apparaît dans les logs JSON.

```sh
playstoregetreviews analysis-show --config config.local.json --analysis-id 1
```

Cette commande affiche un résultat et ses références réelles : sa sortie est privée. Ne pas la publier. Les sorties invalides et erreurs externes brutes ne sont pas stockées ni journalisées.

Une réservation SQLite bloque les analyses concurrentes d'une même application. Si un avis change pendant l'appel, le résultat reste associé à son ancien hash et la nouvelle version reste en attente. Après interruption brutale, la réservation reste `running` pour éviter une nouvelle dépense automatique. **Arrêter et vérifier l'arrêt du processus propriétaire** avant de libérer explicitement son identifiant :

```sh
playstoregetreviews analysis-recover --config config.local.json --analysis-id 1
```

La réservation devient `failed`, avec consommation inconnue ; les avis restent en attente. Ne pas utiliser cette commande pendant qu'un appel est encore actif : cela permettrait un doublon payant. Une analyse terminée ne peut pas être réinitialisée par cette commande.

### Validation de la phase 2

`python -m pytest` exécute les tests de phases 1 et 2 avec des données synthétiques. Ils couvrent l'incrémentalité, la migration du schéma, les versions modifiées pendant l'appel, la réservation, les lots, la préparation des données, les réponses invalides/refusées/tronquées, les références et comptes, la consommation même en cas d'échec, les commandes et le véritable SDK avec un transport HTTP simulé. Aucun test n'envoie de données à OpenAI. Une validation réelle nécessite une clé, un accès au modèle et l'autorisation réseau ; elle n'a pas encore été effectuée dans cet environnement.

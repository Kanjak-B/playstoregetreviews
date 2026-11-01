# Exploitation V2.0

La V2 réalise les cinq phases du cahier des charges. OpenAI est le premier fournisseur LLM ; Gemini et Docker restent optionnels et ne sont pas nécessaires. Python 3.11+ est requis. Les tests n'utilisent ni secrets ni destinataires réels.

## Installation

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[llm]'
cp config.example.json config.local.json
# Tests de développement :
python -m pip install -e '.[test,llm]'
python -m pytest -q
python -m pip check
```

Utiliser des **chemins absolus** pour `db_path`, `report_dir` et `export_dir` en production. Les chemins relatifs restent relatifs au répertoire courant, pas au fichier JSON. Mettre les données dans un dossier privé (mode 700), les fichiers de configuration et secrets en mode 600. Les nouvelles bases et sorties de rapport sont créées en mode 600. Les bases existantes sont protégées lors de l'ouverture ; conserver leurs sauvegardes également privées.

La configuration JSON et les variables exportées sont les sources de configuration ; `.env` n'est pas chargé automatiquement. Les clés et identifiants ne doivent jamais figurer dans Git, un argument de commande ou un journal. Sauvegarder SQLite à l'arrêt de l'application ou avec l'API SQLite `backup` ; ne pas copier un fichier en cours d'écriture sans garantie de cohérence.

## Commandes manuelles

```sh
playstoregetreviews collect --config config.local.json
playstoregetreviews analyze --config config.local.json
playstoregetreviews report --config config.local.json
playstoregetreviews report --config config.local.json --report-id 1
playstoregetreviews email-preview --config config.local.json --report-id 1
playstoregetreviews send-email --config config.local.json --report-id 1
playstoregetreviews email-status --config config.local.json --delivery-id 1
playstoregetreviews export --config config.local.json
```

`--package` sélectionne une application configurée pour `analyze`, `report` et `run`. Les scripts historiques restent disponibles après installation. L'import de l'ancienne base exige `migrate --source ... --package ...` avec une destination inexistante. Sauvegarder avant toute migration. L'ouverture d'une base V2 ajoute automatiquement les nouvelles tables et les colonnes nullables `analysis_reviews.review_date` et `applications.recipient_email`, sans supprimer les données.

## Phase 3 : rapports

`report` consomme les analyses terminées non encore rattachées à un rapport. Il génère `report-ID.json` et `report-ID.html`, conserve le document dans SQLite et permet de le régénérer par identifiant. L'écriture est atomique par fichier ; si l'un des deux fichiers échoue, le document reste en base et la prochaine commande reprend les rendus incomplets.

Chaque rapport comporte application, génération et période d'analyse en UTC, période des avis, nombre de sources distinctes, synthèses, priorités, recommandations, confiance, limites, modèles et consommation connue. Les références et hashes assurent la traçabilité sans réexporter le contenu brut des avis. Les anciennes analyses n'avaient pas de copie des dates sources : leur période peut être inconnue/partielle, ce que signale le rapport. Les nouvelles analyses enregistrent ces dates avant l'appel LLM.

Les rapports portent sur les nouveaux lots disponibles depuis le rapport précédent. Plusieurs versions d'un avis dans ces lots ne sont comptées qu'une fois, pour la version la plus récente. Les problèmes aux titres et catégories identiques sont regroupés ; leurs fréquences sont des nombres de références distinctes, pas des estimations du modèle. Une fusion sémantique de titres différents n'est pas présumée. Les coûts incluent les analyses consommées, y compris les anciennes versions supersédées dans le rapport ; un usage ou tarif inconnu donne `null`.

L'empreinte des versions sources par application supprime les rapports identiques. Un retour à un ensemble de versions déjà rapporté réutilise le rapport initial sans nouveau message. Les rendus HTML utilisent Jinja2 avec échappement automatique ; aucun texte du modèle n'est considéré comme du HTML sûr. Les templates sont inclus dans le paquet installable et ne dépendent pas du répertoire courant.

## Phase 4 : emails

L'envoi est **désactivé par défaut**. Pour l'activer, configurer :

```json
{
  "email": {
    "enabled": true,
    "host": "smtp.example.com",
    "port": 587,
    "security": "starttls",
    "from_address": "reports@example.com",
    "auth_required": true,
    "max_attempts": 3,
    "max_deliveries_per_run": 10
  },
  "apps": [{
    "package_name": "com.example.app",
    "name": "Example App",
    "recipient_email": "authorized@example.com",
    "recipient_authorized": true,
    "interval_minutes": 1440
  }]
}
```

Cet extrait doit être fusionné avec les autres réglages nécessaires dans la configuration privée. `recipient_authorized: true` signifie que l'opérateur a obtenu le consentement du destinataire. Aucun destinataire n'est issu du LLM et aucune option `--to` ne permet de contourner la configuration. Les adresses d'en-tête simples sont validées ; les adresses avec nom d'affichage ou retour à la ligne sont refusées.

Fournir `PLAYSTORE_SMTP_USERNAME` et `PLAYSTORE_SMTP_PASSWORD` via un gestionnaire de secrets ou des exports privés. Ces valeurs doivent être accessibles au processus SMTP ; un jeton substitué uniquement par un proxy HTTPS ne suffit pas pour SMTP. `auth_required: false` convient uniquement à un relais chiffré explicitement autorisé sans authentification. Le transport accepte STARTTLS ou TLS implicite (`security: ssl`, usuellement port 465), avec validation des certificats et noms d'hôte ; aucun mode en clair n'est fourni. Autoriser également le routage TCP vers l'hôte/port SMTP, indépendamment des règles HTTPS. Le transport SMTP standard ne passe pas par `HTTPS_PROXY`.

`email-preview` écrit un `.eml` multipart et un aperçu HTML local sans connexion ni réservation d'envoi. La synthèse email présente les principales recommandations, avec le rapport complet JSON et HTML en pièces jointes. L'aperçu requiert un expéditeur et un destinataire autorisé, mais ni identifiants ni SMTP activé. Vérifier cet aperçu avant l'envoi réel.

Un seul envoi par couple rapport/destinataire est réservé en transaction. Son `Message-ID` reste stable lors des reprises. Un envoi réussi n'est pas répété. Les échecs certains avant acceptation (`failed`) peuvent être retentés lors d'une nouvelle commande ou du prochain pipeline, avec un maximum persistant de `max_attempts`. Les rapports restent conservés après un échec. Les identifiants et messages d'erreur externes bruts ne sont jamais journalisés.

**SMTP ne permet pas de garantir exactement une livraison dans tous les cas.** Une déconnexion ou un timeout pendant l'envoi peut survenir après acceptation ; cet état devient `unknown` et bloque toute reprise automatique. Une interruption brutale peut laisser `sending`, également bloqué. Après arrêt confirmé du processus et vérification des journaux du fournisseur :

```sh
# Le serveur confirme l'acceptation :
playstoregetreviews email-resolve --config config.local.json --delivery-id 1 --outcome sent
# Le serveur confirme que le message n'a PAS été accepté :
playstoregetreviews email-resolve --config config.local.json --delivery-id 1 --outcome retry
```

Ne pas autoriser `retry` sans cette confirmation : le même `Message-ID` n'oblige pas tous les serveurs à dédupliquer. Les états d'envoi et adresses retournés par `email-status` sont privés. Aucun email vide n'est créé quand il n'y a pas d'analyse réussie avec des sources.

## Phase 5 : pipeline et Cron

```sh
playstoregetreviews run --config /etc/playstoregetreviews/config.json
playstoregetreviews run --config /etc/playstoregetreviews/config.json --package com.example.app --force
```

Le pipeline respecte `interval_minutes` par application (1440 par défaut) après un succès. Il collecte, analyse uniquement les avis en attente, génère les rapports et envoie ceux non déjà transmis si l'envoi est activé et le destinataire autorisé. Sans autorisation d'email, il produit seulement les rapports. Les limites de lots LLM et d'envois sont partagées entre applications de la commande. Un backlog ou un échec laisse le pipeline incomplet (code non nul) et peut être repris sans refaire les analyses réussies ni renvoyer les messages confirmés. Les avis vides/trop longs restent comptés comme ignorés par l'analyse et ne causent pas de boucle payante. `--force` ignore la cadence, jamais la déduplication ni les réservations.

Une réservation SQLite par application évite deux pipelines simultanés. Les commandes manuelles d'analyse et d'envoi conservent leurs propres réservations. Une interruption peut laisser des réservations actives ; après arrêt confirmé du processus :

```sh
playstoregetreviews run-recover --config config.local.json --package com.example.app
# Seulement si une analyse est aussi restée active :
playstoregetreviews analysis-recover --config config.local.json --analysis-id 1
```

Ces commandes ne réinitialisent pas les envois incertains. Les résoudre séparément comme décrit ci-dessus.

Pour Cron, copier et adapter [run-scheduled.sh](../deploy/run-scheduled.sh) et [playstoregetreviews.cron.example](../deploy/playstoregetreviews.cron.example). Le fichier `runtime.env`, hors du dépôt et en mode 600, exporte les chemins absolus `PLAYSTORE_BIN`, `PLAYSTORE_CONFIG` et les secrets requis. Le wrapper est un script shell : son fichier d'environnement est du code de confiance, pas un fichier fourni par un tiers. Créer le dossier de journal en mode 700 et prévoir une rotation des logs. Installer l'entrée adaptée avec `crontab -e` sur la machine qui exécute le projet. Aucune tâche Cron active n'est installée automatiquement dans l'environnement de développement.

## Vérification et limites de déploiement

Les tests exécutent le cycle complet avec données synthétiques, transports simulés et un serveur SMTP TLS local à certificat vérifié : reports échappés, multi-lots, migration additive, reprises, acceptations incertaines, réservations, consentement, planification et absence de réanalyse/renvoi. Le workflow GitHub Actions les lance sur Python 3.11, 3.12 et 3.13 et construit le paquet. Cela ne remplace pas une validation des services externes avec les accès de production.

Dans un environnement cloud restreint, prévoir HTTPS `play.google.com` et `api.openai.com`, les secrets LLM, et un accès TCP SMTP autorisé. Les tests réels de collecte, d'analyse payante et d'envoi nécessitent ces prérequis. Ne pas activer la planification d'envois avant leur configuration et vérification. Les rapports, bases, logs, aperçus et configurations locales sont exclus du suivi Git ; l'ancien historique privé n'est pas purgé automatiquement.

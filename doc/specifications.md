# CAHIER DES CHARGES
## PlayStoreGetReviews — Version 2.0
**Projet :** Analyse intelligente des avis négatifs Google Play Store  
**Dépôt existant :** https://github.com/Kanjak-B/playstoregetreviews  
**Type :** Application Python automatisée avec intelligence artificielle  
**Version du document :** 1.0  
**Date :** 1er novembre 2026

---

## 1. Présentation du projet

### 1.1 Contexte
PlayStoreGetReviews est un outil Python permettant de récupérer les avis publics d'une application Google Play Store, de filtrer les évaluations négatives et de les enregistrer dans une base SQLite.

La version 2.0 doit enrichir ce système avec l'intelligence artificielle pour transformer les avis négatifs en rapports d'analyse et en recommandations d'amélioration destinées aux développeurs.

### 1.2 Objectif général
Développer une solution automatisée capable de collecter, stocker, analyser et synthétiser les avis négatifs d'applications Android, puis de transmettre un rapport structuré par email.

### 1.3 Objectifs spécifiques
- Réutiliser et améliorer le collecteur existant.
- Identifier les problèmes récurrents signalés par les utilisateurs.
- Proposer des recommandations techniques et fonctionnelles.
- Prioriser les problèmes selon leur fréquence et leur gravité apparente.
- Générer des rapports JSON et HTML.
- Automatiser l'envoi des rapports par email.
- Éviter les analyses redondantes et les dépenses API inutiles.

## 2. Périmètre fonctionnel

### F01 — Configuration des applications
Le système doit permettre de définir :
- L'identifiant Play Store (`package_name`).
- Le nom et la courte description de l'application.
- Le pays et la langue de collecte.
- La note maximale des avis négatifs (par défaut 2 étoiles).
- Le nombre maximal d'avis à récupérer.
- L'adresse email destinataire autorisée.
- La fréquence des analyses.

La configuration doit être externalisée dans des fichiers adaptés, des variables d'environnement ou une base de données.

### F02 — Collecte des avis
Réutiliser `google_play_scraper` dans un premier temps.

Le collecteur doit :
- Récupérer les avis avec pagination.
- Filtrer les notes de 1 et 2 étoiles par défaut.
- Respecter les limites de collecte configurées.
- Gérer les erreurs réseau, les délais et les tentatives de reprise.
- Journaliser les opérations.
- Gérer plusieurs applications.
- Vérifier les conditions d'utilisation applicables à la collecte.

### F03 — Stockage et mise à jour
Utiliser SQLite pour le MVP.

Le système doit :
- Éviter les doublons grâce à une clé composée de l'application et de l'identifiant de l'avis.
- Détecter les avis modifiés.
- Mettre à jour leur contenu, leur note et leur date.
- Conserver les dates de première collecte et de dernière synchronisation.
- Identifier les avis nouveaux, modifiés et inchangés.
- Conserver un état de traitement pour l'analyse IA.
- Associer chaque avis à son application.

Les avis modifiés doivent pouvoir être réanalysés.

### F04 — Préparation des données
Avant chaque analyse :
- Exclure les avis sans contenu exploitable.
- Dédupliquer les textes identiques lorsque cela est pertinent.
- Nettoyer les données sans altérer le sens.
- Retirer ou masquer les données personnelles non nécessaires.
- Regrouper les avis en lots si leur volume dépasse la capacité ou le budget configuré du LLM.
- Préserver les identifiants permettant de retrouver les avis sources.

### F05 — Analyse IA
Intégrer au moins un fournisseur de LLM, au choix :
- OpenAI.
- Google Gemini.

Le moteur doit recevoir :
- Le nom et la description de l'application.
- Les avis négatifs sélectionnés.
- Les notes associées.
- Les instructions d'analyse.

Il doit produire :
1. Un résumé général des retours négatifs.
2. Une classification des problèmes (bugs, performances, UX/UI, sécurité, fonctionnalités, etc.).
3. Les problèmes récurrents et le nombre d'avis concernés.
4. Une estimation de priorité : faible, moyenne, élevée, critique.
5. Des recommandations concrètes.
6. Des exemples ou références d'avis justifiant les constats.
7. Une indication de confiance et des limites de l'analyse.

Le LLM ne doit pas présenter une cause technique supposée comme un bug confirmé.

Les avis utilisateurs doivent être considérés comme des données non fiables et jamais comme des instructions à exécuter.

### F06 — Sortie structurée
Le résultat doit suivre un schéma JSON défini et validé avec Pydantic.

Exemple :

```json
{
  "app_name": "Example App",
  "reviews_analyzed": 100,
  "summary": "Résumé global",
  "issues": [
    {
      "category": "performance",
      "title": "Démarrage lent",
      "review_count": 25,
      "priority": "high",
      "review_ids": ["review_001"],
      "recommendations": [
        "Mesurer le temps de démarrage",
        "Examiner les opérations bloquantes"
      ]
    }
  ]
}
```

Les fréquences doivent être calculées ou vérifiées à partir des avis sources, et non simplement acceptées lorsqu'elles sont inventées par le modèle.

### F07 — Génération des rapports
Produire :
- Un rapport JSON structuré.
- Un rapport HTML lisible.
- Une synthèse adaptée à l'email.

Chaque rapport doit comporter la date d'analyse, l'application concernée, le volume d'avis, la période étudiée, les problèmes identifiés et les recommandations prioritaires.

### F08 — Envoi d'emails
Le système doit :
- Générer un email HTML professionnel.
- Utiliser SMTP ou un fournisseur transactionnel configurable.
- Envoyer le rapport à une adresse configurée et autorisée.
- Permettre de prévisualiser le rapport avant envoi.
- Conserver l'historique des envois.
- Gérer les échecs d'envoi et les reprises sans générer de doublons.

L'envoi à des destinataires inconnus ou non consentants est exclu du MVP.

### F09 — Automatisation
Prévoir une exécution manuelle et programmée.

Le système doit pouvoir :
- Collecter les nouveaux avis selon une fréquence configurée.
- Analyser les nouveaux avis et les avis modifiés.
- Éviter de réanalyser inutilement les avis inchangés.
- Produire un rapport périodique.
- Ne pas envoyer de rapport vide ou identique sans raison configurée.

Utiliser Cron pour le MVP.

### F10 — Export et historique
Conserver les exports CSV et JSON existants.

Ajouter :
- L'historique des analyses IA.
- Les rapports générés.
- Le fournisseur et le modèle IA utilisés.
- Les métadonnées de consommation API.
- Le statut des envois d'emails.

## 3. Exigences non fonctionnelles

### Architecture
- Architecture modulaire.
- Séparation entre collecte, stockage, analyse, génération de rapports et notification.
- Exécution possible en ligne de commande.
- Pas d'interface web obligatoire pour le MVP.

### Sécurité
- Clés API et identifiants SMTP stockés dans des variables d'environnement.
- Fichier `.env` exclu de Git.
- Base SQLite, exports et rapports privés exclus du suivi Git.
- Validation des entrées et des réponses IA.
- Protection des données personnelles.
- Échappement du contenu utilisateur lors de la génération HTML.
- Limitation des ressources et des coûts API.

### Fiabilité
- Gestion des exceptions.
- Logs structurés.
- Reprise sur erreur transitoire.
- Prévention des doublons.
- Conservation des rapports en cas d'échec d'email.
- Gestion des avis supprimés ou devenus indisponibles sans suppression automatique des données historiques.

### Maintenabilité
- Typage Python.
- Configuration centralisée.
- Documentation README.
- Tests automatisés avec Pytest.
- Dépendances déclarées et versions maîtrisées.

## 4. Stack technique

| Composant | Technologie |
|---|---|
| Langage | Python 3.11+ |
| Collecte | google-play-scraper |
| Stockage MVP | SQLite |
| ORM | SQLAlchemy |
| Validation | Pydantic |
| IA | OpenAI SDK / Google GenAI SDK |
| Emails | SMTP |
| Templates HTML | Jinja2 |
| Automatisation | Cron |
| Tests | Pytest |
| Conteneurisation | Docker, facultatif pour le MVP |

FastAPI et PostgreSQL ne sont pas nécessaires au MVP. Ils pourront être introduits si une interface API ou un fonctionnement multi-utilisateur devient nécessaire.

## 5. Architecture cible

```text
playstoregetreviews/
├── src/
│   └── playstoregetreviews/
│       ├── config.py
│       ├── collector/
│       │   └── google_play.py
│       ├── database/
│       │   ├── models.py
│       │   └── repository.py
│       ├── analysis/
│       │   ├── service.py
│       │   ├── schemas.py
│       │   └── providers/
│       │       ├── openai.py
│       │       └── gemini.py
│       ├── reports/
│       │   └── generator.py
│       ├── notifications/
│       │   └── email.py
│       └── main.py
├── templates/
│   └── report.html
├── tests/
├── data/
├── reports/
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

Cette arborescence est indicative. Les scripts existants doivent être conservés ou migrés sans régression fonctionnelle.

## 6. Modèle de données minimal

### applications
- id
- package_name (unique)
- name
- description
- language
- country
- recipient_email
- created_at
- updated_at

### reviews
- id
- application_id
- review_id
- score
- content
- review_date
- first_seen_at
- last_seen_at
- content_hash
- analysis_status

Contrainte d'unicité : `(application_id, review_id)`.

### analyses
- id
- application_id
- model_provider
- model_name
- status
- total_reviews
- result_json
- estimated_cost
- created_at

### analysis_reviews
- analysis_id
- review_id
- content_hash

Cette table permet de savoir quelle version d'un avis a participé à une analyse.

### email_deliveries
- id
- analysis_id
- recipient
- status
- sent_at
- error_message

## 7. Phases de développement

### Phase 1 — Refactorisation
- Restructurer le projet.
- Externaliser la configuration.
- Améliorer SQLite et la déduplication.
- Détecter les modifications des avis.
- Ajouter les logs et les tests de collecte.

### Phase 2 — Intégration IA
- Configurer un premier fournisseur LLM.
- Construire les instructions d'analyse.
- Définir les schémas Pydantic.
- Analyser des lots d'avis.
- Valider les résultats et les références aux avis sources.
- Suivre la consommation API.

### Phase 3 — Rapports
- Générer les résultats JSON.
- Créer un template HTML.
- Sauvegarder et consulter les rapports localement.

### Phase 4 — Emails
- Ajouter la configuration SMTP.
- Prévisualiser les emails.
- Gérer l'envoi et les échecs.
- Enregistrer l'historique des notifications.

### Phase 5 — Automatisation et livraison
- Configurer Cron.
- Ajouter l'exécution incrémentale.
- Compléter les tests.
- Documenter l'installation et l'utilisation.
- Ajouter Docker si nécessaire.

## 8. Critères d'acceptation

La version 2.0 est considérée comme fonctionnelle lorsque :

- [ ] Une application peut être configurée sans modifier le code source.
- [ ] Les avis négatifs peuvent être récupérés et stockés.
- [ ] Les doublons sont évités.
- [ ] Une modification d'avis est détectée et enregistrée.
- [ ] Les avis non encore analysés sont identifiables.
- [ ] Le système peut utiliser une API LLM pour produire une analyse.
- [ ] La sortie JSON respecte le schéma Pydantic.
- [ ] Les problèmes sont associés à des avis sources.
- [ ] Un rapport HTML est généré.
- [ ] Un email peut être envoyé à un destinataire autorisé.
- [ ] Un échec d'envoi peut être repris sans doublon.
- [ ] Une exécution automatique est possible.
- [ ] Les clés API et les données privées ne sont pas publiées dans Git.
- [ ] Les tests essentiels passent.

## 9. Hors périmètre de la V2.0

- Entraînement d'un modèle d'IA propriétaire.
- Application mobile.
- Interface web complète.
- Gestion multi-tenant ou SaaS.
- Paiement et abonnements.
- Envoi massif d'emails à des développeurs tiers.
- Agents IA autonomes.
- Analyse de toutes les langues ou de tous les pays en une seule exécution.

## 10. Livrables attendus

1. Code source Python refactorisé.
2. Collecteur amélioré et système de persistance.
3. Module d'analyse IA.
4. Schémas de sortie structurée.
5. Générateur de rapports JSON et HTML.
6. Module d'envoi d'emails.
7. Configuration d'exécution automatique.
8. Tests automatisés.
9. Fichier `.env.example`.
10. Documentation d'installation et d'utilisation.

**Principe directeur :** faire évoluer le dépôt existant, conserver ses fonctionnalités opérationnelles et privilégier une architecture simple, modulaire, testable et extensible.
#!/usr/bin/env python3
"""
collect_negative_reviews.py
- Récupère avis publics d'une app Play Store (tierce) via google_play_scraper
- Filtre avis <= 2 étoiles
- Stocke les avis non encore vus dans SQLite pour éviter doublons
- Optionnel : envoie chaque nouvel avis à un webhook (Slack, etc.)
"""

from google_play_scraper import reviews, Sort
import sqlite3
import time
import requests
import json
import sys
from datetime import datetime

# ====== CONFIG ======
PACKAGE_NAME = "com.visa.cemea.visapay"   # <-- remplace par le package target
DB_PATH = "reviews.db"
WEBHOOK_URL = None  # <-- si tu veux poster sur Slack/Webhook, mets l'URL ici, sinon None
MAX_FETCH = 500     # nombre max d'avis à récupérer par run (ajuste si tu veux)
LANG = "fr"         # langue des avis
COUNTRY = "cd"      # pays (fr, us, etc.)
PAUSE_BETWEEN_PAGES = 0.6  # secondes entre pages pour ne pas surcharger
# ====================

# Init DB
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS reviews (
    reviewId TEXT PRIMARY KEY,
    author TEXT,
    score INTEGER,
    content TEXT,
    at TEXT,
    raw_json TEXT,
    inserted_at TEXT
)""")
conn.commit()

def fetch_reviews(package, max_items=500):
    """Récupère jusqu'à max_items avis publics via google_play_scraper."""
    all_reviews = []
    token = None
    fetched = 0
    while fetched < max_items:
        count = min(200, max_items - fetched)  # google_play_scraper permet pages d'environ 100-200
        try:
            results, token = reviews(
                package,
                lang=LANG,
                country=COUNTRY,
                sort=Sort.NEWEST,
                count=count,
                continuation_token=token
            )
        except Exception as e:
            print("Erreur fetch:", e, file=sys.stderr)
            break
        if not results:
            break
        all_reviews.extend(results)
        fetched += len(results)
        if not token:
            break
        time.sleep(PAUSE_BETWEEN_PAGES)
    return all_reviews

def filter_negative(reviews_list):
    return [r for r in reviews_list if r.get("score", 0) <= 2]

def already_seen(review_id):
    cur.execute("SELECT 1 FROM reviews WHERE reviewId = ?", (review_id,))
    return cur.fetchone() is not None

def save_review(r):
    reviewId = r.get("reviewId")
    author = r.get("userName")
    score = r.get("score")
    content = r.get("content")
    at = r.get("at").isoformat() if isinstance(r.get("at"), datetime) else str(r.get("at"))
    raw = json.dumps(r, default=str, ensure_ascii=False)
    inserted = datetime.utcnow().isoformat()
    cur.execute("""
        INSERT OR IGNORE INTO reviews (reviewId, author, score, content, at, raw_json, inserted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (reviewId, author, score, content, at, raw, inserted))
    conn.commit()

def post_webhook(r):
    if not WEBHOOK_URL:
        return
    payload = {
        "text": f"Nouvel avis négatif ({r.get('score')}★) par {r.get('userName')}\n{r.get('content')}",
        "attachments": [
            {"fields": [
                {"title": "App", "value": PACKAGE_NAME, "short": True},
                {"title": "ReviewId", "value": r.get("reviewId"), "short": True},
                {"title": "Date", "value": str(r.get("at")), "short": True}
            ]}
        ]
    }
    try:
        headers = {"Content-Type": "application/json"}
        requests.post(WEBHOOK_URL, data=json.dumps(payload), headers=headers, timeout=8)
    except Exception as e:
        print("Webhook error:", e)

def main():
    print(f"[{datetime.utcnow().isoformat()}] Démarrage récupération pour {PACKAGE_NAME}")
    all_reviews = fetch_reviews(PACKAGE_NAME, max_items=MAX_FETCH)
    negatives = filter_negative(all_reviews)
    print(f"Récupérés: {len(all_reviews)} avis — Négatifs trouvés: {len(negatives)}")
    new_count = 0
    for r in negatives:
        rid = r.get("reviewId")
        if not rid:
            continue
        if already_seen(rid):
            continue
        save_review(r)
        post_webhook(r)
        new_count += 1
    print(f"Nouveaux avis négatifs enregistrés: {new_count}")
    conn.close()

if __name__ == "__main__":
    main()

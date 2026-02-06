#!/usr/bin/env python3
"""
export_reviews.py
- Lit la base SQLite reviews.db
- Exporte les colonnes score et content dans deux fichiers :
  reviews_YYYY-MM-DD_HH-MM.csv et reviews_YYYY-MM-DD_HH-MM.json
"""

import sqlite3
import csv
import json
from datetime import datetime
import os

# ===== CONFIG =====
DB_PATH = "reviews.db"
EXPORT_DIR = "exports"  # dossier où stocker les exports
# ==================

# création dossier si inexistant
os.makedirs(EXPORT_DIR, exist_ok=True)

# nom horodaté
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
csv_file = os.path.join(EXPORT_DIR, f"reviews_{timestamp}.csv")
json_file = os.path.join(EXPORT_DIR, f"reviews_{timestamp}.json")

# connexion DB
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# lecture des données
cur.execute("SELECT score, content FROM reviews")
rows = cur.fetchall()

# export CSV
with open(csv_file, "w", newline="", encoding="utf-8") as f_csv:
    writer = csv.writer(f_csv)
    writer.writerow(["score", "content"])
    writer.writerows(rows)

# export JSON
data = [{"score": row[0], "content": row[1]} for row in rows]
with open(json_file, "w", encoding="utf-8") as f_json:
    json.dump(data, f_json, ensure_ascii=False, indent=2)

# fermeture
conn.close()

print(f"✅ Export terminé :")
print(f"- CSV : {csv_file}")
print(f"- JSON : {json_file}")
print(f"Total d'avis exportés : {len(rows)}")

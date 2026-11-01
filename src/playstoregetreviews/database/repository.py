"""Transactions, déduplication et migration explicite de la base historique."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session
from .models import Application, Base, Review
from ..config import AppConfig


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized(review: dict[str, Any]) -> dict[str, Any]:
    rid, score = review.get("reviewId"), review.get("score")
    if not isinstance(rid, str) or not rid.strip():
        raise ValueError("identifiant d'avis manquant")
    if type(score) is not int or not 1 <= score <= 5:
        raise ValueError("note invalide")
    content = review.get("content") or ""
    if not isinstance(content, str):
        raise ValueError("contenu invalide")
    date = review.get("at")
    date = date.isoformat() if isinstance(date, datetime) else str(date or "")
    digest = hashlib.sha256(json.dumps([score, content, date], ensure_ascii=False).encode()).hexdigest()
    return dict(review_id=rid, score=score, content=content, review_date=date, content_hash=digest)


class Repository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.engine = create_engine(f"sqlite:///{path}")
        if inspect(self.engine).has_table("reviews"):
            columns = {c['name'] for c in inspect(self.engine).get_columns("reviews")}
            if "reviewId" in columns:
                self.engine.dispose()
                raise ValueError("Base historique : utiliser migrate avec --package explicite")
        Base.metadata.create_all(self.engine)
        # Migration additive de V2 phase 2 : les anciennes dates restent inconnues.
        with self.engine.begin() as connection:
            connection.execute(text('BEGIN IMMEDIATE'))
            columns = {c['name'] for c in inspect(connection).get_columns('analysis_reviews')}
            if 'review_date' not in columns:
                connection.execute(text('ALTER TABLE analysis_reviews ADD COLUMN review_date TEXT'))
            app_columns = {c['name'] for c in inspect(connection).get_columns('applications')}
            if 'recipient_email' not in app_columns:
                connection.execute(text('ALTER TABLE applications ADD COLUMN recipient_email TEXT'))
        path.chmod(0o600)

    def close(self) -> None:
        self.engine.dispose()

    def sync(self, app: AppConfig, reviews: list[dict[str, Any]]) -> dict[str, int]:
        counts = dict(new=0, modified=0, unchanged=0, ignored=0)
        with Session(self.engine) as session, session.begin():
            timestamp = now()
            application = session.scalar(select(Application).where(Application.package_name == app.package_name))
            if application is None:
                application = Application(package_name=app.package_name, created_at=timestamp)
                session.add(application)
            for key in ("name", "description", "language", "country", "recipient_email"):
                setattr(application, key, getattr(app, key))
            application.updated_at = timestamp
            session.flush()
            for raw in reviews:
                values = normalized(raw)
                existing = session.scalar(select(Review).where(Review.application_id == application.id, Review.review_id == values['review_id']))
                if existing is None:
                    if values['score'] > app.max_score:
                        counts['ignored'] += 1
                        continue
                    session.add(Review(application_id=application.id, first_seen_at=timestamp, last_seen_at=timestamp, **values))
                    session.flush()
                    counts['new'] += 1
                else:
                    changed = existing.content_hash != values['content_hash']
                    if changed:
                        for key, value in values.items():
                            setattr(existing, key, value)
                        existing.analysis_status = "pending"
                    existing.last_seen_at = timestamp
                    counts['modified' if changed else 'unchanged'] += 1
        return counts

    def export_rows(self) -> list[tuple[int, str]]:
        with Session(self.engine) as session:
            return [(r.score, r.content) for r in session.scalars(select(Review).order_by(Review.id))]


def migrate_legacy(source: Path, destination: Path, app: AppConfig) -> int:
    """Import sans altérer la source ni écraser une destination existante."""
    if destination.exists() or source.resolve() == destination.resolve():
        raise ValueError("La destination doit être une nouvelle base")
    with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as db:
        rows = db.execute('SELECT reviewId, score, content, at, inserted_at FROM reviews').fetchall()
    # Valider avant de créer la destination pour éviter une migration partielle.
    for row in rows:
        normalized(dict(reviewId=row[0], score=row[1], content=row[2], at=row[3]))
    repository = Repository(destination)
    try:
        repository.sync(app.model_copy(update={"max_score": 5}), [dict(reviewId=r[0], score=r[1], content=r[2], at=r[3]) for r in rows])
        with Session(repository.engine) as session, session.begin():
            for rid, _, _, _, inserted in rows:
                review = session.scalar(select(Review).where(Review.review_id == rid))
                if review is not None and inserted:
                    review.first_seen_at = inserted
    finally:
        repository.close()
    return len(rows)

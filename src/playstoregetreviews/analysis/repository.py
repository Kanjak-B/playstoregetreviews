"""Historique d'analyse et réservation transactionnelle des versions d'avis."""
import json
from sqlalchemy import select, update, text
from sqlalchemy.orm import Session
from ..config import AppConfig
from ..database.models import Analysis, AnalysisReview, Application, Review
from ..database.repository import Repository, now
from .preparation import SourceReview, PROMPT_VERSION
from .providers.openai import ProviderResponse


class AnalysisBusy(RuntimeError):
    pass


class AnalysisRepository:
    def __init__(self, repository: Repository):
        self.engine = repository.engine

    def candidates(self, app: AppConfig) -> list[SourceReview]:
        with Session(self.engine) as session:
            rows = session.scalars(select(Review).join(Application).where(
                Application.package_name == app.package_name,
                Review.analysis_status == 'pending', Review.score <= app.max_score,
            ).order_by(Review.id))
            return [SourceReview(r.id, r.review_id, r.content_hash, r.score, r.content) for r in rows]

    def reserve(self, app: AppConfig, sources: list[SourceReview], model: str) -> int | None:
        with Session(self.engine) as session:
            # Sérialiser sélection et réservation, puis libérer avant l'appel réseau.
            session.execute(text('BEGIN IMMEDIATE'))
            application = session.scalar(select(Application).where(Application.package_name == app.package_name))
            if application is None:
                session.rollback()
                return None
            running = session.scalar(select(Analysis.id).where(
                Analysis.application_id == application.id, Analysis.status == 'running'))
            if running is not None:
                raise AnalysisBusy('analysis_already_running')
            # Si un autre processus a terminé le même lot, ne pas le repayer.
            for source in sources:
                row = session.get(Review, source.id)
                if (row is None or row.application_id != application.id or
                        row.content_hash != source.content_hash or row.analysis_status != 'pending'):
                    session.rollback()
                    return None
            analysis = Analysis(application_id=application.id, model_provider='openai', model_name=model,
                                prompt_version=PROMPT_VERSION, status='running', total_reviews=len(sources),
                                created_at=now())
            session.add(analysis)
            session.flush()
            for source in sources:
                session.add(AnalysisReview(analysis_id=analysis.id, review_id=source.id,
                                           content_hash=source.content_hash,
                                           review_date=session.get(Review, source.id).review_date))
            analysis_id = analysis.id
            session.commit()
            return analysis_id

    def finish(self, analysis_id: int, result: dict | None, response: ProviderResponse | None,
               estimated_cost: float | None, error_code: str | None) -> None:
        with Session(self.engine) as session:
            session.execute(text('BEGIN IMMEDIATE'))
            analysis = session.get(Analysis, analysis_id)
            if analysis is None or analysis.status != 'running':
                raise AnalysisBusy('analysis_no_longer_running')
            analysis.status = 'completed' if result is not None else 'failed'
            analysis.result_json = json.dumps(result, ensure_ascii=False) if result else None
            analysis.completed_at = now()
            analysis.error_code = error_code
            analysis.input_tokens = response.input_tokens if response else None
            analysis.output_tokens = response.output_tokens if response else None
            analysis.estimated_cost = estimated_cost
            if result is not None:
                links = session.scalars(select(AnalysisReview).where(AnalysisReview.analysis_id == analysis_id))
                for link in links:
                    # Une collecte concurrente a pu changer le texte : garder sa nouvelle version pending.
                    session.execute(update(Review).where(Review.id == link.review_id,
                        Review.content_hash == link.content_hash).values(analysis_status='analyzed'))
            session.commit()

    def recover(self, analysis_id: int) -> None:
        """Action explicite après arrêt confirmé du processus propriétaire."""
        with Session(self.engine) as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row = session.get(Analysis, analysis_id)
            if row is None or row.status != 'running':
                raise ValueError('Analyse inexistante ou non active')
            row.status = 'failed'
            row.error_code = 'interrupted_usage_unknown'
            row.completed_at = now()
            session.commit()

    def result(self, analysis_id: int) -> dict:
        with Session(self.engine) as session:
            row = session.get(Analysis, analysis_id)
            if row is None:
                raise ValueError('Analyse inexistante')
            return dict(id=row.id, status=row.status, provider=row.model_provider, model=row.model_name,
                        total_reviews=row.total_reviews, created_at=row.created_at,
                        completed_at=row.completed_at, error_code=row.error_code,
                        input_tokens=row.input_tokens, output_tokens=row.output_tokens,
                        estimated_cost=row.estimated_cost,
                        result=json.loads(row.result_json) if row.result_json else None)

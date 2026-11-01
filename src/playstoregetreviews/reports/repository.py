"""Immutable reports, source-version deduplication, and history."""
import hashlib
import json
from datetime import datetime, timezone
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from ..config import AppConfig
from ..database.models import Analysis, AnalysisReview, Application, Report, ReportAnalysis, Review
from ..database.repository import Repository, now
from ..analysis.schemas import AnalysisResult
from .schemas import ReportDocument, ReportIssue, SourceVersion

PRIORITY = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}


def parsed_date(value: str | None) -> datetime | None:
    try:
        date = datetime.fromisoformat(value or '')
        return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)
    except ValueError:
        return None


class ReportRepository:
    def __init__(self, repository: Repository):
        self.engine = repository.engine

    def create(self, app: AppConfig) -> int | None:
        with Session(self.engine) as session:
            session.execute(text('BEGIN IMMEDIATE'))
            analyses = list(session.scalars(select(Analysis).join(Application).where(
                Application.package_name == app.package_name, Analysis.status == 'completed',
                ~Analysis.id.in_(select(ReportAnalysis.analysis_id)),
            ).order_by(Analysis.id)))
            if not analyses:
                return None
            results = {a.id: AnalysisResult.model_validate_json(a.result_json) for a in analyses}
            # Keep only the most recent version of each review in this report.
            selected: dict[str, tuple[int, AnalysisReview]] = {}
            refs: dict[int, list[tuple[str, AnalysisReview]]] = {}
            for analysis in analyses:
                links = session.execute(select(Review.review_id, AnalysisReview).join(
                    AnalysisReview, AnalysisReview.review_id == Review.id).where(
                        AnalysisReview.analysis_id == analysis.id)).all()
                refs[analysis.id] = list(links)
                for rid, link in links:
                    selected[rid] = (analysis.id, link)
            if not selected:
                raise ValueError('Rapport sans sources')
            versions = sorted((rid, link.content_hash) for rid, (_, link) in selected.items())
            fingerprint = hashlib.sha256(json.dumps([app.package_name, versions], ensure_ascii=False).encode()).hexdigest()
            report = session.scalar(select(Report).where(Report.fingerprint == fingerprint))
            if report is None:
                issues: dict[tuple[str, str], dict] = {}
                summaries, limitations, dates = [], [], []
                for analysis in analyses:
                    active = {rid for rid, link in refs[analysis.id]
                              if selected[rid][1].content_hash == link.content_hash}
                    if not active:
                        continue
                    result = results[analysis.id]
                    summaries.append(result.summary)
                    limitations.extend(result.limitations)
                    for issue in result.issues:
                        ids = set(issue.review_ids) & active
                        if not ids:
                            continue
                        key = (issue.category, issue.title.casefold())
                        if key not in issues:
                            issues[key] = dict(issue.model_dump(), review_ids=set(), review_count=0)
                        target = issues[key]
                        target['review_ids'].update(ids)
                        target['recommendations'] = list(dict.fromkeys(target['recommendations'] + issue.recommendations))[:10]
                        target['confidence'] = min(target['confidence'], issue.confidence)
                        if PRIORITY[issue.priority] < PRIORITY[target['priority']]:
                            target['priority'] = issue.priority
                sources = []
                for rid, (_, link) in selected.items():
                    date = parsed_date(link.review_date)
                    if date:
                        dates.append(date)
                    sources.append(SourceVersion(review_id=rid, content_hash=link.content_hash,
                                                  review_date=link.review_date))
                if len(dates) != len(sources):
                    limitations.append('Certaines dates des avis historiques sont inconnues ; la période des avis est partielle.')
                if len(analyses) > 1:
                    limitations.append('Synthèse de lots : seuls les titres et catégories identiques sont regroupés ; les comptes de problèmes peuvent se recouper.')
                verified = []
                for value in issues.values():
                    value['review_ids'] = sorted(value['review_ids'])
                    value['review_count'] = len(value['review_ids'])
                    verified.append(ReportIssue.model_validate(value))
                verified.sort(key=lambda issue: (PRIORITY[issue.priority], -issue.review_count, issue.title))
                def total(field):
                    values = [getattr(a, field) for a in analyses]
                    return None if any(v is None for v in values) else sum(values)
                document = ReportDocument(
                    app_name=app.name, package_name=app.package_name, generated_at=now(),
                    analysis_ids=[a.id for a in analyses],
                    analysis_period_start=min(a.created_at for a in analyses),
                    analysis_period_end=max(a.completed_at or a.created_at for a in analyses),
                    review_period_start=min(dates).isoformat() if dates else None,
                    review_period_end=max(dates).isoformat() if dates else None,
                    reviews_analyzed=len(sources), summaries=list(dict.fromkeys(summaries)),
                    issues=verified, limitations=list(dict.fromkeys(limitations)), sources=sources,
                    models=sorted({f'{a.model_provider}/{a.model_name}' for a in analyses}),
                    input_tokens=total('input_tokens'), output_tokens=total('output_tokens'),
                    estimated_cost=total('estimated_cost'))
                report = Report(application_id=analyses[0].application_id, fingerprint=fingerprint,
                                document_json=document.model_dump_json(), created_at=now())
                session.add(report)
                session.flush()
            for analysis in analyses:
                session.add(ReportAnalysis(report_id=report.id, analysis_id=analysis.id))
            report_id = report.id
            session.commit()
            return report_id

    def get(self, report_id: int) -> ReportDocument:
        with Session(self.engine) as session:
            report = session.get(Report, report_id)
            if report is None:
                raise ValueError('Rapport inexistant')
            return ReportDocument.model_validate_json(report.document_json)

    def ids(self, package: str, unrendered: bool = False) -> list[int]:
        with Session(self.engine) as session:
            statement = select(Report.id).join(Application).where(Application.package_name == package)
            if unrendered:
                statement = statement.where(Report.rendered_at.is_(None))
            return list(session.scalars(statement.order_by(Report.id)))

    def rendered(self, report_id: int) -> None:
        with Session(self.engine) as session, session.begin():
            report = session.get(Report, report_id)
            if report is None:
                raise ValueError('Rapport inexistant')
            report.rendered_at = now()

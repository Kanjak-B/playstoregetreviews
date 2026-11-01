"""Pipeline incrémental, cadence par application et réservations persistantes."""
from datetime import datetime, timedelta, timezone
import logging
import socket
from typing import Callable
from sqlalchemy import text
from sqlalchemy.orm import Session
from .config import AppConfig, Settings
from .database.models import WorkflowState
from .database.repository import Repository, now
from .analysis.repository import AnalysisRepository
from .analysis.providers.openai import OpenAIProvider, Provider
from .analysis.service import analyze_app
from .collector.google_play import fetch_reviews
from .reports.repository import ReportRepository
from .reports.generator import generate_report
from .notifications.repository import DeliveryRepository
from .notifications.email import send_report, MailTransport

logger = logging.getLogger(__name__)


class WorkflowRepository:
    def __init__(self, repository: Repository):
        self.engine = repository.engine

    def reserve(self, app: AppConfig, force: bool = False) -> str:
        with Session(self.engine) as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row = session.get(WorkflowState, app.package_name)
            if row is not None:
                if row.status == 'running':
                    return 'busy'
                if not force and row.status == 'completed' and row.last_success_at:
                    due = datetime.fromisoformat(row.last_success_at) + timedelta(minutes=app.interval_minutes)
                    if datetime.now(timezone.utc) < due:
                        return 'not_due'
            else:
                row = WorkflowState(package_name=app.package_name, status='running', started_at=now())
                session.add(row)
            row.status, row.started_at, row.completed_at = 'running', now(), None
            session.commit()
            return 'reserved'

    def finish(self, package: str, success: bool) -> None:
        with Session(self.engine) as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row = session.get(WorkflowState, package)
            if row is None or row.status != 'running':
                raise ValueError('Réservation du pipeline perdue')
            row.status = 'completed' if success else 'failed'
            row.completed_at = now()
            if success:
                row.last_success_at = row.completed_at
            session.commit()

    def recover(self, package: str) -> None:
        with Session(self.engine) as session:
            session.execute(text('BEGIN IMMEDIATE'))
            row = session.get(WorkflowState, package)
            if row is None or row.status != 'running':
                raise ValueError('Pipeline inexistant ou non actif')
            row.status, row.completed_at = 'failed', now()
            session.commit()


def run_pipeline(repository: Repository, settings: Settings, package: str | None = None,
                 force: bool = False, fetch: Callable = fetch_reviews,
                 provider: Provider | None = None, transport: MailTransport | None = None,
                 notify: Callable | None = None) -> bool:
    apps = [app for app in settings.apps if package is None or app.package_name == package]
    if not apps:
        raise ValueError('Application absente de la configuration')
    owned_provider = provider is None
    provider = provider or OpenAIProvider(settings.analysis)
    workflows, analyses = WorkflowRepository(repository), AnalysisRepository(repository)
    reports, deliveries = ReportRepository(repository), DeliveryRepository(repository)
    remaining = settings.analysis.max_batches_per_run
    remaining_emails = settings.email.max_deliveries_per_run
    all_success = True
    previous_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(settings.timeout_seconds)
        for app in apps:
            reservation = workflows.reserve(app, force)
            if reservation != 'reserved':
                logger.info('workflow_' + reservation, extra={'package': app.package_name})
                all_success = all_success and reservation != 'busy'
                continue
            success = False
            try:
                rows = fetch(app, settings)
                counts = dict(new=0, modified=0, unchanged=0, ignored=0)
                for row in rows:
                    result = repository.sync(app, [row])
                    for key in counts:
                        counts[key] += result[key]
                    if result['new'] and notify is not None:
                        notify(row, app.package_name)
                logger.info('collection_completed', extra={'package': app.package_name, 'counts': counts})
                analysis_failed = False
                if remaining:
                    stats = analyze_app(analyses, provider, app,
                                        settings.analysis.model_copy(update={'max_batches_per_run': remaining}))
                    remaining -= stats.calls
                    analysis_failed = bool(stats.failed or stats.deferred)
                elif analyses.candidates(app):
                    analysis_failed = True
                report_id = reports.create(app)
                ids = reports.ids(app.package_name, unrendered=True)
                if report_id is not None and report_id not in ids:
                    ids.append(report_id)
                for rid in ids:
                    generate_report(reports, rid, settings.report_dir)
                    logger.info('report_generated', extra={'package': app.package_name, 'report_id': rid})
                mail_failed = False
                if settings.email.enabled and app.recipient_authorized:
                    for rid in reports.ids(app.package_name):
                        status = deliveries.status(rid, app.recipient_email)
                        if status == 'sent':
                            continue
                        if not deliveries.can_attempt(rid, app.recipient_email, settings.email.max_attempts):
                            mail_failed = True
                            continue
                        if remaining_emails == 0:
                            # Ne pas repousser un backlog d'envois à la prochaine période complète.
                            mail_failed = True
                            break
                        remaining_emails -= 1
                        status = send_report(reports, deliveries, rid, app, settings.email, transport)
                        mail_failed = mail_failed or status != 'sent'
                success = not analysis_failed and not mail_failed
            except Exception:
                # Les exceptions distantes peuvent contenir avis, URLs ou identifiants.
                logger.error('workflow_failed', extra={'package': app.package_name})
            finally:
                workflows.finish(app.package_name, success)
            all_success = all_success and success
            logger.info('workflow_completed' if success else 'workflow_incomplete', extra={'package': app.package_name})
    finally:
        socket.setdefaulttimeout(previous_timeout)
        if owned_provider:
            provider.close()
    return all_success

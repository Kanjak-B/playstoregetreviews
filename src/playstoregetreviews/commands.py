"""Report and notification commands, separate from legacy entry points."""
import json
import logging
from .config import Settings
from .database.repository import Repository
from .reports.generator import generate_report
from .reports.repository import ReportRepository
from .notifications.email import preview_email, send_report
from .notifications.repository import DeliveryRepository
from .workflow import WorkflowRepository

logger = logging.getLogger(__name__)


def execute(command: str, repository: Repository, settings: Settings, *,
            package: str | None = None, report_id: int | None = None,
            delivery_id: int | None = None, outcome: str | None = None) -> int:
    reports, deliveries = ReportRepository(repository), DeliveryRepository(repository)
    if command == 'run-recover':
        if not package:
            raise ValueError('--package requis')
        WorkflowRepository(repository).recover(package)
        logger.info('workflow_recovered', extra={'package': package})
        return 0
    if command in {'email-status', 'email-resolve'}:
        if delivery_id is None:
            raise ValueError('--delivery-id requis')
        if command == 'email-status':
            print(json.dumps(deliveries.get(delivery_id), ensure_ascii=False, indent=2))
        else:
            if outcome not in {'sent', 'retry'}:
                raise ValueError('--outcome sent ou retry requis')
            deliveries.resolve(delivery_id, outcome == 'sent')
            logger.info('email_resolved', extra={'delivery_id': delivery_id})
        return 0
    if command == 'report':
        apps = [app for app in settings.apps if package is None or app.package_name == package]
        if not apps:
            raise ValueError('Application absente de la configuration')
        if report_id is not None:
            document = reports.get(report_id)
            if not any(app.package_name == document.package_name for app in apps):
                raise ValueError('Rapport hors des applications sélectionnées')
            ids = [report_id]
        else:
            ids = []
            for app in apps:
                rid = reports.create(app)
                ids.extend(reports.ids(app.package_name, unrendered=True))
                if rid is not None:
                    ids.append(rid)
        for rid in dict.fromkeys(ids):
            generate_report(reports, rid, settings.report_dir)
            logger.info('report_generated', extra={'report_id': rid})
        return 0
    if report_id is None:
        raise ValueError('--report-id requis')
    document = reports.get(report_id)
    app = next((app for app in settings.apps if app.package_name == document.package_name), None)
    if app is None or package is not None and app.package_name != package:
        raise ValueError('Application du rapport absente de la configuration')
    if command == 'email-preview':
        preview_email(reports, report_id, app, settings.email, settings.report_dir)
        logger.info('email_preview_generated', extra={'report_id': report_id})
        return 0
    if command == 'send-email':
        return int(send_report(reports, deliveries, report_id, app, settings.email) != 'sent')
    raise ValueError('Commande inconnue')

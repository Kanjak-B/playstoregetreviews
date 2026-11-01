"""Encrypted SMTP, offline previews, and failure classification."""
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
from datetime import datetime
import logging
import os
from pathlib import Path
import smtplib
import ssl
from typing import Protocol
from ..analysis.preparation import redact
from ..config import AppConfig, EmailConfig
from ..reports.schemas import ReportDocument
from ..reports.generator import atomic_write, render_html
from ..reports.repository import ReportRepository
from .repository import DeliveryRepository

logger = logging.getLogger(__name__)


class DeliveryError(RuntimeError):
    def __init__(self, uncertain: bool, code: str):
        super().__init__(code)
        self.uncertain = uncertain
        self.code = code


class MailTransport(Protocol):
    def validate(self) -> None: ...
    def send(self, message: EmailMessage) -> None: ...


class SMTPTransport:
    def __init__(self, config: EmailConfig):
        self.config = config

    def validate(self) -> None:
        if not self.config.enabled or not self.config.host or not self.config.from_address:
            raise ValueError('SMTP non activé ou incomplet')
        username, password = os.getenv('PLAYSTORE_SMTP_USERNAME'), os.getenv('PLAYSTORE_SMTP_PASSWORD')
        if self.config.auth_required and (not username or not password):
            raise ValueError('Identifiants SMTP manquants dans les variables sécurisées')
        if bool(username) != bool(password):
            raise ValueError('Fournir les deux identifiants SMTP ou aucun')

    def send(self, message: EmailMessage) -> None:
        self.validate()
        connection = None
        sending = False
        try:
            context = ssl.create_default_context()
            if self.config.security == 'ssl':
                connection = smtplib.SMTP_SSL(self.config.host, self.config.port,
                    timeout=self.config.timeout_seconds, context=context)
                connection.ehlo()
            else:
                connection = smtplib.SMTP(self.config.host, self.config.port, timeout=self.config.timeout_seconds)
                connection.ehlo()
                connection.starttls(context=context)
                connection.ehlo()
            username, password = os.getenv('PLAYSTORE_SMTP_USERNAME'), os.getenv('PLAYSTORE_SMTP_PASSWORD')
            if username and password:
                connection.login(username, password)
            sending = True
            refused = connection.send_message(message)
            if refused:
                # There is only one authorized recipient; partial refusal is not expected.
                raise DeliveryError(False, 'smtp_recipient_refused')
        except DeliveryError:
            raise
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
            raise DeliveryError(False, 'smtp_rejected') from None
        except Exception:
            # A timeout or disconnect during DATA may follow acceptance.
            raise DeliveryError(sending, 'smtp_acceptance_unknown' if sending else 'smtp_connection_failed') from None
        finally:
            if connection is not None:
                try:
                    connection.quit()
                except Exception:
                    try:
                        connection.close()
                    except Exception:
                        pass


def authorized_recipient(app: AppConfig, document: ReportDocument) -> str:
    if app.package_name != document.package_name:
        raise ValueError('Rapport et application différents')
    if not app.recipient_authorized or not app.recipient_email:
        raise ValueError('Destinataire non configuré ou non autorisé')
    return app.recipient_email


def build_message(document: ReportDocument, app: AppConfig, config: EmailConfig,
                  message_id: str) -> EmailMessage:
    recipient = authorized_recipient(app, document)
    if not config.from_address:
        raise ValueError('Expéditeur requis même pour un aperçu')
    message = EmailMessage(policy=SMTP)
    message['From'] = config.from_address
    message['To'] = recipient
    message['Subject'] = f'Analyse des avis — {redact(document.app_name)}'
    message['Message-ID'] = message_id
    message['Date'] = format_datetime(datetime.fromisoformat(document.generated_at))
    plain = f'{document.app_name} — {document.reviews_analyzed} avis\n\n'
    plain += '\n\n'.join(document.summaries[:3])
    for issue in document.issues[:5]:
        plain += f'\n\n[{issue.priority}] {issue.title} ({issue.review_count} avis)\n'
        plain += '\n'.join(f'- {value}' for value in issue.recommendations[:3])
    plain += '\n\nRapport complet joint. Causes techniques à vérifier ; voir les limites.'
    message.set_content(plain)
    message.add_alternative(render_html(document, 'email.html'), subtype='html')
    message.add_attachment(document.model_dump_json(indent=2).encode(), maintype='application',
                           subtype='json', filename='rapport.json')
    message.add_attachment(render_html(document).encode(), maintype='text', subtype='html', filename='rapport.html')
    return message


def preview_email(reports: ReportRepository, report_id: int, app: AppConfig,
                  config: EmailConfig, directory: Path) -> tuple[Path, Path]:
    document = reports.get(report_id)
    message = build_message(document, app, config, f'<preview-{report_id}@example.invalid>')
    eml, html = directory / f'email-preview-{report_id}.eml', directory / f'email-preview-{report_id}.html'
    atomic_write(eml, message.as_bytes())
    atomic_write(html, render_html(document, 'email.html').encode())
    return eml, html


def send_report(reports: ReportRepository, deliveries: DeliveryRepository, report_id: int,
                app: AppConfig, config: EmailConfig, transport: MailTransport | None = None) -> str:
    if not config.enabled:
        raise ValueError('Envoi SMTP désactivé')
    document = reports.get(report_id)
    recipient = authorized_recipient(app, document)
    existing_status = deliveries.status(report_id, recipient)
    if not deliveries.can_attempt(report_id, recipient, config.max_attempts):
        return existing_status or 'skipped'
    # Prepare and validate before reserving a delivery, without network access.
    build_message(document, app, config, '<validation@example.invalid>')
    if transport is None:
        if config.provider == 'resend':
            from .resend import ResendTransport
            transport = ResendTransport(config)
        else:
            transport = SMTPTransport(config)
    transport.validate()
    reservation = deliveries.reserve(report_id, recipient, config.from_address, config.max_attempts)
    if reservation is None:
        return deliveries.status(report_id, recipient) or 'skipped'
    delivery_id, message_id = reservation
    message = build_message(document, app, config, message_id)
    logger.info('email_started', extra={'report_id': report_id, 'delivery_id': delivery_id})
    try:
        transport.send(message)
    except DeliveryError as exc:
        status = 'unknown' if exc.uncertain else 'failed'
        prefix = config.provider
        code = f'{prefix}_acceptance_unknown' if exc.uncertain else f'{prefix}_failed'
        deliveries.finish(delivery_id, status, code)
        logger.error('email_failed', extra={'report_id': report_id, 'delivery_id': delivery_id})
        return status
    except Exception:
        deliveries.finish(delivery_id, 'unknown', f'{config.provider}_acceptance_unknown')
        logger.error('email_unknown', extra={'report_id': report_id, 'delivery_id': delivery_id})
        return 'unknown'
    deliveries.finish(delivery_id, 'sent')
    logger.info('email_sent', extra={'report_id': report_id, 'delivery_id': delivery_id})
    return 'sent'

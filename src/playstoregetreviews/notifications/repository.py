"""Réservations et statuts d'envoi, sans réessai des acceptations incertaines."""
import hashlib
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from ..database.models import EmailDelivery, Report
from ..database.repository import Repository, now


class DeliveryRepository:
    def __init__(self, repository: Repository):
        self.engine = repository.engine

    def reserve(self, report_id: int, recipient: str, sender: str, max_attempts: int) -> tuple[int, str] | None:
        with Session(self.engine) as session:
            session.execute(text('BEGIN IMMEDIATE'))
            report = session.get(Report, report_id)
            if report is None:
                raise ValueError('Rapport inexistant')
            delivery = session.scalar(select(EmailDelivery).where(
                EmailDelivery.report_id == report_id, EmailDelivery.recipient == recipient))
            if delivery is not None and (delivery.status in {'sent', 'sending', 'unknown'} or delivery.attempts >= max_attempts):
                return None
            if delivery is None:
                identity = hashlib.sha256(f'{report.fingerprint}:{recipient}'.encode()).hexdigest()[:40]
                delivery = EmailDelivery(report_id=report_id, recipient=recipient, status='sending',
                    message_id=f'<playstore-{identity}@{sender.split("@")[1]}>',
                    created_at=now(), updated_at=now(), attempts=0)
                session.add(delivery)
                session.flush()
            delivery.status = 'sending'
            delivery.attempts += 1
            delivery.updated_at = now()
            delivery.error_code = None
            delivery_id, message_id = delivery.id, delivery.message_id
            session.commit()
            return delivery_id, message_id

    def finish(self, delivery_id: int, status: str, error_code: str | None = None) -> None:
        if status not in {'sent', 'failed', 'unknown'}:
            raise ValueError('Statut invalide')
        with Session(self.engine) as session:
            session.execute(text('BEGIN IMMEDIATE'))
            delivery = session.get(EmailDelivery, delivery_id)
            if delivery is None or delivery.status != 'sending':
                raise ValueError('Réservation email perdue')
            delivery.status = status
            delivery.error_code = error_code
            delivery.updated_at = now()
            if status == 'sent':
                delivery.sent_at = now()
            session.commit()

    def resolve(self, delivery_id: int, delivered: bool) -> None:
        with Session(self.engine) as session:
            session.execute(text('BEGIN IMMEDIATE'))
            delivery = session.get(EmailDelivery, delivery_id)
            if delivery is None or delivery.status not in {'sending', 'unknown'}:
                raise ValueError('Seul un envoi incertain peut être résolu')
            delivery.status = 'sent' if delivered else 'failed'
            delivery.sent_at = now() if delivered else None
            delivery.updated_at = now()
            delivery.error_code = 'manually_confirmed_sent' if delivered else 'manually_confirmed_not_sent'
            session.commit()

    def status(self, report_id: int, recipient: str) -> str | None:
        with Session(self.engine) as session:
            return session.scalar(select(EmailDelivery.status).where(
                EmailDelivery.report_id == report_id, EmailDelivery.recipient == recipient))

    def can_attempt(self, report_id: int, recipient: str, max_attempts: int) -> bool:
        with Session(self.engine) as session:
            row = session.scalar(select(EmailDelivery).where(
                EmailDelivery.report_id == report_id, EmailDelivery.recipient == recipient))
            return row is None or (row.status == 'failed' and row.attempts < max_attempts)

    def get(self, delivery_id: int) -> dict:
        with Session(self.engine) as session:
            row = session.get(EmailDelivery, delivery_id)
            if row is None:
                raise ValueError('Envoi inexistant')
            return dict(id=row.id, report_id=row.report_id, recipient=row.recipient, status=row.status,
                        attempts=row.attempts, message_id=row.message_id, sent_at=row.sent_at,
                        error_code=row.error_code)

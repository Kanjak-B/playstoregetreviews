"""Resend HTTPS transport sharing the existing delivery history and previews."""
import base64
from email.message import EmailMessage
import hashlib
import os
import requests
from ..config import EmailConfig
from .email import DeliveryError


class ResendTransport:
    def __init__(self, config: EmailConfig, client=None):
        self.config = config
        self.client = client or requests

    def validate(self) -> None:
        if not self.config.enabled or not self.config.from_address:
            raise ValueError('Resend non activé ou expéditeur manquant')
        if not os.getenv('RESEND_API_KEY'):
            raise ValueError('Clé RESEND_API_KEY manquante')

    def send(self, message: EmailMessage) -> None:
        self.validate()
        plain = message.get_body(preferencelist=('plain',))
        html = message.get_body(preferencelist=('html',))
        payload = {
            'from': str(message['From']),
            'to': [str(message['To'])],
            'subject': str(message['Subject']),
            'text': plain.get_content() if plain else '',
            'html': html.get_content() if html else '',
            'attachments': [
                {'filename': part.get_filename(),
                 'content': base64.b64encode(part.get_payload(decode=True)).decode('ascii')}
                for part in message.iter_attachments()
            ],
        }
        identity = hashlib.sha256(str(message['Message-ID']).encode()).hexdigest()
        try:
            response = self.client.post(
                'https://api.resend.com/emails', json=payload,
                headers={'Authorization': f'Bearer {os.environ["RESEND_API_KEY"]}',
                         'Idempotency-Key': f'playstore-{identity}'},
                timeout=self.config.timeout_seconds, allow_redirects=False,
            )
        except requests.RequestException:
            raise DeliveryError(True, 'resend_acceptance_unknown') from None
        if 200 <= response.status_code < 300:
            try:
                accepted = response.json().get('id')
            except (ValueError, AttributeError):
                accepted = None
            if isinstance(accepted, str) and accepted:
                return
            raise DeliveryError(True, 'resend_acceptance_unknown')
        # A server failure or concurrent idempotent request may follow acceptance.
        uncertain = response.status_code >= 500 or response.status_code == 409
        raise DeliveryError(uncertain, 'resend_rejected')

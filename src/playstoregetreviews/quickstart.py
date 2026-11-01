"""Interactive name-to-report workflow with private, reusable local settings."""
from dataclasses import replace
from getpass import getpass
import os
import logging
from pathlib import Path
from dotenv import load_dotenv, set_key
from .collector.discovery import StoreApp, discover
from .config import AppConfig, AnalysisConfig, EmailConfig, Settings, load_settings, validate_email
from .database.repository import Repository
from .reports.generator import atomic_write
from .reports.repository import ReportRepository
from .workflow import run_pipeline


PROGRESS = {
    'collection_completed': 'Collecte terminée.',
    'analysis_started': 'Analyse IA en cours…',
    'analysis_completed': 'Analyse terminée.',
    'analysis_failed': 'Analyse échouée. Vérifiez la clé OpenAI et l’accès au modèle.',
    'analysis_busy': 'Une analyse est déjà en cours pour cette application.',
    'report_generated': 'Rapports HTML et JSON enregistrés.',
    'email_started': 'Envoi du rapport en cours…',
    'email_sent': 'Rapport accepté par le service email.',
    'email_failed': 'Envoi échoué. Vérifiez votre configuration et votre compte email.',
    'email_unknown': 'Envoi incertain : aucun nouvel envoi automatique ne sera tenté.',
    'workflow_failed': 'Impossible de terminer : vérifiez les accès et la configuration.',
    'workflow_busy': 'Un traitement est déjà en cours pour cette application.',
}


class ProgressFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return PROGRESS.get(record.getMessage(), record.getMessage())


def choose_app(results: list[StoreApp]) -> StoreApp:
    if not results:
        raise ValueError('Aucune application trouvée. Essayez son lien Google Play exact.')
    if len(results) == 1:
        return results[0]
    print('Plusieurs applications correspondent :')
    for index, app in enumerate(results, 1):
        print(f'{index}. {app.title} — {app.developer} ({app.package_name})')
    while True:
        answer = input('Numéro de l’application : ').strip()
        if answer.isdecimal() and 1 <= int(answer) <= len(results):
            return results[int(answer) - 1]
        print('Choisissez un numéro de la liste.')


def secret(name: str, label: str, env_path: Path) -> None:
    if os.getenv(name):
        return
    value = getpass(f'{label} (enregistrée localement dans .env) : ').strip()
    if not value:
        raise ValueError(f'{label} requise pour continuer')
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    set_key(str(env_path), name, value)
    env_path.chmod(0o600)
    os.environ[name] = value


def configure_email(settings: Settings, app: AppConfig, env_path: Path) -> tuple[Settings, AppConfig]:
    secret('RESEND_API_KEY', 'Clé API Resend', env_path)
    recipient = input(f'Votre email destinataire [{app.recipient_email or ""}] : ').strip() or app.recipient_email
    recipient = validate_email(recipient or '')
    sender_default = settings.email.from_address or 'onboarding@resend.dev'
    sender = input(f'Expéditeur Resend [{sender_default}] : ').strip() or sender_default
    sender = validate_email(sender)
    print('Le domaine de l’expéditeur doit être vérifié dans Resend. '
          'onboarding@resend.dev est réservé aux tests autorisés par Resend.')
    return (settings.model_copy(update={'email': settings.email.model_copy(update={
        'provider': 'resend', 'enabled': True, 'from_address': sender})}),
        app.model_copy(update={'recipient_email': recipient, 'recipient_authorized': True}))


def start(query: str | None, filename: str | None = None, email: bool = False) -> int:
    path = Path(filename or os.getenv('PLAYSTORE_CONFIG') or 'config.local.json').resolve()
    env_path = path.parent / '.env'
    load_dotenv(env_path, override=False)
    settings = load_settings(str(path)) if path.exists() else Settings(
        db_path=path.parent/'data'/'reviews.db', report_dir=path.parent/'reports',
        export_dir=path.parent/'exports',
        analysis=AnalysisConfig(max_reviews_per_batch=20, max_batches_per_run=1),
        email=EmailConfig(provider='resend'))
    query = query or input('Nom de l’application ou lien Google Play : ')
    print('Recherche sur Google Play…')
    results = discover(query, timeout=settings.timeout_seconds)
    selected = choose_app(results)
    # Fetch the selected application's short description for the analysis prompt.
    details = discover(f'https://play.google.com/store/apps/details?id={selected.package_name}',
                       timeout=settings.timeout_seconds)
    if details:
        selected = replace(selected, title=details[0].title,
                           description=details[0].description)
    print(f'Application : {selected.title} ({selected.package_name})')
    previous = next((app for app in settings.apps if app.package_name == selected.package_name), None) if path.exists() else None
    app = previous or AppConfig(package_name=selected.package_name, name=selected.title,
                                description=selected.description, max_fetch=20)
    if not (os.getenv('PLAYSTORE_LLM_API_KEY') or os.getenv('OPENAI_API_KEY')):
        secret('PLAYSTORE_LLM_API_KEY', 'Clé API OpenAI', env_path)
    want_email = email
    if previous is None and not want_email:
        want_email = input('Recevoir aussi le rapport par Resend ? [o/N] : ').strip().lower() in {'o', 'oui', 'y', 'yes'}
    if want_email:
        settings, app = configure_email(settings, app, env_path)
    elif settings.email.enabled and settings.email.provider == 'resend' and app.recipient_authorized:
        secret('RESEND_API_KEY', 'Clé API Resend', env_path)
    apps = [item for item in settings.apps if item.package_name != app.package_name] if path.exists() else []
    settings = settings.model_copy(update={'apps': [*apps, app]})
    atomic_write(path, settings.model_dump_json(indent=2).encode())
    print(f'Configuration enregistrée. Collecte de {app.max_fetch} avis maximum, analyse et rapport en cours…')
    repository = Repository(settings.db_path)
    try:
        success = run_pipeline(repository, settings, package=app.package_name, force=True)
        reports = ReportRepository(repository).ids(app.package_name)
        if reports:
            print(f'Dernier rapport disponible : {settings.report_dir / f"report-{reports[-1]}.html"}')
        else:
            print('Aucun rapport généré : aucun avis négatif exploitable ou analyse incomplète.')
        if not success:
            print('Traitement partiel : certains avis ou envois restent en attente. '
                  'Consultez les messages ci-dessus avant de relancer.')
        return int(not success)
    finally:
        repository.close()

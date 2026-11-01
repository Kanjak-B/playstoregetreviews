"""Phases 3–5: reports, messages, and the pipeline using synthetic data."""
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock
import json
import smtplib
import ssl
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from playstoregetreviews.config import AppConfig, EmailConfig, Settings, AnalysisConfig
from playstoregetreviews.database.models import AnalysisReview, EmailDelivery, Report, WorkflowState
from playstoregetreviews.database.repository import Repository
from playstoregetreviews.analysis.repository import AnalysisRepository
from playstoregetreviews.analysis.service import analyze_app
from playstoregetreviews.analysis.providers.openai import ProviderResponse, ProviderError
from playstoregetreviews.reports.repository import ReportRepository
from playstoregetreviews.reports.generator import generate_report
from playstoregetreviews.notifications.email import (
    build_message, preview_email, send_report, SMTPTransport, DeliveryError)
from playstoregetreviews.notifications.repository import DeliveryRepository
from playstoregetreviews.workflow import run_pipeline, WorkflowRepository
from playstoregetreviews.main import main


def raw(rid='one', **changes):
    return dict(dict(reviewId=rid, score=1, content='Le démarrage est lent', at='2026-01-02T00:00:00+00:00'), **changes)


def response(refs=None, title='Démarrage lent', summary='Lenteur signalée.'):
    return ProviderResponse(json.dumps(dict(summary=summary, issues=[dict(category='performance', title=title,
        priority='high', review_ids=refs or ['r1'], recommendations=['Mesurer le démarrage'], confidence=0.8)],
        limitations=['Cause technique non confirmée'])), input_tokens=100, output_tokens=50)


@pytest.fixture
def store(tmp_path):
    repo = Repository(tmp_path/'reviews.db')
    yield repo
    repo.close()


@pytest.fixture
def app():
    return AppConfig(package_name='example.app', name='Application de test',
                     recipient_email='tester@example.com', recipient_authorized=True)


@pytest.fixture
def email_config():
    return EmailConfig(enabled=True, host='smtp.example.invalid', from_address='reports@example.com')


def analyze(repo, app, rows=None, answer=None):
    repo.sync(app, rows or [raw()])
    provider = Mock(analyze=Mock(return_value=answer or response()))
    assert analyze_app(AnalysisRepository(repo), provider, app, AnalysisConfig()).completed == 1


def report(repo, app):
    analyze(repo, app)
    return ReportRepository(repo).create(app)


def test_report_files_are_valid_escaped_private_and_repeatable(store, app, tmp_path):
    app = app.model_copy(update={'name': '<img src=x onerror=alert(1)>'})
    analyze(store, app, answer=response(title='<script>alert(1)</script>', summary='<b>Test</b>'))
    reports = ReportRepository(store)
    rid = reports.create(app)
    paths = generate_report(reports, rid, tmp_path/'reports')
    data = json.loads(paths[0].read_text())
    assert data['reviews_analyzed'] == 1
    assert data['issues'][0]['review_count'] == 1
    assert data['review_period_start'] == '2026-01-02T00:00:00+00:00'
    html = paths[1].read_text()
    assert '<script>alert' not in html and '&lt;script&gt;' in html
    assert '<img src=x' not in html and '&lt;img' in html
    assert '&lt;b&gt;Test&lt;/b&gt;' in html
    assert paths[0].stat().st_mode & 0o777 == 0o600
    assert paths[1].stat().st_mode & 0o777 == 0o600
    assert reports.create(app) is None
    original = paths[0].read_bytes()
    generate_report(reports, rid, tmp_path/'reports')
    assert paths[0].read_bytes() == original


def test_multiple_batches_aggregate_distinct_sources(store, app):
    store.sync(app, [raw('one'), raw('two')])
    stats = analyze_app(AnalysisRepository(store), Mock(analyze=Mock(return_value=response())), app,
                        AnalysisConfig(max_reviews_per_batch=1))
    assert stats.completed == 2
    reports = ReportRepository(store)
    document = reports.get(reports.create(app))
    assert document.reviews_analyzed == 2
    assert len(document.issues) == 1
    assert document.issues[0].review_count == 2
    assert document.issues[0].review_ids == ['one', 'two']
    assert document.input_tokens == 200 and document.output_tokens == 100
    assert document.estimated_cost is None


def test_latest_version_in_one_report_counts_review_once(store, app):
    analyze(store, app, answer=response(title='Ancien problème'))
    analyze(store, app, rows=[raw(content='Application figée')], answer=response(title='Blocage'))
    reports = ReportRepository(store)
    document = reports.get(reports.create(app))
    assert document.reviews_analyzed == 1
    assert [issue.title for issue in document.issues] == ['Blocage']
    assert document.input_tokens == 200


def test_same_versions_do_not_produce_identical_new_report(store, app):
    first = report(store, app)
    analyze(store, app, rows=[raw(content='Un autre problème')])
    reports = ReportRepository(store)
    second = reports.create(app)
    assert second != first
    analyze(store, app, rows=[raw()])
    assert reports.create(app) == first
    assert reports.ids(app.package_name) == [first, second]


def test_empty_failed_and_other_app_analyses_do_not_make_reports(store, app):
    reports = ReportRepository(store)
    assert reports.create(app) is None
    store.sync(app, [raw()])
    analyze_app(AnalysisRepository(store), Mock(analyze=Mock(side_effect=ProviderError('fail'))), app, AnalysisConfig())
    assert reports.create(app) is None
    other = app.model_copy(update={'package_name': 'other.app'})
    analyze(store, other)
    assert reports.create(app) is None
    assert reports.create(other) is not None


def test_unknown_legacy_dates_are_explicit(store, app):
    analyze(store, app)
    with Session(store.engine) as session, session.begin():
        session.scalar(select(AnalysisReview)).review_date = None
    reports = ReportRepository(store)
    document = reports.get(reports.create(app))
    assert document.review_period_start is None
    assert any('inconnues' in limitation for limitation in document.limitations)


def test_report_retained_after_disk_failure_and_retry(store, app, tmp_path, monkeypatch):
    reports = ReportRepository(store)
    rid = report(store, app)
    monkeypatch.setattr('playstoregetreviews.reports.generator.atomic_write', Mock(side_effect=OSError('disk')))
    with pytest.raises(OSError):
        generate_report(reports, rid, tmp_path)
    assert reports.ids(app.package_name, unrendered=True) == [rid]
    assert reports.create(app) is None


def test_preview_builds_multipart_without_reserving_or_sending(store, app, email_config, tmp_path):
    rid = report(store, app)
    paths = preview_email(ReportRepository(store), rid, app, email_config, tmp_path/'reports')
    message = BytesParser(policy=policy.default).parsebytes(paths[0].read_bytes())
    assert message['To'] == 'tester@example.com'
    assert message['From'] == 'reports@example.com'
    assert message.get_body(preferencelist=('plain',)) is not None
    assert message.get_body(preferencelist=('html',)) is not None
    assert {part.get_filename() for part in message.iter_attachments()} == {'rapport.json', 'rapport.html'}
    assert DeliveryRepository(store).status(rid, app.recipient_email) is None
    assert paths[0].stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('changes', [dict(recipient_authorized=False), dict(package_name='wrong.app')])
def test_unauthorized_recipient_never_sent(store, app, email_config, changes):
    rid = report(store, app)
    transport = Mock()
    with pytest.raises(ValueError):
        send_report(ReportRepository(store), DeliveryRepository(store), rid,
                    app.model_copy(update=changes), email_config, transport)
    transport.send.assert_not_called()


def test_disabled_mail_cannot_send(store, app, email_config):
    rid = report(store, app)
    with pytest.raises(ValueError):
        send_report(ReportRepository(store), DeliveryRepository(store), rid, app,
                    email_config.model_copy(update={'enabled': False}), Mock())


def test_sent_email_is_not_resent(store, app, email_config):
    rid = report(store, app)
    reports, deliveries, transport = ReportRepository(store), DeliveryRepository(store), Mock()
    assert send_report(reports, deliveries, rid, app, email_config, transport) == 'sent'
    assert send_report(reports, deliveries, rid, app, email_config, transport) == 'sent'
    transport.send.assert_called_once()
    saved = deliveries.get(1)
    assert saved['attempts'] == 1 and saved['sent_at'] is not None
    assert transport.send.call_args.args[0]['Message-ID'] == saved['message_id']


def test_known_failure_retries_same_message_id_and_is_bounded(store, app, email_config):
    rid = report(store, app)
    reports, deliveries = ReportRepository(store), DeliveryRepository(store)
    transport = Mock(send=Mock(side_effect=DeliveryError(False, 'SECRET')))
    for _ in range(5):
        assert send_report(reports, deliveries, rid, app, email_config, transport) == 'failed'
    assert transport.send.call_count == 3
    assert len({str(call.args[0]['Message-ID']) for call in transport.send.call_args_list}) == 1
    assert deliveries.get(1)['error_code'] == 'smtp_failed'


def test_safe_failure_then_success(store, app, email_config):
    rid = report(store, app)
    transport = Mock(send=Mock(side_effect=[DeliveryError(False, 'safe'), None]))
    reports, deliveries = ReportRepository(store), DeliveryRepository(store)
    assert send_report(reports, deliveries, rid, app, email_config, transport) == 'failed'
    assert send_report(reports, deliveries, rid, app, email_config, transport) == 'sent'
    assert deliveries.get(1)['attempts'] == 2


@pytest.mark.parametrize('error', [DeliveryError(True, 'SECRET'), OSError('SECRET')])
def test_uncertain_sending_is_not_automatically_retried(store, app, email_config, error, caplog):
    rid = report(store, app)
    reports, deliveries = ReportRepository(store), DeliveryRepository(store)
    transport = Mock(send=Mock(side_effect=error))
    assert send_report(reports, deliveries, rid, app, email_config, transport) == 'unknown'
    assert send_report(reports, deliveries, rid, app, email_config, transport) == 'unknown'
    assert transport.send.call_count == 1
    assert 'SECRET' not in json.dumps(deliveries.get(1)) + caplog.text
    deliveries.resolve(1, True)
    assert send_report(reports, deliveries, rid, app, email_config, transport) == 'sent'
    with pytest.raises(ValueError):
        deliveries.resolve(1, False)


def test_reservation_blocks_concurrent_and_crashed_sender(store, app, email_config):
    rid = report(store, app)
    deliveries = DeliveryRepository(store)
    assert deliveries.reserve(rid, app.recipient_email, email_config.from_address, 3)
    transport = Mock()
    assert send_report(ReportRepository(store), deliveries, rid, app, email_config, transport) == 'sending'
    transport.send.assert_not_called()
    deliveries.resolve(1, False)
    assert send_report(ReportRepository(store), deliveries, rid, app, email_config, transport) == 'sent'


def test_smtp_starttls_before_auth_and_data(store, app, email_config, monkeypatch):
    rid = report(store, app)
    connection = Mock()
    connection.send_message.return_value = {}
    factory = Mock(return_value=connection)
    monkeypatch.setattr(smtplib, 'SMTP', factory)
    monkeypatch.setenv('PLAYSTORE_SMTP_USERNAME', 'synthetic-username')
    monkeypatch.setenv('PLAYSTORE_SMTP_PASSWORD', 'synthetic-password')
    transport = SMTPTransport(email_config)
    send_report(ReportRepository(store), DeliveryRepository(store), rid, app, email_config, transport)
    names = [call[0] for call in connection.method_calls]
    assert names == ['ehlo', 'starttls', 'ehlo', 'login', 'send_message', 'quit']
    context = connection.starttls.call_args.kwargs['context']
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


def test_smtp_ssl_and_successful_delivery_survives_quit_failure(store, app, email_config, monkeypatch):
    rid = report(store, app)
    config = email_config.model_copy(update={'security': 'ssl', 'port': 465, 'auth_required': False})
    monkeypatch.delenv('PLAYSTORE_SMTP_USERNAME', raising=False)
    monkeypatch.delenv('PLAYSTORE_SMTP_PASSWORD', raising=False)
    connection = Mock()
    connection.send_message.return_value = {}
    connection.quit.side_effect = OSError('quit failure')
    factory = Mock(return_value=connection)
    monkeypatch.setattr(smtplib, 'SMTP_SSL', factory)
    assert send_report(ReportRepository(store), DeliveryRepository(store), rid, app, config, SMTPTransport(config)) == 'sent'
    assert factory.call_args.kwargs['context'].verify_mode == ssl.CERT_REQUIRED
    connection.login.assert_not_called()


@pytest.mark.parametrize('stage,error,uncertain', [
    ('starttls', OSError('SECRET'), False),
    ('send_message', smtplib.SMTPRecipientsRefused({'synthetic': (550, b'no')}), False),
    ('send_message', smtplib.SMTPDataError(550, b'no'), False),
    ('send_message', smtplib.SMTPServerDisconnected('SECRET'), True),
])
def test_smtp_error_classification(store, app, email_config, monkeypatch, stage, error, uncertain):
    rid = report(store, app)
    config = email_config.model_copy(update={'auth_required': False})
    connection = Mock()
    connection.send_message.return_value = {}
    getattr(connection, stage).side_effect = error
    monkeypatch.setattr(smtplib, 'SMTP', Mock(return_value=connection))
    monkeypatch.delenv('PLAYSTORE_SMTP_USERNAME', raising=False)
    monkeypatch.delenv('PLAYSTORE_SMTP_PASSWORD', raising=False)
    message = build_message(ReportRepository(store).get(rid), app, config, '<test@example.com>')
    with pytest.raises(DeliveryError) as caught:
        SMTPTransport(config).send(message)
    assert caught.value.uncertain == uncertain
    assert 'SECRET' not in str(caught.value)


def test_smtp_credentials_missing_before_reservation(store, app, email_config, monkeypatch):
    rid = report(store, app)
    monkeypatch.delenv('PLAYSTORE_SMTP_USERNAME', raising=False)
    monkeypatch.delenv('PLAYSTORE_SMTP_PASSWORD', raising=False)
    with pytest.raises(ValueError):
        send_report(ReportRepository(store), DeliveryRepository(store), rid, app, email_config)
    assert DeliveryRepository(store).status(rid, app.recipient_email) is None


@pytest.mark.parametrize('address', ['bad', 'a@example.com\nBcc:x@example.com', 'Name <a@example.com>'])
def test_email_header_injection_rejected(address):
    with pytest.raises(ValueError):
        AppConfig(recipient_email=address)
    with pytest.raises(ValueError):
        EmailConfig(from_address=address)


def test_mail_configuration_requires_consent_and_secure_transport():
    with pytest.raises(ValueError):
        AppConfig(recipient_authorized=True)
    with pytest.raises(ValueError):
        EmailConfig(enabled=True)
    with pytest.raises(ValueError):
        EmailConfig(security='plain')


def test_end_to_end_pipeline_is_incremental_scheduled_and_deduplicated(store, app, email_config, tmp_path):
    settings = Settings(apps=[app], email=email_config, report_dir=tmp_path/'reports')
    provider = Mock(analyze=Mock(return_value=response()))
    transport = Mock()
    fetch = Mock(return_value=[raw()])
    assert run_pipeline(store, settings, fetch=fetch, provider=provider, transport=transport)
    assert provider.analyze.call_count == 1 and transport.send.call_count == 1
    assert len(list(settings.report_dir.glob('report-*'))) == 2
    assert run_pipeline(store, settings, fetch=fetch, provider=provider, transport=transport)
    assert fetch.call_count == 1
    assert run_pipeline(store, settings, force=True, fetch=fetch, provider=provider, transport=transport)
    assert fetch.call_count == 2 and provider.analyze.call_count == 1 and transport.send.call_count == 1
    fetch.return_value = [raw(content='Encore plus lent')]
    assert run_pipeline(store, settings, force=True, fetch=fetch, provider=provider, transport=transport)
    assert provider.analyze.call_count == 2 and transport.send.call_count == 2


def test_pipeline_retry_mail_without_reanalyzing(store, app, email_config, tmp_path):
    settings = Settings(apps=[app], email=email_config, report_dir=tmp_path/'reports')
    provider = Mock(analyze=Mock(return_value=response()))
    transport = Mock(send=Mock(side_effect=[DeliveryError(False, 'safe'), None]))
    fetch = Mock(return_value=[raw()])
    assert not run_pipeline(store, settings, fetch=fetch, provider=provider, transport=transport)
    assert run_pipeline(store, settings, fetch=fetch, provider=provider, transport=transport)
    assert provider.analyze.call_count == 1 and transport.send.call_count == 2


def test_pipeline_collection_failure_no_model_or_mail(store, app, email_config):
    provider, transport = Mock(), Mock()
    assert not run_pipeline(store, Settings(apps=[app], email=email_config), fetch=Mock(side_effect=RuntimeError('SECRET')),
                            provider=provider, transport=transport)
    provider.analyze.assert_not_called()
    transport.send.assert_not_called()


def test_pipeline_empty_does_not_send(store, app, email_config):
    provider, transport = Mock(), Mock()
    assert run_pipeline(store, Settings(apps=[app], email=email_config), fetch=Mock(return_value=[]),
                        provider=provider, transport=transport)
    provider.analyze.assert_not_called()
    transport.send.assert_not_called()


def test_workflow_lock_recovery_and_force(store, app):
    workflows = WorkflowRepository(store)
    assert workflows.reserve(app) == 'reserved'
    assert workflows.reserve(app, force=True) == 'busy'
    workflows.recover(app.package_name)
    assert workflows.reserve(app) == 'reserved'
    workflows.finish(app.package_name, True)
    assert workflows.reserve(app) == 'not_due'
    assert workflows.reserve(app, True) == 'reserved'
    workflows.finish(app.package_name, False)
    assert workflows.reserve(app) == 'reserved'


def test_phase2_schema_migrates_additively(tmp_path):
    import sqlite3
    path = tmp_path/'phase2.db'
    repo = Repository(path)
    repo.close()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE analysis_reviews')
        db.execute('CREATE TABLE analysis_reviews (analysis_id INTEGER, review_id INTEGER, content_hash TEXT, PRIMARY KEY(analysis_id, review_id))')
        db.execute("INSERT INTO analysis_reviews VALUES (99,99,'preserved')")
    repo = Repository(path)
    assert 'review_date' in {c['name'] for c in inspect(repo.engine).get_columns('analysis_reviews')}
    with Session(repo.engine) as session:
        row = session.scalar(select(AnalysisReview))
        assert row.content_hash == 'preserved' and row.review_date is None
    repo.close()
    Repository(path).close()


def test_new_cli_report_preview_status_and_validation(store, app, email_config, tmp_path, capsys):
    rid = report(store, app)
    config = tmp_path/'config.json'
    settings = Settings(db_path=tmp_path/'reviews.db', apps=[app], email=email_config, report_dir=tmp_path/'reports')
    config.write_text(settings.model_dump_json())
    assert main(['report', '--config', str(config), '--report-id', str(rid)]) == 0
    assert main(['email-preview', '--config', str(config), '--report-id', str(rid)]) == 0
    assert main(['send-email', '--config', str(config)]) == 1
    assert main(['report', '--config', str(config), '--package', 'missing']) == 1
    deliveries = DeliveryRepository(store)
    deliveries.reserve(rid, app.recipient_email, email_config.from_address, 3)
    assert main(['email-status', '--config', str(config), '--delivery-id', '1']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'sending'
    assert main(['email-resolve', '--config', str(config), '--delivery-id', '1', '--outcome', 'sent']) == 0
    assert deliveries.get(1)['status'] == 'sent'


def test_report_larger_than_one_hundred_sources(store, app):
    store.sync(app, [raw(str(i)) for i in range(101)])
    stats = analyze_app(AnalysisRepository(store), Mock(analyze=Mock(return_value=response())), app, AnalysisConfig())
    assert stats.completed == 3
    reports = ReportRepository(store)
    document = reports.get(reports.create(app))
    assert document.reviews_analyzed == 101 and document.issues[0].review_count == 101


def test_report_uses_snapshot_dates_after_review_changes(store, app):
    analyze(store, app)
    store.sync(app, [raw(content='Changement', at='2026-02-01')])
    reports = ReportRepository(store)
    document = reports.get(reports.create(app))
    assert document.review_period_start == '2026-01-02T00:00:00+00:00'


def test_pipeline_model_budget_is_global_and_backlog_resumes(store, app, tmp_path):
    other = app.model_copy(update={'package_name': 'other.app'})
    settings = Settings(apps=[app, other], report_dir=tmp_path/'reports',
                        analysis=AnalysisConfig(max_reviews_per_batch=1, max_batches_per_run=1))
    fetch = Mock(return_value=[raw()])
    provider = Mock(analyze=Mock(return_value=response()))
    assert not run_pipeline(store, settings, fetch=fetch, provider=provider)
    assert provider.analyze.call_count == 1
    assert run_pipeline(store, settings, fetch=fetch, provider=provider)
    assert provider.analyze.call_count == 2
    with Session(store.engine) as session:
        assert all(row.status == 'completed' for row in session.scalars(select(WorkflowState)))


def test_pipeline_email_budget_is_global_and_resume_is_not_duplicate(store, app, email_config, tmp_path):
    other = app.model_copy(update={'package_name': 'other.app'})
    settings = Settings(apps=[app, other], report_dir=tmp_path/'reports',
                        email=email_config.model_copy(update={'max_deliveries_per_run': 1}))
    provider = Mock(analyze=Mock(return_value=response()))
    transport = Mock()
    fetch = Mock(return_value=[raw()])
    assert not run_pipeline(store, settings, fetch=fetch, provider=provider, transport=transport)
    assert transport.send.call_count == 1
    assert run_pipeline(store, settings, fetch=fetch, provider=provider, transport=transport)
    assert transport.send.call_count == 2 and provider.analyze.call_count == 2


def test_pipeline_unknown_email_is_not_retried(store, app, email_config, tmp_path):
    settings = Settings(apps=[app], email=email_config, report_dir=tmp_path/'reports')
    provider = Mock(analyze=Mock(return_value=response()))
    transport = Mock(send=Mock(side_effect=DeliveryError(True, 'unknown')))
    fetch = Mock(return_value=[raw()])
    assert not run_pipeline(store, settings, fetch=fetch, provider=provider, transport=transport)
    assert not run_pipeline(store, settings, fetch=fetch, provider=provider, transport=transport)
    assert transport.send.call_count == 1


def test_pipeline_cli_runs_reports_with_email_disabled(store, app, tmp_path, monkeypatch):
    config = tmp_path/'config.json'
    config.write_text(Settings(db_path=tmp_path/'reviews.db', report_dir=tmp_path/'reports', apps=[app]).model_dump_json())
    monkeypatch.setattr('playstoregetreviews.main.fetch_reviews', Mock(return_value=[raw()]))
    provider = Mock(analyze=Mock(return_value=response()))
    monkeypatch.setattr('playstoregetreviews.workflow.OpenAIProvider', Mock(return_value=provider))
    assert main(['run', '--config', str(config)]) == 0
    assert (tmp_path/'reports'/'report-1.html').exists()
    assert main(['run', '--config', str(config), '--force']) == 0
    assert provider.analyze.call_count == 1


def test_cron_wrapper_requires_absolute_paths(tmp_path):
    import os
    import subprocess
    script = Path(__file__).resolve().parents[1]/'deploy'/'run-scheduled.sh'
    environment = dict(os.environ, PLAYSTORE_CONFIG='relative.json', PLAYSTORE_BIN='/bin/true')
    result = subprocess.run(['sh', str(script)], env=environment, capture_output=True)
    assert result.returncode == 2
    environment['PLAYSTORE_CONFIG'] = str(tmp_path/'private.json')
    result = subprocess.run(['sh', str(script)], env=environment, capture_output=True)
    assert result.returncode == 0


def test_exhausted_failure_does_not_starve_other_reports(store, app, email_config, tmp_path):
    first = report(store, app)
    deliveries, reports = DeliveryRepository(store), ReportRepository(store)
    rejected = Mock(send=Mock(side_effect=DeliveryError(False, 'rejected')))
    config = email_config.model_copy(update={'max_attempts': 1, 'max_deliveries_per_run': 1})
    assert send_report(reports, deliveries, first, app, config, rejected) == 'failed'
    analyze(store, app, rows=[raw(content='Autre problème')])
    second = reports.create(app)
    transport = Mock()
    settings = Settings(apps=[app], email=config, report_dir=tmp_path/'reports')
    assert not run_pipeline(store, settings, fetch=Mock(return_value=[raw(content='Autre problème')]),
                            provider=Mock(), transport=transport)
    assert transport.send.call_count == 1
    assert deliveries.status(second, app.recipient_email) == 'sent'


def test_pipeline_busy_reports_incomplete_without_external_calls(store, app):
    WorkflowRepository(store).reserve(app)
    fetch, provider = Mock(), Mock()
    assert not run_pipeline(store, Settings(apps=[app]), fetch=fetch, provider=provider)
    fetch.assert_not_called()
    provider.analyze.assert_not_called()

"""Name discovery, guided setup, and Resend contracts without external services."""
from email.message import EmailMessage
from unittest.mock import Mock
from pathlib import Path
import json
import os
import socket
import base64
import pytest
import requests
from dotenv import dotenv_values
from playstoregetreviews.collector.discovery import StoreApp, discover
from playstoregetreviews.config import AppConfig, EmailConfig, Settings, load_settings
from playstoregetreviews.notifications.resend import ResendTransport
from playstoregetreviews.notifications.email import DeliveryError
from playstoregetreviews.quickstart import choose_app, secret, start
from playstoregetreviews.main import main


@pytest.fixture
def env(monkeypatch):
    for key in ('PLAYSTORE_LLM_API_KEY', 'OPENAI_API_KEY', 'RESEND_API_KEY', 'PLAYSTORE_CONFIG'):
        monkeypatch.setenv(key, '')


def test_discovery_search_deduplicates_and_restores_timeout(monkeypatch):
    previous = socket.getdefaulttimeout()
    search = Mock(return_value=[dict(appId='org.example.app', title='Example', developer='Publisher'),
                               dict(appId='org.example.app', title='Duplicate'),
                               dict(appId='not a package')])
    monkeypatch.setattr('playstoregetreviews.collector.google_play.search_apps', search)
    result = discover('Example')
    assert len(result) == 1 and result[0].package_name == 'org.example.app'
    search.assert_called_once_with('Example', n_hits=10, lang='fr', country='cd')
    assert socket.getdefaulttimeout() == previous


def test_discovery_exact_url_uses_package_and_details(monkeypatch):
    fetch = Mock(return_value=dict(title='Example', developer='Publisher', summary='A summary'))
    monkeypatch.setattr('playstoregetreviews.collector.google_play.app_details', fetch)
    result = discover('https://play.google.com/store/apps/details?id=org.example.app')
    assert result[0].description == 'A summary'
    fetch.assert_called_once_with('org.example.app', lang='fr', country='cd')


@pytest.mark.parametrize('query', ['', 'https://evil.invalid/store/apps/details?id=org.example.app',
                                  'https://play.google.com/store/apps/details?id=invalid'])
def test_discovery_rejects_invalid_inputs(query):
    with pytest.raises(ValueError):
        discover(query)


def test_discovery_does_not_leak_remote_errors(monkeypatch):
    monkeypatch.setattr('playstoregetreviews.collector.google_play.search_apps', Mock(side_effect=ValueError('SECRET')))
    with pytest.raises(ValueError) as error:
        discover('Example')
    assert 'SECRET' not in str(error.value)


def test_ambiguous_names_require_selection(monkeypatch, capsys):
    one, two = StoreApp('org.first.app', 'Same name', 'First'), StoreApp('org.second.app', 'Same name', 'Second')
    answers = iter(['invalid', '0', '2'])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    assert choose_app([one, two]) == two
    assert 'First' in capsys.readouterr().out
    assert choose_app([one]) == one
    with pytest.raises(ValueError):
        choose_app([])


def test_secret_stored_without_printing_and_existing_key_preserved(tmp_path, monkeypatch, env, capsys):
    path = tmp_path/'.env'
    path.write_text('OTHER_SETTING=preserved\n')
    getpass = Mock(return_value="synthetic'quoted-key")
    monkeypatch.setattr('playstoregetreviews.quickstart.getpass', getpass)
    secret('RESEND_API_KEY', 'API key', path)
    assert dotenv_values(path)['RESEND_API_KEY'] == "synthetic'quoted-key"
    assert dotenv_values(path)['OTHER_SETTING'] == 'preserved'
    assert 'synthetic' not in capsys.readouterr().out
    secret('RESEND_API_KEY', 'API key', path)
    assert getpass.call_count == 1


def test_env_loading_does_not_override_injected_credentials(tmp_path, monkeypatch, env):
    path = tmp_path/'config.local.json'
    path.write_text('{}')
    (tmp_path/'.env').write_text('RESEND_API_KEY=from-file\n')
    monkeypatch.setenv('RESEND_API_KEY', 'from-environment')
    load_settings(str(path))
    assert os.environ['RESEND_API_KEY'] == 'from-environment'


def test_first_start_resolves_app_and_saves_private_configuration(tmp_path, monkeypatch, env):
    chosen = StoreApp('org.example.app', 'Example', 'Publisher', 'Short description')
    monkeypatch.setattr('playstoregetreviews.quickstart.discover', Mock(return_value=[chosen]))
    answers = iter(['o', 'tester@example.com', ''])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    keys = iter(['synthetic-openai', 'synthetic-resend'])
    monkeypatch.setattr('playstoregetreviews.quickstart.getpass', lambda _: next(keys))
    run = Mock(return_value=True)
    monkeypatch.setattr('playstoregetreviews.quickstart.run_pipeline', run)
    config = tmp_path/'config.local.json'
    assert start('Example', str(config)) == 0
    saved = json.loads(config.read_text())
    assert len(saved['apps']) == 1
    assert saved['apps'][0]['package_name'] == chosen.package_name
    assert saved['apps'][0]['recipient_authorized'] is True
    assert saved['email']['provider'] == 'resend'
    assert saved['email']['host'] is None and saved['email']['enabled'] is True
    assert 'synthetic-openai' not in config.read_text()
    assert 'synthetic-resend' not in config.read_text()
    assert dotenv_values(tmp_path/'.env')['RESEND_API_KEY'] == 'synthetic-resend'
    assert run.call_args.kwargs['force'] is True
    monkeypatch.setattr('builtins.input', Mock(side_effect=AssertionError('Unexpected prompt')))
    monkeypatch.setattr('playstoregetreviews.quickstart.getpass', Mock(side_effect=AssertionError('Unexpected secret prompt')))
    assert start('Example', str(config)) == 0


def test_start_without_email_never_enables_sending(tmp_path, monkeypatch, env):
    monkeypatch.setenv('PLAYSTORE_LLM_API_KEY', 'synthetic-key')
    selected = StoreApp('org.example.app', 'Example', 'Publisher')
    monkeypatch.setattr('playstoregetreviews.quickstart.discover', Mock(return_value=[selected]))
    monkeypatch.setattr('builtins.input', lambda _: 'n')
    run = Mock(return_value=True)
    monkeypatch.setattr('playstoregetreviews.quickstart.run_pipeline', run)
    config = tmp_path/'config.local.json'
    assert start('Example', str(config)) == 0
    assert not run.call_args.args[1].email.enabled
    assert not run.call_args.args[1].apps[0].recipient_authorized


def test_start_preserves_other_applications_and_reports_failure(tmp_path, monkeypatch, env):
    monkeypatch.setenv('PLAYSTORE_LLM_API_KEY', 'synthetic-key')
    config = tmp_path/'config.local.json'
    original = Settings(db_path=tmp_path/'reviews.db', apps=[AppConfig(package_name='org.previous.app')])
    config.write_text(original.model_dump_json())
    selected = StoreApp('org.new.app', 'New', 'Publisher')
    monkeypatch.setattr('playstoregetreviews.quickstart.discover', Mock(return_value=[selected]))
    monkeypatch.setattr('builtins.input', lambda _: 'n')
    monkeypatch.setattr('playstoregetreviews.quickstart.run_pipeline', Mock(return_value=False))
    assert start('New', str(config)) == 1
    assert len(load_settings(str(config)).apps) == 2


def message():
    mail = EmailMessage()
    mail['From'] = 'reports@example.com'
    mail['To'] = 'tester@example.com'
    mail['Subject'] = 'Synthetic report'
    mail['Message-ID'] = '<stable@example.com>'
    mail.set_content('Plain summary')
    mail.add_alternative('<p>HTML summary</p>', subtype='html')
    mail.add_attachment(b'{"synthetic":true}', maintype='application', subtype='json', filename='report.json')
    return mail


def test_resend_payload_idempotency_and_no_redirect(monkeypatch, env):
    monkeypatch.setenv('RESEND_API_KEY', 'synthetic-key')
    client = Mock()
    client.post.return_value = Mock(status_code=200, json=Mock(return_value={'id': 'synthetic-id'}))
    transport = ResendTransport(EmailConfig(provider='resend', enabled=True, from_address='reports@example.com'), client)
    transport.send(message())
    transport.send(message())
    args, kwargs = client.post.call_args
    assert args[0] == 'https://api.resend.com/emails'
    assert kwargs['allow_redirects'] is False
    assert kwargs['json']['to'] == ['tester@example.com']
    assert kwargs['json']['text'].strip() == 'Plain summary'
    assert '<p>HTML summary</p>' in kwargs['json']['html']
    attachment = kwargs['json']['attachments'][0]
    assert base64.b64decode(attachment['content']) == b'{"synthetic":true}'
    assert kwargs['headers']['Idempotency-Key'] == client.post.call_args_list[0].kwargs['headers']['Idempotency-Key']


@pytest.mark.parametrize('status,uncertain', [(400, False), (401, False), (403, False),
                                           (429, False), (409, True), (500, True)])
def test_resend_error_classification(monkeypatch, env, status, uncertain):
    monkeypatch.setenv('RESEND_API_KEY', 'synthetic-key')
    client = Mock(post=Mock(return_value=Mock(status_code=status)))
    transport = ResendTransport(EmailConfig(provider='resend', enabled=True, from_address='reports@example.com'), client)
    with pytest.raises(DeliveryError) as caught:
        transport.send(message())
    assert caught.value.uncertain == uncertain


def test_resend_timeout_and_invalid_success_are_uncertain(monkeypatch, env):
    monkeypatch.setenv('RESEND_API_KEY', 'synthetic-key')
    config = EmailConfig(provider='resend', enabled=True, from_address='reports@example.com')
    client = Mock(post=Mock(side_effect=requests.Timeout('SECRET')))
    with pytest.raises(DeliveryError) as error:
        ResendTransport(config, client).send(message())
    assert error.value.uncertain and 'SECRET' not in str(error.value)
    client.post.side_effect = None
    client.post.return_value = Mock(status_code=200, json=Mock(return_value={}))
    with pytest.raises(DeliveryError) as error:
        ResendTransport(config, client).send(message())
    assert error.value.uncertain


def test_resend_missing_key_fails_before_network(env):
    client = Mock()
    config = EmailConfig(provider='resend', enabled=True, from_address='reports@example.com')
    with pytest.raises(ValueError):
        ResendTransport(config, client).send(message())
    client.post.assert_not_called()


def test_cli_start_dispatch_and_cancel(monkeypatch):
    run = Mock(return_value=0)
    monkeypatch.setattr('playstoregetreviews.quickstart.start', run)
    assert main(['start', 'Example', '--email']) == 0
    run.assert_called_once_with('Example', None, True)
    run.side_effect = EOFError()
    assert main(['start']) == 130


def test_guided_name_to_report_and_resend_pipeline_end_to_end(tmp_path, monkeypatch, env):
    from playstoregetreviews.workflow import run_pipeline
    from playstoregetreviews.analysis.providers.openai import ProviderResponse
    selected = StoreApp('org.example.app', 'Example', 'Publisher', 'Description')
    monkeypatch.setattr('playstoregetreviews.quickstart.discover', Mock(return_value=[selected]))
    answers = iter(['o', 'tester@example.com', ''])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    keys = iter(['synthetic-openai', 'synthetic-resend'])
    monkeypatch.setattr('playstoregetreviews.quickstart.getpass', lambda _: next(keys))
    response = ProviderResponse(json.dumps(dict(summary='Lenteur signalée', issues=[], limitations=['Test synthétique'])))
    provider = Mock(analyze=Mock(return_value=response))
    fetch = Mock(return_value=[dict(reviewId='synthetic', score=1, content='Application lente', at='2026-01-01')])
    def pipeline(repo, settings, **kwargs):
        return run_pipeline(repo, settings, fetch=fetch, provider=provider, **kwargs)
    monkeypatch.setattr('playstoregetreviews.quickstart.run_pipeline', pipeline)
    post = Mock(return_value=Mock(status_code=200, json=Mock(return_value={'id': 'synthetic-id'})))
    monkeypatch.setattr('playstoregetreviews.notifications.resend.requests.post', post)
    config = tmp_path/'config.local.json'
    assert start('Example', str(config)) == 0
    assert (tmp_path/'reports'/'report-1.html').exists()
    assert post.call_count == 1 and provider.analyze.call_count == 1
    monkeypatch.delenv('PLAYSTORE_LLM_API_KEY')
    monkeypatch.delenv('RESEND_API_KEY')
    monkeypatch.setattr('builtins.input', Mock(side_effect=AssertionError('Unexpected prompt')))
    monkeypatch.setattr('playstoregetreviews.quickstart.getpass', Mock(side_effect=AssertionError('Unexpected key prompt')))
    assert start('Example', str(config)) == 0
    assert post.call_count == 1 and provider.analyze.call_count == 1

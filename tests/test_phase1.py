import csv
import json
import sqlite3
from datetime import datetime
from unittest.mock import Mock
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from playstoregetreviews.config import AppConfig, Settings, load_settings
from playstoregetreviews.collector.google_play import fetch_reviews
from playstoregetreviews.database.models import Review
from playstoregetreviews.database.repository import Repository, migrate_legacy
from playstoregetreviews.main import export_reviews, main, notify


def review(**changes):
    return dict(dict(reviewId='r1', score=1, content='Lent', at=datetime(2026, 1, 1)), **changes)


def test_lifecycle_and_application_isolation(tmp_path):
    repo = Repository(tmp_path / 'r.db')
    app = AppConfig()
    assert repo.sync(app, [review(), review()]) == dict(new=1, unchanged=1, modified=0, ignored=0)
    with Session(repo.engine) as session, session.begin():
        row = session.scalar(select(Review))
        first = row.first_seen_at
        row.analysis_status = 'analyzed'
    repo.sync(app, [review()])
    with Session(repo.engine) as session:
        assert session.scalar(select(Review)).analysis_status == 'analyzed'
    assert repo.sync(app, [review(score=5, content='Résolu')])['modified'] == 1
    with Session(repo.engine) as session:
        row = session.scalar(select(Review))
        assert row.analysis_status == 'pending'
        assert row.score == 5
        assert row.first_seen_at == first
        assert row.last_seen_at >= first
    assert repo.sync(AppConfig(package_name='other.app'), [review()])['new'] == 1
    assert repo.sync(app, [review(reviewId='positive', score=4)])['ignored'] == 1
    repo.close()


@pytest.mark.parametrize('change', [dict(content='Nouveau'), dict(score=2), dict(at=datetime(2026, 2, 1))])
def test_each_change_detected(tmp_path, change):
    repo = Repository(tmp_path / 'r.db')
    repo.sync(AppConfig(), [review()])
    assert repo.sync(AppConfig(), [review(**change)])['modified'] == 1
    repo.close()


@pytest.mark.parametrize('change', [dict(reviewId=''), dict(score=0), dict(score=True), dict(content=42)])
def test_invalid_rolls_back(tmp_path, change):
    repo = Repository(tmp_path / 'r.db')
    with pytest.raises(ValueError):
        repo.sync(AppConfig(), [review(), review(**change)])
    assert repo.export_rows() == []
    repo.close()


def test_pagination_limit_and_options():
    fetch = Mock(side_effect=[([review()] * 200, 'token'), ([review()] * 50, None)])
    sleep = Mock()
    result = fetch_reviews(AppConfig(max_fetch=210), Settings(), fetch, sleep)
    assert len(result) == 210
    assert fetch.call_args.kwargs['count'] == 10
    assert fetch.call_args.kwargs['continuation_token'] == 'token'
    assert fetch.call_args.kwargs['country'] == 'cd'
    sleep.assert_called_once_with(0.6)


def test_retry_and_failure():
    fetch = Mock(side_effect=[OSError('private'), ([review()], None)])
    assert len(fetch_reviews(AppConfig(), Settings(), fetch, Mock())) == 1
    fetch = Mock(side_effect=OSError('private'))
    with pytest.raises(RuntimeError, match='Échec'):
        fetch_reviews(AppConfig(), Settings(retries=1), fetch, Mock())
    assert fetch.call_count == 2


def test_empty_and_stuck_pagination():
    assert fetch_reviews(AppConfig(), Settings(), Mock(return_value=([], None)), Mock()) == []
    with pytest.raises(RuntimeError, match='bloquée'):
        fetch_reviews(AppConfig(), Settings(), Mock(return_value=([review()], 'same')), Mock())


def test_export_roundtrip(tmp_path):
    repo = Repository(tmp_path / 'r.db')
    text = 'Échec, "connexion"\nLent'
    repo.sync(AppConfig(), [review(content=text)])
    assert export_reviews(repo, tmp_path / 'exports') == 1
    assert json.loads(next((tmp_path / 'exports').glob('*.json')).read_text()) == [dict(score=1, content=text)]
    with next((tmp_path / 'exports').glob('*.csv')).open(newline='') as stream:
        assert list(csv.reader(stream)) == [['score', 'content'], ['1', text]]
    repo.close()


def test_legacy_migration(tmp_path):
    source, dest = tmp_path / 'old.db', tmp_path / 'new.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE reviews (reviewId TEXT, score INTEGER, content TEXT, at TEXT, inserted_at TEXT)')
        db.execute("INSERT INTO reviews VALUES ('r1', 1, 'test', '2025-01-01', '2025-02-01')")
    original = source.read_bytes()
    with pytest.raises(ValueError, match='historique'):
        Repository(source)
    assert migrate_legacy(source, dest, AppConfig()) == 1
    assert source.read_bytes() == original
    repo = Repository(dest)
    with Session(repo.engine) as session:
        assert session.scalar(select(Review)).first_seen_at == '2025-02-01'
    repo.close()
    with pytest.raises(ValueError):
        migrate_legacy(source, dest, AppConfig())


def test_configuration(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(dict(apps=[dict(package_name='one'), dict(package_name='two')])))
    monkeypatch.setenv('PLAYSTORE_CONFIG', str(path))
    assert len(load_settings().apps) == 2
    with pytest.raises(ValueError):
        Settings(apps=[AppConfig(), AppConfig()])
    with pytest.raises(ValueError):
        AppConfig(max_fetch=0)


def test_cli_failure_is_nonzero_and_private(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(dict(db_path=str(tmp_path / 'r.db'))))
    monkeypatch.setattr('playstoregetreviews.main.fetch_reviews', Mock(side_effect=RuntimeError('SECRET')))
    assert main(['collect', '--config', str(path)]) == 1
    output = capsys.readouterr().err
    assert 'collection_failed' in output
    assert 'SECRET' not in output


def test_webhook_disabled_and_failure_redacted(monkeypatch, caplog):
    import requests
    post = Mock(side_effect=requests.RequestException('SECRET'))
    monkeypatch.setattr('playstoregetreviews.main.requests.post', post)
    monkeypatch.delenv('REVIEW_WEBHOOK_URL', raising=False)
    notify(review(), 'app')
    post.assert_not_called()
    monkeypatch.setenv('REVIEW_WEBHOOK_URL', 'https://example.invalid/private')
    notify(review(), 'app')
    assert 'webhook_failed' in caplog.text
    assert 'SECRET' not in caplog.text


def test_scraper_adapter_and_tls(monkeypatch):
    import ssl
    from playstoregetreviews.collector import google_play as module
    assert ssl._create_default_https_context().verify_mode == ssl.CERT_REQUIRED
    assert ssl._create_default_https_context().check_hostname
    underlying = Mock(return_value=([], 'next'))
    monkeypatch.setattr(module._scraper, '_fetch_review_items', underlying)
    page, token = module.fetch_page('app', lang='fr', country='cd', sort=module.Sort.NEWEST,
                                   count=10, continuation_token='previous')
    assert page == [] and token == 'next'
    assert underlying.call_args.args[-1] == 'previous'
    underlying.side_effect = OSError('network error')
    with pytest.raises(OSError):
        module.fetch_page('app', lang='fr', country='cd', sort=module.Sort.NEWEST,
                          count=10, continuation_token=None)


def test_cli_multiple_apps_and_export(tmp_path, monkeypatch):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(db_path=str(tmp_path/'r.db'), export_dir=str(tmp_path/'exports'),
                                     apps=[dict(package_name='one'), dict(package_name='two')])) )
    fetch = Mock(side_effect=[RuntimeError(), [review()]])
    monkeypatch.setattr('playstoregetreviews.main.fetch_reviews', fetch)
    assert main(['collect', '--config', str(config)]) == 1
    assert fetch.call_count == 2
    assert main(['export', '--config', str(config)]) == 0
    assert json.loads(next((tmp_path/'exports').glob('*.json')).read_text()) == [dict(score=1, content='Lent')]


def test_historical_export_entrypoint(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    repo = Repository(tmp_path / 'r.db')
    repo.sync(AppConfig(), [review()])
    repo.close()
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(dict(db_path=str(tmp_path/'r.db'), export_dir=str(tmp_path/'exports'))))
    result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/'export_reviews.py'),
                             '--config', str(config)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert len(list((tmp_path/'exports').glob('*'))) == 2


def test_invalid_migration_does_not_create_destination(tmp_path):
    source, dest = tmp_path/'old.db', tmp_path/'new.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE reviews (reviewId TEXT, score INTEGER, content TEXT, at TEXT, inserted_at TEXT)')
        db.execute("INSERT INTO reviews VALUES ('bad', 0, 'test', '', '')")
    with pytest.raises(ValueError):
        migrate_legacy(source, dest, AppConfig())
    assert not dest.exists()

"""CLI de collecte, migration, export et analyse IA."""
import argparse
import csv
import json
import logging
import os
from pathlib import Path
import socket
from datetime import datetime, timezone
import requests
import sqlite3
from sqlalchemy.exc import SQLAlchemyError
from .config import load_settings
from .collector.google_play import fetch_reviews
from .database.repository import Repository, migrate_legacy


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({"time": datetime.now(timezone.utc).isoformat(), "level": record.levelname,
                           "event": record.getMessage(), **{k: getattr(record, k) for k in
                           ("package", "attempt", "counts", "analysis_id", "report_id", "delivery_id") if hasattr(record, k)}})


def export_reviews(repository: Repository, directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    stem = 'reviews_' + datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S_%f')
    rows = repository.export_rows()
    with (directory / (stem + '.csv')).open('x', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['score', 'content'])
        writer.writerows(rows)
    with (directory / (stem + '.json')).open('x', encoding='utf-8') as stream:
        json.dump([dict(score=s, content=c) for s, c in rows], stream, ensure_ascii=False, indent=2)
    return len(rows)


def notify(review: dict, package: str) -> None:
    url = os.getenv('REVIEW_WEBHOOK_URL')
    if url:
        try:
            response = requests.post(url, json={'text': f"Nouvel avis négatif ({review['score']}★) pour {package}\n{review.get('content', '')}"}, timeout=8)
            response.raise_for_status()
        except requests.RequestException:
            logging.getLogger(__name__).warning('webhook_failed')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['collect', 'export', 'migrate', 'analyze', 'analysis-show', 'analysis-recover', 'report', 'email-preview', 'send-email', 'email-status', 'email-resolve', 'run', 'run-recover'])
    parser.add_argument('--config')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--analysis-id', type=int)
    parser.add_argument('--report-id', type=int)
    parser.add_argument('--delivery-id', type=int)
    parser.add_argument('--outcome', choices=['sent', 'retry'])
    parser.add_argument('--force', action='store_true', help='Ignorer la cadence du pipeline, sans contourner les réservations')
    parser.add_argument('--package', help='Application à analyser ou propriétaire de la base historique')
    args = parser.parse_args(argv)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logger = logging.getLogger(__name__)
    repository = None
    previous_timeout = socket.getdefaulttimeout()
    try:
        settings = load_settings(args.config)
        if args.command == 'migrate':
            if not args.source or not args.package:
                parser.error('migrate exige --source et --package')
            app = next((a for a in settings.apps if a.package_name == args.package), None)
            if app is None:
                raise ValueError('Application absente de la configuration')
            migrate_legacy(args.source, settings.db_path, app)
            logger.info('migration_completed')
            return 0
        repository = Repository(settings.db_path)
        if args.command == 'run':
            from .workflow import run_pipeline
            return int(not run_pipeline(repository, settings, args.package, args.force,
                                        fetch=fetch_reviews, notify=notify))
        if args.command in {'report', 'email-preview', 'send-email', 'email-status', 'email-resolve', 'run-recover'}:
            from .commands import execute
            return execute(args.command, repository, settings, package=args.package,
                           report_id=args.report_id, delivery_id=args.delivery_id, outcome=args.outcome)
        if args.command in {'analyze', 'analysis-show', 'analysis-recover'}:
            from .analysis.repository import AnalysisRepository, AnalysisBusy
            from .analysis.providers.openai import OpenAIProvider
            from .analysis.service import analyze_app
            from dataclasses import asdict
            analyses = AnalysisRepository(repository)
            if args.command != 'analyze':
                if args.analysis_id is None:
                    parser.error('--analysis-id requis')
                if args.command == 'analysis-show':
                    print(json.dumps(analyses.result(args.analysis_id), ensure_ascii=False, indent=2))
                else:
                    analyses.recover(args.analysis_id)
                    logger.info('analysis_recovered')
                return 0
            provider = OpenAIProvider(settings.analysis)
            remaining = settings.analysis.max_batches_per_run
            failed = False
            apps = [app for app in settings.apps if not args.package or app.package_name == args.package]
            if not apps:
                raise ValueError('Application absente de la configuration')
            try:
                for app in apps:
                    if remaining == 0:
                        break
                    try:
                        stats = analyze_app(analyses, provider, app,
                                            settings.analysis.model_copy(update={'max_batches_per_run': remaining}))
                        remaining -= stats.calls
                        failed = failed or bool(stats.failed)
                        logger.info('analysis_run_completed', extra={'package': app.package_name, 'counts': asdict(stats)})
                    except AnalysisBusy:
                        failed = True
                        logger.error('analysis_busy', extra={'package': app.package_name})
            finally:
                provider.close()
            return int(failed)
        if args.command == 'export':
            export_reviews(repository, settings.export_dir)
            logger.info('export_completed')
        else:
            # google-play-scraper utilise urllib ; borner ses connexions pour cette CLI.
            socket.setdefaulttimeout(settings.timeout_seconds)
            failed = False
            for app in settings.apps:
                try:
                    fetched = fetch_reviews(app, settings)
                    counts = dict(new=0, modified=0, unchanged=0, ignored=0)
                    for review in fetched:
                        result = repository.sync(app, [review])
                        for key, value in result.items():
                            counts[key] += value
                        if result['new']:
                            notify(review, app.package_name)
                    logger.info('collection_completed', extra={'package': app.package_name, 'counts': counts})
                except (RuntimeError, ValueError):
                    failed = True
                    logger.error('collection_failed', extra={'package': app.package_name})
            return int(failed)
    except (ValueError, OSError, SQLAlchemyError, sqlite3.Error):
        logger.error('configuration_or_storage_failed')
        return 1
    finally:
        socket.setdefaulttimeout(previous_timeout)
        if repository:
            repository.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
